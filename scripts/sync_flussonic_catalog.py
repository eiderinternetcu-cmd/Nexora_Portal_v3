"""Sync the Nexora channel catalog with the streams that are ONLINE in Flussonic.

WHY HLS PROBING (not the API 'alive' flag):
  The Flussonic management API '/flussonic/api/v3/streams' is unreliable for
  liveness: ec-main reports alive=false for streams whose HLS manifest actually
  serves 200, and the co-main read-only user gets 403 on the management API
  altogether. The ground truth for "playable right now" is the HLS manifest:
  GET {origin}/{stream_key}/index.m3u8  ->  200 + "#EXTM3U".

STREAM NAME DISCOVERY:
  - ec-main: management API list_streams() (names only — liveness comes from HLS).
  - co-main: SEED list + stream_keys already in the DB (API returns 403, so the
    node cannot be enumerated; new co-main streams must be seeded here).

BEHAVIOR (idempotent):
  - Online stream already in DB   -> is_active=True (kept/updated, curated
    name/category/number/channel_key preserved).
  - Online stream not in DB        -> inserted as a new active channel.
  - DB channel no longer online    -> is_active=False (deactivated, kept for history).

Flussonic is READ-ONLY. This script never writes to Flussonic.

Run inside the api container:
    docker exec nexora_api python scripts/sync_flussonic_catalog.py            # apply
    docker exec nexora_api python scripts/sync_flussonic_catalog.py --dry-run  # preview only

Designed to run daily via cron.
"""
import asyncio
import os
import re
import sys
from pathlib import Path

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

sys.path.insert(0, str(Path(__file__).parent.parent))

import httpx  # noqa: E402
from sqlalchemy import select  # noqa: E402

from app.database import AsyncSessionLocal  # noqa: E402
from app.models.channel import Channel  # noqa: E402

DRY_RUN = "--dry-run" in sys.argv

# ── Origins used ONLY to probe HLS liveness (read-only GET) ───────────────────
_PROBE_ORIGIN = {
    "ec-main": os.environ.get("FLUSSONIC_EC_MAIN_ORIGIN") or "http://181.78.246.211:8002",
    "co-main": os.environ.get("FLUSSONIC_CO_MAIN_ORIGIN") or "http://38.210.187.13:8002",
}

# ── Public base URLs stored as channel.source_url (HTTPS proxy in production) ──
# Prefer the explicit public var, then the node base URL the player already uses
# (FLUSSONIC_*_BASE_URL -> the HTTPS /stream proxy in production), then the origin.
_PUBLIC_BASE = {
    "ec-main": (
        os.environ.get("FLUSSONIC_PUBLIC_EC_MAIN_BASE_URL")
        or os.environ.get("FLUSSONIC_BASE_URL")
        or _PROBE_ORIGIN["ec-main"]
    ).rstrip("/"),
    "co-main": (
        os.environ.get("FLUSSONIC_PUBLIC_CO_MAIN_BASE_URL")
        or os.environ.get("FLUSSONIC_CO_MAIN_BASE_URL")
        or _PROBE_ORIGIN["co-main"]
    ).rstrip("/"),
}

# ── co-main cannot be enumerated (API 403) — seed its known stream names here ──
SEED_CO_MAIN = ["TeleNostalgia", "Son_Corazon", "Son_Latino", "Son_Popular"]

_HLS_PATH = "index.m3u8"
_PROBE_TIMEOUT = 8.0
_PROBE_CONCURRENCY = 10


# ── Stream name discovery ─────────────────────────────────────────────────────

async def _discover_ec_main_names() -> list[str]:
    """List ec-main stream names via the management API (names only)."""
    from app.integrations.flussonic_client import get_flussonic_node_client
    client = get_flussonic_node_client("ec-main")
    if client is None or not client.is_configured:
        print("  [ec-main] management API not configured — using DB keys only")
        return []
    try:
        raw = await client.list_streams()
        names = [s.get("name", "") for s in raw if s.get("name")]
        print(f"  [ec-main] management API listed {len(names)} stream names")
        return names
    except Exception as exc:  # noqa: BLE001
        print(f"  [ec-main] management API unavailable ({exc}) — using DB keys only")
        return []


# ── HLS liveness probe ────────────────────────────────────────────────────────

async def _probe_online(
    client: httpx.AsyncClient, sem: asyncio.Semaphore, node: str, stream_key: str
) -> tuple[str, str, bool]:
    """Return (node, stream_key, online). online iff HLS manifest is 200 + #EXTM3U."""
    origin = _PROBE_ORIGIN.get(node, _PROBE_ORIGIN["ec-main"])
    url = f"{origin}/{stream_key}/{_HLS_PATH}"
    async with sem:
        try:
            resp = await client.get(url, headers={"Range": "bytes=0-256"})
            online = resp.status_code in (200, 206) and "#EXTM3U" in resp.text[:256]
        except httpx.HTTPError:
            online = False
    return node, stream_key, online


