"""Flussonic node health — shared by /admin/nodes/health and the background
stream-health monitor (M2). Reports EVERY configured node (ec-main, co-main,
ec-quito), so a down secondary node like co-main is visible, not just the primary.
"""
import time
from urllib.parse import urlparse

from app.config import get_settings
from app.integrations.flussonic_client import get_flussonic_node_client

# node_id -> (region, settings attribute holding its base_url)
_NODES = {
    "ec-main": ("EC", "flussonic_base_url"),
    "co-main": ("CO", "flussonic_co_main_base_url"),
    "ec-quito": ("EC", "flussonic_ec_quito_base_url"),
}


def configured_node_ids() -> list[str]:
    s = get_settings()
    return [n for n, (_, attr) in _NODES.items() if getattr(s, attr, "")]


async def check_node(node_id: str) -> dict:
    s = get_settings()
    region, attr = _NODES.get(node_id, (None, None))
    base = getattr(s, attr, "") if attr else ""
    client = get_flussonic_node_client(node_id)
    configured = bool(client and client.is_configured)
    reachable = False
    latency_ms = None
    stream_count = None
    if configured:
        t0 = time.monotonic()
        reachable = await client.check_connectivity()
        latency_ms = round((time.monotonic() - t0) * 1000, 2)
        if reachable:
            try:
                stream_count = len(await client.list_streams())
            except Exception:
                pass
    return {
        "node_id": node_id,
        "host": urlparse(base).netloc if base else "",
        "region": region,
        "configured": configured,
        "reachable": reachable,
        "latency_ms": latency_ms,
        "stream_count": stream_count,
    }


async def check_all_nodes() -> list[dict]:
    return [await check_node(n) for n in configured_node_ids()]
