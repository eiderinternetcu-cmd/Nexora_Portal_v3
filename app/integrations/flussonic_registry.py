"""Flussonic node registry + node failover (P2.2 / `NX-FLU`).

Until now the nodes lived in three places at once: a hand-written `if/elif`
chain in `flussonic_client.get_flussonic_node_client()`, a `_NODES` dict in
`services/node_health.py`, and the operator's memory of which `FLUSSONIC_*`
variables exist. This module is the single declarative table
(`node_id`, `base_url`, `region`, `priority`, `is_healthy`); the other two read
from it instead of keeping their own copy.

WHAT THIS MODULE DOES NOT KNOW — and cannot
-------------------------------------------
Failover needs to answer "which OTHER node serves this stream?". There is no
honest automatic answer here:

  * `channels.flussonic_node` is a single scalar column — one node per channel.
    Nothing in the schema can express "this stream also lives on node B", and
    the schema is not ours to change in this task.
  * The only authority on what a node serves is the node itself
    (`GET /flussonic/api/v3/streams`, i.e. `FlussonicClient.list_streams`), and
    the `api` container has NO route to any Flussonic origin. That is exactly
    why P0.5 rewrote the health probe to go through the EDGE with a signed HLS
    request instead of asking the origin. Any design that assumes the backend
    can interrogate a node does not work in production.
  * The edge could in principle be asked "does node B serve stream K?" by
    requesting `<edge>/stream/B/K/index.m3u8` with a minted token — but that is
    a network round-trip (plus a minted token) per candidate node inside
    `/authorize`, and a 404 from the edge is ambiguous: missing Nginx
    `location`, node up but stream absent, or node down. It is a monitoring
    signal, not something to run on the request path.

So the mapping is DECLARED, not discovered: `FLUSSONIC_STREAM_MIRRORS` is a CSV
of `stream_key:node[|node…]`, ordered by operator preference. Empty (the
default) means no stream has a declared mirror, so failover can never fire —
which is also why the feature is inert until someone states the facts it needs.

FAILOVER HAPPENS AT AUTHORIZATION, NOT MID-STREAM
-------------------------------------------------
`resolve_playback_node()` is called where the client surface computes the node
for a request, BEFORE the playback token is minted and BEFORE the playback URL
is built — so the same value feeds the token's `node` claim and the
`/stream/<node>/` path of the URL, and the two can never disagree.

Resolving lower down (say inside `get_flussonic_node_client()`) would move only
the URL: the client would be sent to node B holding a token whose `node` claim
still says A. Under `PLAYBACK_NODE_BINDING_ENFORCE` the gate answers 403; with
the flag off it answers 200 — which is precisely the cross-node token reuse
`NX-NODE` just closed. A failover that reuses a token is a security regression,
not a feature.

Hot failover (switching a client that is already playing) would require the
gate to accept a token for a node it was not issued for, i.e. the same
regression. It is also nearly worthless here: playback tokens live 60s and the
player reissues every ~45s, so a node that dies is picked up on the next
reissue anyway. Hot failover buys under a minute and costs the node binding.
"""
import logging
from dataclasses import dataclass

import redis.asyncio as aioredis

from app.config import get_settings
from app.core.exceptions import NexoraException
from app.services.alert_service import AlertService

logger = logging.getLogger(__name__)

# Failover modes. Same three-state discipline as PLAYBACK_IP_BINDING_MODE:
#   off     — default. The channel's declared node is used, no health is read,
#             no Redis call is made. Byte for byte today's behavior.
#   mirror  — an unhealthy node is swapped for the first HEALTHY node declared
#             as a mirror of that stream. If no candidate is healthy the
#             original node is kept, i.e. it degrades to `off` and can never be
#             worse than today.
#   strict  — same, but when nothing healthy is available it answers 503
#             NODE_UNAVAILABLE instead of handing out a URL to a node known to
#             be down.
MODE_OFF = "off"
MODE_MIRROR = "mirror"
MODE_STRICT = "strict"
_MODES = (MODE_OFF, MODE_MIRROR, MODE_STRICT)

# Raised (strict mode only) when neither the channel's node nor any declared
# mirror is usable. A named 503 — the request is refused because no origin can
# serve it, which is not the caller's fault and is not a 500 either.
REASON_NODE_UNAVAILABLE = "NODE_UNAVAILABLE"


@dataclass(frozen=True)
class _NodeSpec:
    """Static wiring of a node to its settings fields. Values are read from
    Settings on every access, never cached, so a monkeypatched or reloaded
    configuration is honored immediately."""
    node_id: str
    region: str
    priority: int
    base_url_attr: str
    user_attr: str
    password_attr: str
    mgmt_attr: str


# priority = declared preference of the node itself (lower first). It orders
# list_nodes() and is the shape the roadmap asks for; the per-stream mirror map
# is MORE specific, so for failover the map's order wins.
_SPECS: tuple[_NodeSpec, ...] = (
    _NodeSpec(
        "ec-main", "EC", 10,
        "flussonic_base_url", "flussonic_readonly_user",
        "flussonic_readonly_password", "flussonic_mgmt_base_url",
    ),
    _NodeSpec(
        "co-main", "CO", 20,
        "flussonic_co_main_base_url", "flussonic_co_main_user",
        "flussonic_co_main_password", "flussonic_co_main_mgmt_base_url",
    ),
    _NodeSpec(
        "ec-quito", "EC", 30,
        "flussonic_ec_quito_base_url", "flussonic_ec_quito_user",
        "flussonic_ec_quito_password", "flussonic_ec_quito_mgmt_base_url",
    ),
)
_SPEC_BY_ID: dict[str, _NodeSpec] = {s.node_id: s for s in _SPECS}