# ── Helpers for new channels ──────────────────────────────────────────────────

def _slug(stream_key: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", stream_key.lower()).strip("-")
    return s[:60] or "stream"


def _pretty(stream_key: str) -> str:
    return re.sub(r"[_-]+", " ", stream_key).strip()[:128]


# ── Main ──────────────────────────────────────────────────────────────────────

async def main() -> None:
    mode = "DRY-RUN (no DB writes)" if DRY_RUN else "APPLY"
    print(f"\n{'='*72}\n  Flussonic catalog sync — {mode}\n{'='*72}")

    print("\nDiscovering stream names...")
    ec_names = await _discover_ec_main_names()

    async with AsyncSessionLocal() as session:
        existing_list = (
            await session.execute(select(Channel))
        ).scalars().all()
        existing = {(c.stream_key, c.flussonic_node): c for c in existing_list}
        existing_keys = {c.channel_key for c in existing_list}
        max_number = max((c.number for c in existing_list), default=0)

        # Build candidate (stream_key, node) set: every DB channel + discovered + seed
        candidates: set[tuple[str, str]] = set(existing.keys())
        candidates.update((n, "ec-main") for n in ec_names)
        candidates.update((k, "co-main") for k in SEED_CO_MAIN)
        print(f"  Candidates to probe: {len(candidates)} "
              f"(DB={len(existing)}, ec-main API={len(ec_names)}, co-main seed={len(SEED_CO_MAIN)})")

        # Probe HLS liveness for all candidates concurrently
        print("\nProbing HLS manifests for liveness...")
        sem = asyncio.Semaphore(_PROBE_CONCURRENCY)
        async with httpx.AsyncClient(timeout=_PROBE_TIMEOUT, follow_redirects=True) as http:
            results = await asyncio.gather(*[
                _probe_online(http, sem, node, key) for key, node in candidates
            ])
        online = {(key, node) for node, key, ok in results if ok}
        print(f"  Online now: {len(online)} / {len(candidates)} probed")

        # Capture report lines BEFORE commit/rollback — afterwards the ORM
        # expires attributes and reading them would trigger an async lazy-load.
        def _fmt(number: int, ckey: str, name: str, node: str, stream_key: str) -> str:
            return f"#{number:<3d} {ckey:<18s} {name:<24s} [{node}] {stream_key}"

        added, reactivated, deactivated, kept_count = [], [], [], 0

        # Online streams -> ensure an active channel exists
        for stream_key, node in sorted(online):
            ch = existing.get((stream_key, node))
            if ch is not None:
                if not ch.is_active:
                    ch.is_active = True
                    reactivated.append(_fmt(ch.number, ch.channel_key, ch.name, node, stream_key))
                else:
                    kept_count += 1
                ch.source_type = "flussonic"
                ch.source_url = f"{_PUBLIC_BASE[node]}/{stream_key}/{_HLS_PATH}"
            else:
                ckey = _slug(stream_key)
                base_ckey, n = ckey, 2
                while ckey in existing_keys:
                    ckey = f"{base_ckey}-{node.split('-')[0]}" if n == 2 else f"{base_ckey}-{n}"
                    n += 1
                existing_keys.add(ckey)
                max_number += 1
                new_ch = Channel(
                    channel_key=ckey,
                    number=max_number,
                    name=_pretty(stream_key),
                    category="general",
                    stream_key=stream_key,
                    flussonic_node=node,
                    hls_path=_HLS_PATH,
                    source_type="flussonic",
                    source_url=f"{_PUBLIC_BASE[node]}/{stream_key}/{_HLS_PATH}",
                    is_active=True,
                    requires_subscription=True,
                )
                if not DRY_RUN:
                    session.add(new_ch)
                added.append(_fmt(new_ch.number, new_ch.channel_key, new_ch.name, node, stream_key))

        # DB channels no longer online -> deactivate
        for (stream_key, node), ch in existing.items():
            if (stream_key, node) not in online and ch.is_active:
                ch.is_active = False
                deactivated.append(_fmt(ch.number, ch.channel_key, ch.name, node, stream_key))

        active_total = (
            len([c for c in existing_list if c.is_active]) + len(added)
        )

        if DRY_RUN:
            await session.rollback()
        else:
            await session.commit()

        # ── Report ──
        print(f"\n{'─'*72}")
        print(f"  ADDED ({len(added)}):")
        for line in added:
            print(f"    + {line}")
        print(f"  REACTIVATED ({len(reactivated)}):")
        for line in reactivated:
            print(f"    ^ {line}")
        print(f"  DEACTIVATED — offline ({len(deactivated)}):")
        for line in deactivated:
            print(f"    - {line}")
        print(f"  KEPT active ({kept_count})")
        print(f"{'─'*72}")
        print(f"  Active channels after sync: ~{active_total}")
        print(f"  Mode: {mode}")
        print(f"{'='*72}\n")


if __name__ == "__main__":
    asyncio.run(main())
