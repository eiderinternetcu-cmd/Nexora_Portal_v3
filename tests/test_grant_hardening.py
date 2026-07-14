"""M1 — segment grant hardening.

1. Absolute max-lifetime cap: a grant dies once its age since the first seed
   reaches stream_grant_max_lifetime_seconds, regardless of renewal — so a revoked
   session's stream cannot be kept alive forever by tokenless segments.
2. Token fallback: a present-but-expired/invalid token still passes at the edge if
   a valid grant already exists for the same node+stream+IP (continuity), unless
   stream_grant_token_fallback is off.
"""
import httpx
import pytest
from httpx import ASGITransport

from app.core.security import hash_ip
from app.redis_client import key_stream_grant
from app.services.stream_auth_service import StreamAuthService
from app.services import stream_auth_service as sas_mod
from app.api.internal import stream_auth as endp_mod

pytestmark = pytest.mark.asyncio

IP = "1.2.3.4"


async def _seed(world, redis, *, ip=IP, stream_key="K1", node="co-main", channel_key="canal-1"):
    svc = StreamAuthService(world["session"], redis)
    res = await svc.authorize(
        subscriber_id=world["subscriber"].id,
        device_id_str=world["device"].device_id,
        channel_id=stream_key, ip=ip, channel_key=channel_key, node=node,
    )
    out = await svc.validate_stream_request(res.token, stream_key=stream_key, node=node, client_ip=ip)
    await svc.grant_stream_access(node, stream_key, hash_ip(ip), out["session_id"])
    return svc, res


# ── Absolute max-lifetime cap ────────────────────────────────────────────────

async def test_grant_dies_after_max_lifetime(entitlement_world, redis_client, monkeypatch):
    monkeypatch.setattr(sas_mod.settings, "stream_grant_max_lifetime_seconds", 60)
    svc, _ = await _seed(entitlement_world, redis_client)
    key = key_stream_grant("co-main", "K1", hash_ip(IP))
    # Rewrite the seed epoch to 120s ago → age (120) >= cap (60) → grant must die.
    val = await redis_client.get(key)
    sess = str(val).rsplit("|", 1)[0]
    import time
    await redis_client.set(key, f"{sess}|{int(time.time()) - 120}")
    assert await svc.check_stream_grant("co-main", "K1", hash_ip(IP)) is False
    assert await redis_client.exists(key) == 0  # deleted


async def test_grant_survives_within_max_lifetime(entitlement_world, redis_client, monkeypatch):
    monkeypatch.setattr(sas_mod.settings, "stream_grant_max_lifetime_seconds", 3600)
    svc, _ = await _seed(entitlement_world, redis_client)
    assert await svc.check_stream_grant("co-main", "K1", hash_ip(IP)) is True


async def test_grant_unbounded_when_cap_zero(entitlement_world, redis_client, monkeypatch):
    monkeypatch.setattr(sas_mod.settings, "stream_grant_max_lifetime_seconds", 0)  # default
    svc, _ = await _seed(entitlement_world, redis_client)
    key = key_stream_grant("co-main", "K1", hash_ip(IP))
    import time
    val = await redis_client.get(key)
    sess = str(val).rsplit("|", 1)[0]
    await redis_client.set(key, f"{sess}|{int(time.time()) - 99999}")  # ancient
    assert await svc.check_stream_grant("co-main", "K1", hash_ip(IP)) is True  # no cap → still valid


# ── Token fallback at the edge endpoint ──────────────────────────────────────

async def _client(world, redis):
    from app.main import app
    from app.database import get_db
    from app.redis_client import get_redis
    app.dependency_overrides[get_db] = lambda: world["session"]
    app.dependency_overrides[get_redis] = lambda: redis
    return app


async def test_expired_token_falls_back_to_grant(entitlement_world, redis_client, monkeypatch):
    monkeypatch.setattr(endp_mod.settings, "stream_grant_token_fallback", True)
    await _seed(entitlement_world, redis_client)  # seeds a valid grant for K1/co-main/IP
    await entitlement_world["session"].commit()
    app = await _client(entitlement_world, redis_client)
    try:
        async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            # invalid/expired token but a valid grant exists → 200 (continuity)
            r = await c.get("/internal/stream-auth/validate",
                            params={"token": "not.a.valid.jwt", "stream_key": "K1", "node": "co-main"},
                            headers={"X-Real-IP": IP})
            assert r.status_code == 200, r.text
    finally:
        app.dependency_overrides.clear()


async def test_fallback_off_rejects_expired_token(entitlement_world, redis_client, monkeypatch):
    monkeypatch.setattr(endp_mod.settings, "stream_grant_token_fallback", False)
    await _seed(entitlement_world, redis_client)
    await entitlement_world["session"].commit()
    app = await _client(entitlement_world, redis_client)
    try:
        async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get("/internal/stream-auth/validate",
                            params={"token": "not.a.valid.jwt", "stream_key": "K1", "node": "co-main"},
                            headers={"X-Real-IP": IP})
            assert r.status_code == 401  # fallback off → bad token denied
    finally:
        app.dependency_overrides.clear()


async def test_bad_token_without_grant_denied(entitlement_world, redis_client, monkeypatch):
    monkeypatch.setattr(endp_mod.settings, "stream_grant_token_fallback", True)
    await entitlement_world["session"].commit()  # NO grant seeded
    app = await _client(entitlement_world, redis_client)
    try:
        async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get("/internal/stream-auth/validate",
                            params={"token": "not.a.valid.jwt", "stream_key": "K1", "node": "co-main"},
                            headers={"X-Real-IP": IP})
            assert r.status_code in (401, 403)  # no grant → deny even with fallback on
    finally:
        app.dependency_overrides.clear()
