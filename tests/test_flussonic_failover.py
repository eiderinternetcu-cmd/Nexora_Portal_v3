"""Flussonic node registry + node failover (P2.2 / NX-FLU).

The contract under test, in one sentence: the node is chosen ONCE per playback
request, before the token exists, and the SAME value ends up in the token's
`node` claim and in the /stream/<node>/ path of the playback_url — so a failover
always issues a NEW token bound to the NEW node and never reuses one across
nodes (which is exactly what PLAYBACK_NODE_BINDING_ENFORCE refuses).

Node health is read from the alert the existing monitor keeps open
(AlertService.record_node_health), so these tests mark a node down the same way
production does instead of inventing a second health signal.
"""
import jwt
import pytest
import pytest_asyncio
from starlette.requests import Request

from app.api.client import playback as pb_mod
from app.config import get_settings
from app.core.exceptions import NexoraException
from app.integrations import flussonic_client as fc
from app.integrations import flussonic_registry as registry
from app.models.channel import Channel
from app.schemas.client import PlaybackAuthorizeRequest
from app.services.alert_service import AlertService

pytestmark = pytest.mark.asyncio
settings = get_settings()

HOST = "nexoraplay.net"
EC_MAIN_BASE = f"https://{HOST}/stream/ec-main"
CO_MAIN_BASE = f"https://{HOST}/stream/co-main"
EC_QUITO_BASE = f"https://{HOST}/stream/ec-quito"


# ── fixtures / helpers ───────────────────────────────────────────────────────

@pytest.fixture
def nodes(monkeypatch):
    """All three nodes wired up, failover OFF, client cache reset."""
    s = get_settings()
    monkeypatch.setattr(s, "flussonic_base_url", EC_MAIN_BASE)
    monkeypatch.setattr(s, "flussonic_readonly_user", "ro")
    monkeypatch.setattr(s, "flussonic_readonly_password", "x")
    monkeypatch.setattr(s, "flussonic_co_main_base_url", CO_MAIN_BASE)
    monkeypatch.setattr(s, "flussonic_co_main_user", "ro")
    monkeypatch.setattr(s, "flussonic_co_main_password", "x")
    monkeypatch.setattr(s, "flussonic_ec_quito_base_url", EC_QUITO_BASE)
    monkeypatch.setattr(s, "flussonic_ec_quito_user", "ro")
    monkeypatch.setattr(s, "flussonic_ec_quito_password", "x")
    monkeypatch.setattr(s, "flussonic_failover_mode", registry.MODE_OFF)
    monkeypatch.setattr(s, "flussonic_stream_mirrors", "")
    monkeypatch.setattr(s, "signed_url_enforce", False)
    monkeypatch.setattr(s, "playback_host_allowlist", "")
    fc._node_clients.clear()
    yield s
    fc._node_clients.clear()


@pytest_asyncio.fixture
async def world(entitlement_world):
    """entitlement_world with a device_id long enough for the request schema
    (PlaybackAuthorizeRequest requires >= 6 chars; the shared fixture uses
    'dev-1', which is fine for the service layer but not for the endpoint)."""
    entitlement_world["device"].device_id = "dev-0001"
    await entitlement_world["session"].commit()
    return entitlement_world


async def _mark_down(redis, node_id: str) -> None:
    """Down exactly the way production marks it: an open AlertService alert."""
    await AlertService(redis).record_node_health(node_id, False, "probe failed")


def _req() -> Request:
    return Request({
        "type": "http", "http_version": "1.1", "method": "POST",
        "scheme": "https", "root_path": "", "path": "/api/client/playback/authorize",
        "raw_path": b"/api/client/playback/authorize", "query_string": b"",
        "headers": [(b"host", HOST.encode())],
        "client": ("10.0.0.1", 51234), "server": ("nexora_api", 8000),
    })


def _claims(token: str) -> dict:
    return jwt.decode(token, settings.secret_key,
                      algorithms=[settings.jwt_algorithm], options={"verify_aud": False})


async def _authorize(world, redis, channel_key="canal-1"):
    """Call the REAL handler — Depends defaults are bypassed with explicit args,
    so what is exercised is the endpoint, not a re-implementation of it."""
    return await pb_mod.authorize_playback(
        data=PlaybackAuthorizeRequest(
            device_id=world["device"].device_id, channel_id=channel_key
        ),
        request=_req(),
        subscriber=world["subscriber"],
        db=world["session"],
        redis=redis,
    )


async def _put_on_node(world, node_id: str) -> None:
    world["ch_in"].flussonic_node = node_id
    await world["session"].commit()


# ── the registry itself ──────────────────────────────────────────────────────

def test_registry_declares_the_three_nodes_in_priority_order(nodes):
    assert registry.known_node_ids() == ["ec-main", "co-main", "ec-quito"]
    assert registry.configured_node_ids() == ["ec-main", "co-main", "ec-quito"]


