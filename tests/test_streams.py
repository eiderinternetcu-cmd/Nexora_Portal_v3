"""P2.1 — GET /api/admin/streams: `is_active` (DB) vs `alive` (Flussonic).

Before this endpoint, an admin could not tell "we turned this channel off"
apart from "the source died" — /channels/{id}/stream-status asked the
Flussonic ORIGIN directly, which the `api` container cannot reach in
production (only Nginx can; see app/services/node_health.py). This endpoint
probes through the edge instead, the same technique the node-health monitor
uses, applied per channel.
"""
import httpx
import pytest
from httpx import ASGITransport

from app.config import get_settings
from app.models.channel import Channel
from app.models.user import User, UserRole
from app.api.admin import streams as streams_mod

pytestmark = pytest.mark.asyncio


# ── helpers ──────────────────────────────────────────────────────────────────

class _FakeResponse:
    def __init__(self, status_code: int):
        self.status_code = status_code

    @property
    def is_success(self) -> bool:
        return 200 <= self.status_code < 300


def _stub_probe_http(monkeypatch, *, status=None, exc=None) -> dict:
    """Replace the probe's HTTP seam (app.api.admin.streams._get) — same shape
    as app.services.node_health's own seam, so tests never touch real httpx."""
    calls: list[dict] = []

    async def fake_get(url, params, timeout):
        calls.append({"url": url, "params": params, "timeout": timeout})
        if exc is not None:
            raise exc
        return _FakeResponse(status)

    monkeypatch.setattr(streams_mod, "_get", fake_get)
    return calls


def _probe_settings(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "node_probe_edge_base_url", "http://nexora_nginx")
    monkeypatch.setattr(s, "node_probe_timeout_seconds", 8.0)
    monkeypatch.setattr(s, "flussonic_base_url", "http://ec.example:8002")
    monkeypatch.setattr(s, "flussonic_co_main_base_url", "http://co.example:8002")
    monkeypatch.setattr(s, "flussonic_ec_quito_base_url", "", raising=False)
    return s


async def _client(db, redis, user: User):
    from app.main import app
    from app.database import get_db
    from app.redis_client import get_redis
    from app.core.dependencies import get_current_user

    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_redis] = lambda: redis
    app.dependency_overrides[get_current_user] = lambda: user
    return app


@pytest.fixture
def admin(db_session):
    return User(username="streams_admin", email="streams_admin@test.local",
                password_hash="x", role=UserRole.admin, is_active=True)


@pytest.fixture
def reseller(db_session):
    return User(username="streams_reseller", email="streams_reseller@test.local",
                password_hash="x", role=UserRole.reseller, is_active=True)


# ── tests ────────────────────────────────────────────────────────────────────

async def test_active_channel_alive_true_on_2xx(monkeypatch, db_session, redis_client, admin):
    _probe_settings(monkeypatch)
    calls = _stub_probe_http(monkeypatch, status=200)
    db_session.add(Channel(channel_key="ch-up", number=1, name="Up",
                            stream_key="K-UP", flussonic_node="co-main", is_active=True))
    await db_session.commit()

    app = await _client(db_session, redis_client, admin)
    try:
        async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get("/api/admin/streams")
    finally:
        app.dependency_overrides.clear()

    assert r.status_code == 200, r.text
    row = next(x for x in r.json() if x["channel_key"] == "ch-up")
    assert row["is_active"] is True
    assert row["alive"] is True
    assert row["probe_mode"] == "hls_signed"
    assert row["detail"] is None
    assert calls[0]["url"] == "http://nexora_nginx/stream/co-main/K-UP/index.m3u8"
    assert calls[0]["params"]["token"]


async def test_active_channel_alive_false_on_error_status(monkeypatch, db_session, redis_client, admin):
    _probe_settings(monkeypatch)
    _stub_probe_http(monkeypatch, status=502)
    db_session.add(Channel(channel_key="ch-dead", number=2, name="Dead",
                            stream_key="K-DEAD", flussonic_node="co-main", is_active=True))
    await db_session.commit()

    app = await _client(db_session, redis_client, admin)
    try:
        async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get("/api/admin/streams")
    finally:
        app.dependency_overrides.clear()

    row = next(x for x in r.json() if x["channel_key"] == "ch-dead")
    assert row["is_active"] is True
    assert row["alive"] is False
    assert "502" in row["detail"]


