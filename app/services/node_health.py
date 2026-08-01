"""Flussonic node health — shared by /admin/nodes/health and the background
stream-health monitor (M2). Reports EVERY configured node (ec-main, co-main,
ec-quito), so a down secondary node like co-main is visible, not just the primary.

Two probe strategies, selected by NODE_PROBE_MODE (P0.5):

  origin      (default, legacy) — ask the node's management API directly. Honest
                only when the backend can route to the Flussonic origin; in the
                current production topology it cannot (only Nginx can), so every
                node reads "down" regardless of reality.

  hls_signed  — mint a playback token and ask the EDGE for a signed HLS manifest,
                exactly as a player would. A 2xx means the gate accepted the
                token, Nginx reached the node, and the node served the stream —
                one end-to-end signal instead of a probe of a path nothing uses.
"""
import logging
import time
from urllib.parse import urlparse

import httpx
from sqlalchemy import select

from app.config import get_settings
from app.database import AsyncSessionLocal
from app.integrations import flussonic_registry as registry
from app.integrations.flussonic_client import get_flussonic_node_client
from app.models.channel import Channel
from app.redis_client import get_redis
from app.services.stream_auth_service import StreamAuthService

logger = logging.getLogger(__name__)

MODE_ORIGIN = "origin"
MODE_HLS_SIGNED = "hls_signed"

_DEFAULT_HLS_PATH = "index.m3u8"

# The node table used to live here as a second copy of the one in
# flussonic_client (and of the operator's memory of the FLUSSONIC_* variables).
# It is now read from app/integrations/flussonic_registry.py, the single
# declaration of node_id / base_url / region / priority (P2.2).


def configured_node_ids() -> list[str]:
    return registry.configured_node_ids()


def _base_result(node_id: str, probe_mode: str) -> dict:
    node = registry.describe_node(node_id)
    base = node.base_url if node else ""
    return {
        "node_id": node_id,
        "host": urlparse(base).netloc if base else "",
        "region": node.region if node else None,
        "configured": False,
        "reachable": False,
        "latency_ms": None,
        "stream_count": None,
        "probe_mode": probe_mode,
        "detail": None,
    }


# ── origin probe (legacy) ─────────────────────────────────────────────────────

async def check_node_origin(node_id: str) -> dict:
    """Management-API probe: GET <mgmt_base_url>/flussonic/api/v3/streams."""
    out = _base_result(node_id, MODE_ORIGIN)
    client = get_flussonic_node_client(node_id)
    out["configured"] = bool(client and client.is_configured)
    if out["configured"]:
        t0 = time.monotonic()
        out["reachable"] = await client.check_connectivity()
        out["latency_ms"] = round((time.monotonic() - t0) * 1000, 2)
        if out["reachable"]:
            try:
                out["stream_count"] = len(await client.list_streams())
            except Exception:
                pass
        else:
            out["detail"] = f"management API unreachable at host={out['host']}"
    else:
        out["detail"] = "node not configured"
    return out


# ── signed-HLS probe (P0.5) ───────────────────────────────────────────────────

async def _get(url: str, params: dict, timeout: float) -> httpx.Response:
    """Single HTTP seam for the probe — kept apart so tests can replace it
    without patching httpx globally. Redirects are NOT followed: the contract is
    a 2xx manifest, and a redirect off the edge is not the signal we want."""
    async with httpx.AsyncClient(timeout=timeout) as client:
        return await client.get(url, params=params)


async def _probe_target(db, node_id: str) -> tuple[str, str] | None:
    """(stream_key, hls_path) to probe for `node_id`.

    NODE_PROBE_STREAMS pins an explicit stream; otherwise the lowest-numbered
    active channel of that node in the catalog is used, so the probe follows the
    catalog instead of a second list that can silently rot.
    """
    pinned = get_settings().node_probe_stream_map.get(node_id)
    if pinned:
        return pinned, _DEFAULT_HLS_PATH
    row = (
        await db.execute(
            select(Channel.stream_key, Channel.hls_path)
            .where(Channel.flussonic_node == node_id, Channel.is_active.is_(True))
            .order_by(Channel.number)
            .limit(1)
        )
    ).first()
    if row is None:
        return None
    return row[0], row[1] or _DEFAULT_HLS_PATH


async def check_node_hls_signed(node_id: str, db, redis) -> dict:
    """Probe a node the way a player reaches it: signed HLS manifest via the edge.

    reachable=True only on a 2xx. A 401/403 means the gate rejected the token (a
    real failure of the playback path, not just of the node), a 404/5xx means the
    edge could not serve the stream, and a transport error means the edge itself
    is unreachable — each is reported in `detail` so the alert names the cause.
    """
    s = get_settings()
    out = _base_result(node_id, MODE_HLS_SIGNED)
    out["configured"] = bool(out["host"])
    if not out["configured"]:
        out["detail"] = "node not configured"
        return out

    target = await _probe_target(db, node_id)
    if target is None:
        out["detail"] = (
            "no probe stream for this node — set NODE_PROBE_STREAMS or activate "
            "a channel on it"
        )
        return out
    stream_key, hls_path = target

    svc = StreamAuthService(db, redis)
    token, jti = await svc.mint_probe_token(node_id, stream_key)
    url = f"{s.node_probe_edge_base_url.rstrip('/')}/stream/{node_id}/{stream_key}/{hls_path}"
    t0 = time.monotonic()
    try:
        resp = await _get(url, {"token": token}, s.node_probe_timeout_seconds)
        out["latency_ms"] = round((time.monotonic() - t0) * 1000, 2)
        out["reachable"] = resp.is_success
        if not resp.is_success:
            out["detail"] = f"signed HLS probe returned HTTP {resp.status_code} for {stream_key}"
    except httpx.HTTPError as exc:
        out["latency_ms"] = round((time.monotonic() - t0) * 1000, 2)
        out["detail"] = f"signed HLS probe failed for {stream_key}: {type(exc).__name__}"
    finally:
        await svc.release_probe_token(jti)
    return out


# ── dispatch ──────────────────────────────────────────────────────────────────

def probe_mode() -> str:
    mode = get_settings().node_probe_mode.strip().lower()
    if mode not in (MODE_ORIGIN, MODE_HLS_SIGNED):
        logger.warning("Unknown NODE_PROBE_MODE %r — falling back to %r", mode, MODE_ORIGIN)
        return MODE_ORIGIN
    return mode


async def check_node(node_id: str) -> dict:
    if probe_mode() == MODE_HLS_SIGNED:
        redis = await get_redis()
        async with AsyncSessionLocal() as db:
            return await check_node_hls_signed(node_id, db, redis)
    return await check_node_origin(node_id)


async def check_all_nodes() -> list[dict]:
    return [await check_node(n) for n in configured_node_ids()]