def test_registry_node_shape(nodes):
    node = registry.describe_node("co-main")
    assert node.node_id == "co-main"
    assert node.base_url == CO_MAIN_BASE
    assert node.region == "CO"
    assert node.priority == 20
    assert node.is_healthy is True       # unknown → not known to be down
    assert node.is_configured is True


def test_registry_unknown_node_is_none(nodes):
    assert registry.describe_node("rogue-node") is None
    assert registry.node_credentials("rogue-node") is None


def test_registry_unconfigured_node_drops_out_of_configured_ids(nodes, monkeypatch):
    monkeypatch.setattr(nodes, "flussonic_co_main_base_url", "")
    assert registry.configured_node_ids() == ["ec-main", "ec-quito"]
    assert registry.describe_node("co-main").is_configured is False


def test_flussonic_client_factory_reads_the_registry(nodes):
    """The if/elif chain is gone; the client still comes out fully configured."""
    client = fc.get_flussonic_node_client("ec-quito")
    assert client is not None and client.is_configured
    assert client.stream_hls_url("K1") == f"{EC_QUITO_BASE}/K1/index.m3u8"
    assert fc.get_flussonic_node_client("rogue-node") is None


async def test_registry_health_comes_from_the_existing_alert(nodes, redis_client):
    assert await registry.is_node_healthy(redis_client, "co-main") is True
    await _mark_down(redis_client, "co-main")
    assert await registry.is_node_healthy(redis_client, "co-main") is False
    # …and recovery is the monitor resolving the alert, nothing else.
    await AlertService(redis_client).record_node_health("co-main", True)
    assert await registry.is_node_healthy(redis_client, "co-main") is True


async def test_list_nodes_carries_live_health(nodes, redis_client):
    await _mark_down(redis_client, "co-main")
    health = {n.node_id: n.is_healthy for n in await registry.list_nodes(redis_client)}
    assert health == {"ec-main": True, "co-main": False, "ec-quito": True}


# ── mirror map parsing ───────────────────────────────────────────────────────

def test_mirror_map_parses_order_and_ignores_junk(nodes, monkeypatch):
    monkeypatch.setattr(
        nodes, "flussonic_stream_mirrors",
        " caracol : ec-main | ec-quito , rcn:ec-main , broken , :ec-main , dup:ec-main|ec-main ",
    )
    assert nodes.flussonic_stream_mirror_map == {
        "caracol": ["ec-main", "ec-quito"],   # preference order preserved
        "rcn": ["ec-main"],
        "dup": ["ec-main"],                   # duplicates collapsed
    }


def test_mirror_map_empty_by_default(nodes):
    assert nodes.flussonic_stream_mirror_map == {}


# ── resolve_playback_node ────────────────────────────────────────────────────

async def test_flag_off_is_a_no_op_even_with_a_dead_node(nodes, redis_client, monkeypatch):
    """Default = today byte for byte: the declared node is returned regardless."""
    monkeypatch.setattr(nodes, "flussonic_stream_mirrors", "K1:ec-main")
    await _mark_down(redis_client, "co-main")
    assert await registry.resolve_playback_node(redis_client, "co-main", "K1") == "co-main"


async def test_healthy_node_is_kept(nodes, redis_client, monkeypatch):
    monkeypatch.setattr(nodes, "flussonic_failover_mode", registry.MODE_MIRROR)
    monkeypatch.setattr(nodes, "flussonic_stream_mirrors", "K1:ec-main")
    assert await registry.resolve_playback_node(redis_client, "co-main", "K1") == "co-main"


async def test_down_node_switches_to_the_declared_mirror(nodes, redis_client, monkeypatch):
    monkeypatch.setattr(nodes, "flussonic_failover_mode", registry.MODE_MIRROR)
    monkeypatch.setattr(nodes, "flussonic_stream_mirrors", "K1:ec-main")
    await _mark_down(redis_client, "co-main")
    assert await registry.resolve_playback_node(redis_client, "co-main", "K1") == "ec-main"


async def test_first_healthy_candidate_wins_in_declared_order(nodes, redis_client, monkeypatch):
    monkeypatch.setattr(nodes, "flussonic_failover_mode", registry.MODE_MIRROR)
    monkeypatch.setattr(nodes, "flussonic_stream_mirrors", "K1:ec-main|ec-quito")
    await _mark_down(redis_client, "co-main")
    await _mark_down(redis_client, "ec-main")
    assert await registry.resolve_playback_node(redis_client, "co-main", "K1") == "ec-quito"


