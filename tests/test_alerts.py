"""M2 — node/stream alerting + multi-node health."""
import httpx
import pytest

from app.config import get_settings
from app.models.channel import Channel
from app.services.alert_service import AlertService
from app.services.stream_auth_service import StreamAuthService
from app.services import node_health as nh

pytestmark = pytest.mark.asyncio


async def test_alert_open_idempotent_resolve_list(redis_client):
    a = AlertService(redis_client)
    assert await a.record_node_health("co-main", False, "timeout") == "opened"
    assert await a.record_node_health("co-main", False, "timeout") is None  # already open
    active = await a.active_alerts()
    assert len(active) == 1 and active[0]["id"] == "co-main" and active[0]["status"] == "down"
    assert await a.record_node_health("co-main", True) == "resolved"
    assert await a.active_alerts() == []
    assert await a.record_node_health("co-main", True) is None  # nothing to resolve


class _FakeClient:
    is_configured = True
    async def check_connectivity(self):
        return True
    async def list_streams(self):
        return [1, 2, 3]


async def test_check_node_reports_health(monkeypatch):
    monkeypatch.setattr(nh, "get_flussonic_node_client", lambda nid: _FakeClient())
    monkeypatch.setattr(get_settings(), "flussonic_base_url", "http://ec.example:8002")
    out = await nh.check_node("ec-main")
    assert out["configured"] and out["reachable"]
    assert out["stream_count"] == 3 and out["region"] == "EC"
    assert out["host"] == "ec.example:8002"


async def test_configured_node_ids(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "flussonic_base_url", "http://ec:8002")
    monkeypatch.setattr(s, "flussonic_co_main_base_url", "")
    monkeypatch.setattr(s, "flussonic_ec_quito_base_url", "", raising=False)
    ids = nh.configured_node_ids()
    assert "ec-main" in ids and "co-main" not in ids and "ec-quito" not in ids


# ── P0.5 · signed-HLS probe through the edge ──────────────────────────────────

class _FakeResponse:
    def __init__(self, status_code: int):
        self.status_code = status_code

    @property
    def is_success(self) -> bool:
        return 200 <= self.status_code < 300


def _stub_probe_http(monkeypatch, *, status=None, exc=None) -> dict:
    """Replace the probe's HTTP seam; return a dict capturing the call."""
    captured: dict = {}

    async def fake_get(url, params, timeout):
        captured.update(url=url, params=params, timeout=timeout)
        if exc is not None:
            raise exc
        return _FakeResponse(status)

    monkeypatch.setattr(nh, "_get", fake_get)
    return captured


def _probe_settings(monkeypatch, **over):
    s = get_settings()
    monkeypatch.setattr(s, "node_probe_mode", over.get("mode", nh.MODE_HLS_SIGNED))
    monkeypatch.setattr(s, "node_probe_edge_base_url", "http://nexora_nginx")
    monkeypatch.setattr(s, "node_probe_streams", over.get("streams", ""))
    monkeypatch.setattr(s, "flussonic_co_main_base_url", "http://co.example:8002")
    return s


async def _seed_channel(db_session, node="co-main", stream_key="K-CO", number=7):
    db_session.add(Channel(
        channel_key=f"c-{stream_key}", number=number, name="probe",
        stream_key=stream_key, flussonic_node=node, is_active=True,
    ))
    await db_session.commit()


async def test_probe_ok_marks_node_healthy(monkeypatch, db_session, redis_client):
    _probe_settings(monkeypatch)
    await _seed_channel(db_session)
    captured = _stub_probe_http(monkeypatch, status=200)

    out = await nh.check_node_hls_signed("co-main", db_session, redis_client)

    assert out["reachable"] is True
    assert out["detail"] is None
    assert out["probe_mode"] == nh.MODE_HLS_SIGNED
    assert out["latency_ms"] is not None
    # Probes the EDGE at /stream/<node>/<stream>/index.m3u8 with a minted token.
    assert captured["url"] == "http://nexora_nginx/stream/co-main/K-CO/index.m3u8"
    assert captured["params"]["token"]


