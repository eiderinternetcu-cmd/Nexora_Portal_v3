"""Admin — catalog-wide stream status (P2.1).

GET /api/admin/streams — every channel with `is_active` (DB) separated from
`alive` (Flussonic), so "we turned it off" and "the source died" stop being
the same question.

Why this exists
----------------
GET /api/admin/channels/{id}/stream-status already reports a Flussonic
`alive` flag, but it asks the Flussonic ORIGIN directly
(`FlussonicClient.get_stream_status`) — and the `api` container has NO route
to the Flussonic origins in the current production topology (only Nginx
does; see app/services/node_health.py). That call times out in prod
regardless of whether the stream is actually up, which is exactly the "node
down" false-positive app/services/node_health.py was rewritten to fix for
node-level health. This endpoint reuses the SAME fix — mint a real playback
token and ask the EDGE for the signed HLS manifest, exactly as a player
would — applied per channel instead of once per node, so a single dead
channel on an otherwise healthy node is visible too.

Cardinality / secrecy
----------------------
Only `channel_key` (public, ~40 rows) is used as an identifying label — never
`stream_key` or `source_url` (see app/schemas/channel.py: P0.8 closed that
leak across the whole admin API and this route must not reopen it).

Cost control
------------
Probing is live HTTP through the edge, so it is bounded to active channels
only (an intentionally-disabled channel is not worth an origin round trip)
and runs with bounded concurrency (`_MAX_CONCURRENT_PROBES`) so a page load
costs roughly one probe timeout, not the sum of forty.
"""
import asyncio
import time

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
import httpx
import redis.asyncio as aioredis

from app.config import get_settings
from app.database import get_db
from app.redis_client import get_redis
from app.core.dependencies import require_admin_or_reseller
from app.models.channel import Channel
from app.models.user import User
from app.services.channel_service import ChannelService
from app.services.node_health import configured_node_ids
from app.services.stream_auth_service import StreamAuthService

router = APIRouter(tags=["Admin Streams"])

_MAX_CONCURRENT_PROBES = 8
_PROBE_MODE = "hls_signed"  # the only mode this route uses — see module docstring


class StreamStatusItem(BaseModel):
    channel_key: str
    number: int
    name: str
    flussonic_node: str
    is_active: bool          # DB: did we turn it off
    alive: bool | None       # Flussonic (via signed-edge probe): is the source up
    probe_mode: str = _PROBE_MODE
    latency_ms: float | None = None
    detail: str | None = None  # populated when alive is False, or when not probed


async def _get(url: str, params: dict, timeout: float) -> httpx.Response:
    """HTTP seam for the probe, kept separate so tests can replace it without
    patching httpx globally — same shape as app.services.node_health._get.
    Redirects are NOT followed: a 2xx manifest is the contract."""
    async with httpx.AsyncClient(timeout=timeout) as client:
        return await client.get(url, params=params)


async def _probe_channel(
    channel: Channel, db: AsyncSession, redis: aioredis.Redis
) -> tuple[bool | None, float | None, str | None]:
    """(alive, latency_ms, detail) for ONE channel via the signed-HLS edge probe.

    Mirrors app.services.node_health.check_node_hls_signed, but targets this
    channel's own stream_key instead of a node's representative one, and never
    calls the Flussonic origin — that path is unreachable from this container
    in production (see module docstring)."""
    settings = get_settings()
    if channel.flussonic_node not in configured_node_ids():
        return None, None, f"node '{channel.flussonic_node}' not configured"

    svc = StreamAuthService(db, redis)
    token, jti = await svc.mint_probe_token(channel.flussonic_node, channel.stream_key)
    hls_path = channel.hls_path or "index.m3u8"
    url = (
        f"{settings.node_probe_edge_base_url.rstrip('/')}"
        f"/stream/{channel.flussonic_node}/{channel.stream_key}/{hls_path}"
    )
    t0 = time.monotonic()
    try:
        resp = await _get(url, {"token": token}, settings.node_probe_timeout_seconds)
        latency = round((time.monotonic() - t0) * 1000, 2)
        if resp.is_success:
            return True, latency, None
        return False, latency, f"signed HLS probe returned HTTP {resp.status_code}"
    except httpx.HTTPError as exc:
        latency = round((time.monotonic() - t0) * 1000, 2)
        return False, latency, f"signed HLS probe failed: {type(exc).__name__}"
    finally:
        await svc.release_probe_token(jti)


@router.get("", response_model=list[StreamStatusItem])
async def list_streams(
    db: AsyncSession = Depends(get_db),
    redis: aioredis.Redis = Depends(get_redis),
    _: User = Depends(require_admin_or_reseller),
):
    """Every catalog channel with `is_active` (DB) and `alive` (Flussonic,
    live-probed through the edge) reported separately.

    Only channels with `is_active=True` are probed — an inactive channel's
    source status is not the question an admin is asking, and skipping it
    keeps the request fast. `alive` is `None` when a channel was not probed
    (inactive, or its node is not configured); `detail` explains why.
    """
    channels = await ChannelService(db).list_all()
    to_probe = [c for c in channels if c.is_active]

    semaphore = asyncio.Semaphore(_MAX_CONCURRENT_PROBES)

    async def _bounded(channel: Channel):
        async with semaphore:
            return await _probe_channel(channel, db, redis)

    results = await asyncio.gather(*(_bounded(c) for c in to_probe))
    probed = {c.channel_key: r for c, r in zip(to_probe, results)}

    items = []
    for c in channels:
        if c.channel_key in probed:
            alive, latency_ms, detail = probed[c.channel_key]
        else:
            alive, latency_ms, detail = None, None, "not probed — channel is inactive in catalog"
        items.append(
            StreamStatusItem(
                channel_key=c.channel_key,
                number=c.number,
                name=c.name,
                flussonic_node=c.flussonic_node,
                is_active=c.is_active,
                alive=alive,
                latency_ms=latency_ms,
                detail=detail,
            )
        )
    return items