async def test_unconfigured_mirror_is_skipped(nodes, redis_client, monkeypatch):
    monkeypatch.setattr(nodes, "flussonic_failover_mode", registry.MODE_MIRROR)
    monkeypatch.setattr(nodes, "flussonic_stream_mirrors", "K1:ec-main|ec-quito")
    monkeypatch.setattr(nodes, "flussonic_base_url", "")   # ec-main declared, not wired
    await _mark_down(redis_client, "co-main")
    assert await registry.resolve_playback_node(redis_client, "co-main", "K1") == "ec-quito"


async def test_mirror_mode_without_alternative_keeps_the_node(nodes, redis_client, monkeypatch):
    """No mirror declared → degrade to today's behavior, never to an exception."""
    monkeypatch.setattr(nodes, "flussonic_failover_mode", registry.MODE_MIRROR)
    await _mark_down(redis_client, "co-main")
    assert await registry.resolve_playback_node(redis_client, "co-main", "K1") == "co-main"


async def test_mirror_mode_with_every_candidate_down_keeps_the_node(
    nodes, redis_client, monkeypatch
):
    monkeypatch.setattr(nodes, "flussonic_failover_mode", registry.MODE_MIRROR)
    monkeypatch.setattr(nodes, "flussonic_stream_mirrors", "K1:ec-main|ec-quito")
    for n in ("co-main", "ec-main", "ec-quito"):
        await _mark_down(redis_client, n)
    assert await registry.resolve_playback_node(redis_client, "co-main", "K1") == "co-main"


async def test_strict_mode_without_alternative_is_a_named_503(nodes, redis_client, monkeypatch):
    """A clear, named refusal — not a 500, and not a URL to a node known to be dead."""
    monkeypatch.setattr(nodes, "flussonic_failover_mode", registry.MODE_STRICT)
    await _mark_down(redis_client, "co-main")
    with pytest.raises(NexoraException) as ei:
        await registry.resolve_playback_node(redis_client, "co-main", "K1")
    assert ei.value.status_code == 503
    assert ei.value.detail == registry.REASON_NODE_UNAVAILABLE == "NODE_UNAVAILABLE"


async def test_strict_mode_still_prefers_a_healthy_mirror_over_failing(
    nodes, redis_client, monkeypatch
):
    monkeypatch.setattr(nodes, "flussonic_failover_mode", registry.MODE_STRICT)
    monkeypatch.setattr(nodes, "flussonic_stream_mirrors", "K1:ec-main")
    await _mark_down(redis_client, "co-main")
    assert await registry.resolve_playback_node(redis_client, "co-main", "K1") == "ec-main"


async def test_mirrors_are_per_stream_not_per_node(nodes, redis_client, monkeypatch):
    """K1 is mirrored, K99 is not — the same dead node fails over for one and not
    the other, which is the whole point of declaring this per stream."""
    monkeypatch.setattr(nodes, "flussonic_failover_mode", registry.MODE_MIRROR)
    monkeypatch.setattr(nodes, "flussonic_stream_mirrors", "K1:ec-main")
    await _mark_down(redis_client, "co-main")
    assert await registry.resolve_playback_node(redis_client, "co-main", "K1") == "ec-main"
    assert await registry.resolve_playback_node(redis_client, "co-main", "K99") == "co-main"


async def test_unknown_mode_falls_back_to_off(nodes, redis_client, monkeypatch):
    monkeypatch.setattr(nodes, "flussonic_failover_mode", "aggressive")
    monkeypatch.setattr(nodes, "flussonic_stream_mirrors", "K1:ec-main")
    await _mark_down(redis_client, "co-main")
    assert registry.failover_mode() == registry.MODE_OFF
    assert await registry.resolve_playback_node(redis_client, "co-main", "K1") == "co-main"


async def test_no_node_resolves_to_no_node(nodes, redis_client, monkeypatch):
    monkeypatch.setattr(nodes, "flussonic_failover_mode", registry.MODE_STRICT)
    assert await registry.resolve_playback_node(redis_client, None, "K1") is None


# ── end to end through the endpoint: token AND url follow the new node ───────

async def test_authorize_on_a_healthy_node_binds_the_token_to_it(
    nodes, redis_client, world
):
    await _put_on_node(world, "co-main")
    resp = await _authorize(world, redis_client)
    assert _claims(resp.token)["node"] == "co-main"
    assert resp.playback_url == f"{CO_MAIN_BASE}/K1/index.m3u8"