@dataclass(frozen=True)
class FlussonicNode:
    node_id: str
    base_url: str
    region: str
    priority: int
    is_healthy: bool

    @property
    def is_configured(self) -> bool:
        """A node with no base_url is declared but not wired up."""
        return bool(self.base_url)


# ── Static view (no I/O) ──────────────────────────────────────────────────────

def known_node_ids() -> list[str]:
    """Every node the registry knows about, in priority order."""
    return [s.node_id for s in _SPECS]


def configured_node_ids() -> list[str]:
    """Known nodes that actually have a base_url set, in priority order."""
    s = get_settings()
    return [n.node_id for n in _SPECS if getattr(s, n.base_url_attr, "")]


def describe_node(node_id: str, *, is_healthy: bool = True) -> FlussonicNode | None:
    """Static description of a node, or None if the id is unknown.

    `is_healthy` defaults to True: health is an external signal (see
    is_node_healthy) and "not known to be down" is the honest default.
    """
    spec = _SPEC_BY_ID.get(node_id)
    if spec is None:
        return None
    s = get_settings()
    return FlussonicNode(
        node_id=spec.node_id,
        base_url=getattr(s, spec.base_url_attr, "") or "",
        region=spec.region,
        priority=spec.priority,
        is_healthy=is_healthy,
    )


def node_credentials(node_id: str) -> tuple[str, str, str, str] | None:
    """(base_url, user, password, mgmt_base_url) for a node, or None if unknown.

    The only place credentials are read. They are handed straight to
    FlussonicClient, which keeps them private — nothing else in the codebase
    should call this.
    """
    spec = _SPEC_BY_ID.get(node_id)
    if spec is None:
        return None
    s = get_settings()
    return (
        getattr(s, spec.base_url_attr, "") or "",
        getattr(s, spec.user_attr, "") or "",
        getattr(s, spec.password_attr, "") or "",
        getattr(s, spec.mgmt_attr, "") or "",
    )


# ── Health (from the health check that already exists) ────────────────────────

async def is_node_healthy(redis: aioredis.Redis, node_id: str) -> bool:
    """Is `node_id` currently up, according to the existing monitor?

    No new probing and no second source of truth: `_stream_health_monitor` in
    app/main.py already runs `node_health.check_all_nodes()` every 2 minutes and
    feeds the result to AlertService, which keeps an open alert exactly while a
    node is down. Reading that alert is one Redis EXISTS, cheap enough for the
    request path, and it means the failover sees the SAME node state the
    operator sees at /admin/alerts.

    Consequence worth stating: the freshness of this signal is the monitor's
    interval, and its honesty is NODE_PROBE_MODE's. With the default
    `origin` probe the backend cannot reach any origin and calls every node
    down — which is why `mirror` mode falls back to the original node instead of
    failing, and why `strict` should only be enabled once NODE_PROBE_MODE is
    `hls_signed`.
    """
    return not await AlertService(redis).is_node_down(node_id)


async def list_nodes(redis: aioredis.Redis | None = None) -> list[FlussonicNode]:
    """Full registry in priority order, with live health when redis is given."""
    out: list[FlussonicNode] = []
    for spec in _SPECS:
        healthy = True if redis is None else await is_node_healthy(redis, spec.node_id)
        node = describe_node(spec.node_id, is_healthy=healthy)
        if node is not None:
            out.append(node)
    return out


# ── Failover ──────────────────────────────────────────────────────────────────

def failover_mode() -> str:
    mode = get_settings().flussonic_failover_mode.strip().lower()
    if mode not in _MODES:
        logger.warning(
            "Unknown FLUSSONIC_FAILOVER_MODE %r — falling back to %r", mode, MODE_OFF
        )
        return MODE_OFF
    return mode


def stream_mirrors(stream_key: str | None) -> list[str]:
    """Nodes declared as mirrors of `stream_key`, in operator preference order."""
    if not stream_key:
        return []
    return get_settings().flussonic_stream_mirror_map.get(stream_key, [])


async def _usable(redis: aioredis.Redis, node_id: str) -> bool:
    """Configured (has a base_url) AND not currently alerting as down."""
    node = describe_node(node_id)
    if node is None or not node.is_configured:
        return False
    return await is_node_healthy(redis, node_id)


async def resolve_playback_node(
    redis: aioredis.Redis,
    node_id: str | None,
    stream_key: str | None,
) -> str | None:
    """Return the node that should serve `stream_key` right now.

    Call this ONCE per playback request, before minting the token and before
    building the URL, and use the result for both (see the module docstring for
    why splitting them is a security regression rather than an optimization).

    Returns `node_id` unchanged whenever the feature is off, the node is fine,
    or nothing better exists. Raises 503 NODE_UNAVAILABLE only in strict mode
    when no candidate is usable.
    """
    mode = failover_mode()
    if mode == MODE_OFF or not node_id:
        return node_id

    if await _usable(redis, node_id):
        return node_id

    for candidate in stream_mirrors(stream_key):
        if candidate == node_id:
            continue
        if await _usable(redis, candidate):
            logger.warning(
                "node failover: stream=%s %s -> %s (mode=%s)",
                stream_key, node_id, candidate, mode,
            )
            return candidate

    if mode == MODE_STRICT:
        logger.error(
            "node failover exhausted: stream=%s node=%s has no healthy mirror",
            stream_key, node_id,
        )
        raise NexoraException(503, REASON_NODE_UNAVAILABLE)

    # mirror mode with nothing better: keep the declared node. The player gets
    # the same (dead) node it would get with the feature off — never worse.
    logger.warning(
        "node failover unavailable: stream=%s node=%s is down and has no healthy "
        "mirror — serving it anyway (mode=%s)", stream_key, node_id, mode,
    )
    return node_id