@pytest.mark.parametrize("status", [401, 404, 500])
async def test_probe_http_error_marks_node_down_with_reason(
    monkeypatch, db_session, redis_client, status
):
    _probe_settings(monkeypatch)
    await _seed_channel(db_session)
    _stub_probe_http(monkeypatch, status=status)

    out = await nh.check_node_hls_signed("co-main", db_session, redis_client)

    assert out["reachable"] is False
    assert f"HTTP {status}" in out["detail"] and "K-CO" in out["detail"]


async def test_probe_timeout_marks_node_down_with_reason(
    monkeypatch, db_session, redis_client
):
    _probe_settings(monkeypatch)
    await _seed_channel(db_session)
    _stub_probe_http(monkeypatch, exc=httpx.ConnectTimeout("timed out"))

    out = await nh.check_node_hls_signed("co-main", db_session, redis_client)

    assert out["reachable"] is False
    assert "ConnectTimeout" in out["detail"]


async def test_probe_without_a_stream_reports_it_instead_of_probing(
    monkeypatch, db_session, redis_client
):
    """No pin and no active channel on the node → say so, don't fake a 200."""
    _probe_settings(monkeypatch)
    captured = _stub_probe_http(monkeypatch, status=200)

    out = await nh.check_node_hls_signed("co-main", db_session, redis_client)

    assert out["reachable"] is False
    assert "NODE_PROBE_STREAMS" in out["detail"]
    assert captured == {}  # no HTTP call attempted


async def test_probe_stream_pin_overrides_catalog(monkeypatch, db_session, redis_client):
    _probe_settings(monkeypatch, streams="co-main:PINNED,ec-main:OTHER")
    await _seed_channel(db_session)
    captured = _stub_probe_http(monkeypatch, status=200)

    await nh.check_node_hls_signed("co-main", db_session, redis_client)

    assert captured["url"].endswith("/stream/co-main/PINNED/index.m3u8")


async def test_probe_leaves_no_segment_grant_behind(db_session, redis_client):
    """A successful probe must NOT open tokenless segment access for its own IP.

    Drives the REAL auth_request gate the way Nginx does, so this fails if a
    grant is ever seeded for a probe token again — the probe runs every
    monitoring cycle, and a grant it opens is exactly the revocation-latency hole
    STREAM_GRANT_MAX_LIFETIME_SECONDS exists to bound.
    """
    from httpx import ASGITransport
    from app.main import app
    from app.database import get_db
    from app.redis_client import get_redis as _get_redis
    from app.core.security import hash_ip

    probe_ip = "172.20.0.9"  # the api container as Nginx sees it
    svc = StreamAuthService(db_session, redis_client)
    token, jti = await svc.mint_probe_token("co-main", "K-CO")

    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[_get_redis] = lambda: redis_client
    try:
        async with httpx.AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as c:
            H = {"X-Real-IP": probe_ip}
            # Manifest with the probe token → authorized, exactly like a player.
            r = await c.get(
                "/internal/stream-auth/validate",
                params={"token": token, "stream_key": "K-CO", "node": "co-main"},
                headers=H,
            )
            assert r.status_code == 200, r.text

            # …but a tokenless SEGMENT from that same IP must still be denied.
            r = await c.get(
                "/internal/stream-auth/validate",
                params={"stream_key": "K-CO", "node": "co-main"},
                headers=H,
            )
            assert r.status_code == 401, "probe leaked a segment grant for its own IP"
    finally:
        app.dependency_overrides.clear()
        await svc.release_probe_token(jti)

    # No grant key survives the probe at all.
    assert await svc.check_stream_grant("co-main", "K-CO", hash_ip(probe_ip)) is False