async def test_failover_mints_a_new_token_bound_to_the_new_node(
    nodes, redis_client, world, monkeypatch
):
    """THE security property: after a failover the token's `node` claim is the
    NEW node and the URL path is the NEW node. No token is carried over."""
    monkeypatch.setattr(nodes, "flussonic_failover_mode", registry.MODE_MIRROR)
    monkeypatch.setattr(nodes, "flussonic_stream_mirrors", "K1:ec-main")
    await _put_on_node(world, "co-main")
    await _mark_down(redis_client, "co-main")

    resp = await _authorize(world, redis_client)
    claims = _claims(resp.token)

    assert claims["node"] == "ec-main"            # not the channel's declared node
    assert claims["sk"] == "K1"
    assert resp.playback_url == f"{EC_MAIN_BASE}/K1/index.m3u8"
    # url path and token claim agree — that agreement is what the /stream/* gate
    # checks under PLAYBACK_NODE_BINDING_ENFORCE.
    assert f"/stream/{claims['node']}/" in resp.playback_url


async def test_token_issued_before_failover_is_not_reused_after_it(
    nodes, redis_client, world, monkeypatch
):
    """Two authorizations either side of the outage must be different tokens with
    different `node` claims — a failover that recycled the first one would be the
    cross-node reuse NX-NODE closed."""
    monkeypatch.setattr(nodes, "flussonic_failover_mode", registry.MODE_MIRROR)
    monkeypatch.setattr(nodes, "flussonic_stream_mirrors", "K1:ec-main")
    await _put_on_node(world, "co-main")

    before = await _authorize(world, redis_client)
    await _mark_down(redis_client, "co-main")
    after = await _authorize(world, redis_client)

    assert _claims(before.token)["node"] == "co-main"
    assert _claims(after.token)["node"] == "ec-main"
    assert before.token != after.token
    assert _claims(before.token)["jti"] != _claims(after.token)["jti"]


async def test_reissue_also_follows_the_failover(
    nodes, redis_client, world, monkeypatch
):
    """The reissue route is where failover reaches a session already playing (the
    player renews every ~45s), so it must resolve the node too."""
    monkeypatch.setattr(nodes, "flussonic_failover_mode", registry.MODE_MIRROR)
    monkeypatch.setattr(nodes, "flussonic_stream_mirrors", "K1:ec-main")
    await _put_on_node(world, "co-main")
    await _authorize(world, redis_client)   # opens the IPTV session

    await _mark_down(redis_client, "co-main")
    resp = await pb_mod.reissue_playback_token(
        channel_id="canal-1",
        request=_req(),
        device_id=world["device"].device_id,
        subscriber=world["subscriber"],
        db=world["session"],
        redis=redis_client,
    )
    assert _claims(resp.token)["node"] == "ec-main"
    assert resp.playback_url == f"{EC_MAIN_BASE}/K1/index.m3u8"


async def test_authorize_without_alternative_answers_503_not_500(
    nodes, redis_client, world, monkeypatch
):
    monkeypatch.setattr(nodes, "flussonic_failover_mode", registry.MODE_STRICT)
    await _put_on_node(world, "co-main")
    await _mark_down(redis_client, "co-main")

    with pytest.raises(NexoraException) as ei:
        await _authorize(world, redis_client)
    assert ei.value.status_code == 503
    assert ei.value.detail == "NODE_UNAVAILABLE"


async def test_failover_url_never_falls_back_to_the_old_node_source_url(
    nodes, redis_client, world, monkeypatch
):
    """source_url names the DECLARED node in its path, so after a failover it
    contradicts the token. No URL is better than one the gate will refuse."""
    monkeypatch.setattr(nodes, "flussonic_failover_mode", registry.MODE_MIRROR)
    monkeypatch.setattr(nodes, "flussonic_stream_mirrors", "K1:ec-main")
    monkeypatch.setattr(nodes, "flussonic_readonly_user", "")   # ec-main URL unbuildable
    fc._node_clients.clear()
    world["ch_in"].source_url = f"{CO_MAIN_BASE}/K1/index.m3u8"
    await _put_on_node(world, "co-main")
    await _mark_down(redis_client, "co-main")

    resp = await _authorize(world, redis_client)
    assert _claims(resp.token)["node"] == "ec-main"
    assert resp.playback_url is None
    assert "co-main" not in (resp.playback_url or "")


def test_source_url_fallback_survives_when_no_failover_happened(nodes):
    """Guards the guard above: the same fallback still works for the node the
    channel declares, so the new check did not silently kill it."""
    ch = Channel(channel_key="c", number=1, name="C", stream_key="K1",
                 flussonic_node="co-main", source_url="/stream/co-main/K1/index.m3u8")
    assert pb_mod._resolve_playback_url(ch, None) == "/stream/co-main/K1/index.m3u8"


# ── node_health keeps working off the shared registry ────────────────────────

def test_node_health_reads_the_registry(nodes, monkeypatch):
    from app.services import node_health as nh
    assert nh.configured_node_ids() == ["ec-main", "co-main", "ec-quito"]
    base = nh._base_result("co-main", nh.MODE_HLS_SIGNED)
    assert base["region"] == "CO" and base["host"] == HOST
    assert nh._base_result("rogue-node", nh.MODE_ORIGIN)["region"] is None