async def test_inactive_channel_is_not_probed(monkeypatch, db_session, redis_client, admin):
    """The DB says we turned it off — no reason to ask Flussonic anything."""
    _probe_settings(monkeypatch)
    calls = _stub_probe_http(monkeypatch, status=200)
    db_session.add(Channel(channel_key="ch-off", number=3, name="Off",
                            stream_key="K-OFF", flussonic_node="co-main", is_active=False))
    await db_session.commit()

    app = await _client(db_session, redis_client, admin)
    try:
        async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get("/api/admin/streams")
    finally:
        app.dependency_overrides.clear()

    row = next(x for x in r.json() if x["channel_key"] == "ch-off")
    assert row["is_active"] is False
    assert row["alive"] is None
    assert "inactive" in row["detail"]
    assert calls == []  # no HTTP call attempted for an inactive channel


async def test_channel_on_unconfigured_node_is_not_probed(monkeypatch, db_session, redis_client, admin):
    _probe_settings(monkeypatch)
    calls = _stub_probe_http(monkeypatch, status=200)
    db_session.add(Channel(channel_key="ch-noquito", number=4, name="NoQuito",
                            stream_key="K-Q", flussonic_node="ec-quito", is_active=True))
    await db_session.commit()

    app = await _client(db_session, redis_client, admin)
    try:
        async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get("/api/admin/streams")
    finally:
        app.dependency_overrides.clear()

    row = next(x for x in r.json() if x["channel_key"] == "ch-noquito")
    assert row["alive"] is None
    assert "not configured" in row["detail"]
    assert calls == []


async def test_probe_transport_error_marks_alive_false_with_reason(
    monkeypatch, db_session, redis_client, admin
):
    _probe_settings(monkeypatch)
    _stub_probe_http(monkeypatch, exc=httpx.ConnectTimeout("timed out"))
    db_session.add(Channel(channel_key="ch-timeout", number=5, name="Timeout",
                            stream_key="K-TO", flussonic_node="co-main", is_active=True))
    await db_session.commit()

    app = await _client(db_session, redis_client, admin)
    try:
        async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get("/api/admin/streams")
    finally:
        app.dependency_overrides.clear()

    row = next(x for x in r.json() if x["channel_key"] == "ch-timeout")
    assert row["alive"] is False
    assert "ConnectTimeout" in row["detail"]


async def test_response_never_carries_stream_key_or_flussonic_credentials(
    monkeypatch, db_session, redis_client, admin
):
    _probe_settings(monkeypatch)
    _stub_probe_http(monkeypatch, status=200)
    db_session.add(Channel(channel_key="ch-secret", number=6, name="Secret",
                            stream_key="TOP_SECRET_KEY", flussonic_node="co-main", is_active=True))
    await db_session.commit()

    app = await _client(db_session, redis_client, admin)
    try:
        async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get("/api/admin/streams")
    finally:
        app.dependency_overrides.clear()

    assert "TOP_SECRET_KEY" not in r.text
    assert "stream_key" not in r.text
    assert "source_url" not in r.text


async def test_reseller_can_read_streams(monkeypatch, db_session, redis_client, reseller):
    """Same role as /nodes/health and /channels/{id}/stream-status — no secret
    leaves this route, so a reseller reading it is not the P0.8 hole."""
    _probe_settings(monkeypatch)
    _stub_probe_http(monkeypatch, status=200)

    app = await _client(db_session, redis_client, reseller)
    try:
        async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get("/api/admin/streams")
    finally:
        app.dependency_overrides.clear()

    assert r.status_code == 200, r.text


async def test_unauthenticated_gets_401(db_session, redis_client):
    from app.main import app
    from app.database import get_db
    from app.redis_client import get_redis

    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[get_redis] = lambda: redis_client
    try:
        async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get("/api/admin/streams")
    finally:
        app.dependency_overrides.clear()

    assert r.status_code == 401