async def test_real_token_still_seeds_its_grant(entitlement_world, redis_client):
    """The other side of the guard: suppressing the grant is scoped to probe
    tokens and must not touch the path real players depend on."""
    from httpx import ASGITransport
    from app.main import app
    from app.database import get_db
    from app.redis_client import get_redis as _get_redis

    ip = "1.2.3.4"
    svc = StreamAuthService(entitlement_world["session"], redis_client)
    res = await svc.authorize(
        subscriber_id=entitlement_world["subscriber"].id,
        device_id_str=entitlement_world["device"].device_id,
        channel_id="K1", ip=ip, channel_key="canal-1", node="co-main",
    )
    await entitlement_world["session"].commit()

    app.dependency_overrides[get_db] = lambda: entitlement_world["session"]
    app.dependency_overrides[_get_redis] = lambda: redis_client
    try:
        async with httpx.AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as c:
            H = {"X-Real-IP": ip}
            r = await c.get(
                "/internal/stream-auth/validate",
                params={"token": res.token, "stream_key": "K1", "node": "co-main"},
                headers=H,
            )
            assert r.status_code == 200, r.text
            r = await c.get(
                "/internal/stream-auth/validate",
                params={"stream_key": "K1", "node": "co-main"},
                headers=H,
            )
            assert r.status_code == 200, "real player lost its segment grant"
    finally:
        app.dependency_overrides.clear()


async def test_probe_claim_rides_inside_the_signed_token(entitlement_world, redis_client):
    """'pb' is a signed claim, not a request parameter, so a client cannot ask to
    be treated as a probe (nor a probe be mistaken for a player)."""
    svc = StreamAuthService(entitlement_world["session"], redis_client)
    res = await svc.authorize(
        subscriber_id=entitlement_world["subscriber"].id,
        device_id_str=entitlement_world["device"].device_id,
        channel_id="K1", ip="1.2.3.4", channel_key="canal-1", node="co-main",
    )
    out = await svc.validate_stream_request(res.token, stream_key="K1", node="co-main")
    assert out["probe"] is False

    token, jti = await svc.mint_probe_token("co-main", "K-CO")
    try:
        out = await svc.validate_stream_request(token, stream_key="K-CO", node="co-main")
        assert out["probe"] is True
    finally:
        await svc.release_probe_token(jti)


async def test_probe_token_passes_the_real_stream_gate(
    monkeypatch, db_session, redis_client
):
    """The probe must exercise the real gate, not a look-alike: the token it
    mints has to satisfy validate_stream_request for that node+stream, and must
    be dead once the probe releases it."""
    svc = StreamAuthService(db_session, redis_client)
    token, jti = await svc.mint_probe_token("co-main", "K-CO")

    out = await svc.validate_stream_request(token, stream_key="K-CO", node="co-main")
    assert out["stream_key"] == "K-CO" and out["node"] == "co-main"

    await svc.release_probe_token(jti)
    with pytest.raises(Exception):
        await svc.validate_stream_request(token, stream_key="K-CO", node="co-main")


async def test_probe_token_is_bound_to_its_stream(db_session, redis_client):
    svc = StreamAuthService(db_session, redis_client)
    token, jti = await svc.mint_probe_token("co-main", "K-CO")
    try:
        with pytest.raises(Exception):
            await svc.validate_stream_request(token, stream_key="OTHER", node="co-main")
    finally:
        await svc.release_probe_token(jti)


# ── flag gating: legacy mode must be untouched ────────────────────────────────

async def test_legacy_mode_is_the_default_and_uses_the_origin_probe(monkeypatch):
    assert get_settings().node_probe_mode == nh.MODE_ORIGIN
    monkeypatch.setattr(nh, "get_flussonic_node_client", lambda nid: _FakeClient())
    monkeypatch.setattr(get_settings(), "flussonic_base_url", "http://ec.example:8002")

    called = False

    async def must_not_run(*a, **kw):
        nonlocal called
        called = True

    monkeypatch.setattr(nh, "check_node_hls_signed", must_not_run)

    out = await nh.check_node("ec-main")
    assert called is False
    assert out["probe_mode"] == nh.MODE_ORIGIN
    assert out["reachable"] and out["stream_count"] == 3


async def test_unknown_probe_mode_falls_back_to_origin(monkeypatch):
    monkeypatch.setattr(get_settings(), "node_probe_mode", "nonsense")
    assert nh.probe_mode() == nh.MODE_ORIGIN
