"""PRE-PROD hardening — HLS segment grant (C-PROD-1) + token IP-binding (C-PROD-2)."""
import logging

import httpx
import pytest
from httpx import ASGITransport

from app.core.exceptions import NexoraException
from app.core.security import hash_ip
from app.services.stream_auth_service import StreamAuthService
from app.services import stream_auth_service as sas_mod

pytestmark = pytest.mark.asyncio

IP = "1.2.3.4"
OTHER_IP = "9.9.9.9"


async def _authorize(world, redis, *, ip=IP, stream_key="K1", node="co-main", channel_key="canal-1"):
    svc = StreamAuthService(world["session"], redis)
    res = await svc.authorize(
        subscriber_id=world["subscriber"].id,
        device_id_str=world["device"].device_id,
        channel_id=stream_key,
        ip=ip,
        channel_key=channel_key,
        node=node,
    )
    return svc, res


# ── C-PROD-1: segment grant cache ─────────────────────────────────────────────

async def test_manifest_seeds_grant_and_segment_passes(entitlement_world, redis_client):
    svc, res = await _authorize(entitlement_world, redis_client)
    out = await svc.validate_stream_request(res.token, stream_key="K1", node="co-main", client_ip=IP)
    await svc.grant_stream_access("co-main", "K1", hash_ip(IP), out["session_id"])
    assert await svc.check_stream_grant("co-main", "K1", hash_ip(IP)) is True  # segment allowed


async def test_segment_without_grant_denied(entitlement_world, redis_client):
    svc = StreamAuthService(entitlement_world["session"], redis_client)
    assert await svc.check_stream_grant("co-main", "K1", hash_ip(IP)) is False  # also = expired


async def test_segment_other_stream_key_denied(entitlement_world, redis_client):
    svc = StreamAuthService(entitlement_world["session"], redis_client)
    await svc.grant_stream_access("co-main", "K1", hash_ip(IP), "ses")
    assert await svc.check_stream_grant("co-main", "K99", hash_ip(IP)) is False


async def test_segment_other_node_denied(entitlement_world, redis_client):
    svc = StreamAuthService(entitlement_world["session"], redis_client)
    await svc.grant_stream_access("co-main", "K1", hash_ip(IP), "ses")
    assert await svc.check_stream_grant("ec-main", "K1", hash_ip(IP)) is False


async def test_segment_other_ip_denied(entitlement_world, redis_client):
    svc = StreamAuthService(entitlement_world["session"], redis_client)
    await svc.grant_stream_access("co-main", "K1", hash_ip(IP), "ses")
    assert await svc.check_stream_grant("co-main", "K1", hash_ip(OTHER_IP)) is False


async def test_segment_grant_expiry_simulated(entitlement_world, redis_client):
    svc = StreamAuthService(entitlement_world["session"], redis_client)
    await svc.grant_stream_access("co-main", "K1", hash_ip(IP), "ses")
    from app.redis_client import key_stream_grant
    await redis_client.delete(key_stream_grant("co-main", "K1", hash_ip(IP)))  # emulate TTL expiry
    assert await svc.check_stream_grant("co-main", "K1", hash_ip(IP)) is False


# ── C-PROD-2: IP binding ──────────────────────────────────────────────────────

async def test_ipbinding_strict_same_ip_allows(entitlement_world, redis_client, monkeypatch):
    monkeypatch.setattr(sas_mod.settings, "playback_ip_binding_mode", "strict")
    svc, res = await _authorize(entitlement_world, redis_client, ip=IP)
    out = await svc.validate_stream_request(res.token, stream_key="K1", node="co-main", client_ip=IP)
    assert out["stream_key"] == "K1"


async def test_ipbinding_strict_diff_ip_403(entitlement_world, redis_client, monkeypatch):
    monkeypatch.setattr(sas_mod.settings, "playback_ip_binding_mode", "strict")
    svc, res = await _authorize(entitlement_world, redis_client, ip=IP)
    with pytest.raises(NexoraException) as ei:
        await svc.validate_stream_request(res.token, stream_key="K1", node="co-main", client_ip=OTHER_IP)
    assert ei.value.status_code == 403


async def test_ipbinding_soft_diff_ip_allows_with_warning(entitlement_world, redis_client, monkeypatch, caplog):
    monkeypatch.setattr(sas_mod.settings, "playback_ip_binding_mode", "soft")
    svc, res = await _authorize(entitlement_world, redis_client, ip=IP)
    with caplog.at_level(logging.WARNING, logger="app.services.stream_auth_service"):
        out = await svc.validate_stream_request(res.token, stream_key="K1", node="co-main", client_ip=OTHER_IP)
    assert out["stream_key"] == "K1"
    assert any("playback IP mismatch" in r.getMessage() for r in caplog.records)


async def test_ipbinding_off_diff_ip_allows(entitlement_world, redis_client, monkeypatch):
    monkeypatch.setattr(sas_mod.settings, "playback_ip_binding_mode", "off")
    svc, res = await _authorize(entitlement_world, redis_client, ip=IP)
    out = await svc.validate_stream_request(res.token, stream_key="K1", node="co-main", client_ip=OTHER_IP)
    assert out["stream_key"] == "K1"


async def test_ipbinding_token_without_cip_skips(entitlement_world, redis_client, monkeypatch):
    monkeypatch.setattr(sas_mod.settings, "playback_ip_binding_mode", "strict")
    svc, res = await _authorize(entitlement_world, redis_client, ip=None)  # no cip in token
    out = await svc.validate_stream_request(res.token, stream_key="K1", node="co-main", client_ip=IP)
    assert out["stream_key"] == "K1"  # cannot enforce without cip → allow


# ── Endpoint E2E (auth_request gate): manifest → segment → deny ───────────────

async def test_endpoint_manifest_then_segment_flow(entitlement_world, redis_client):
    from app.main import app
    from app.database import get_db
    from app.redis_client import get_redis

    svc, res = await _authorize(entitlement_world, redis_client, ip=IP)
    await entitlement_world["session"].commit()

    app.dependency_overrides[get_db] = lambda: entitlement_world["session"]
    app.dependency_overrides[get_redis] = lambda: redis_client
    try:
        async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            H = {"X-Real-IP": IP}
            # manifest (with token) → 200 + seeds grant
            r = await c.get("/internal/stream-auth/validate",
                            params={"token": res.token, "stream_key": "K1", "node": "co-main"}, headers=H)
            assert r.status_code == 200, r.text
            # segment (no token) same stream + IP → 200 (grant)
            r = await c.get("/internal/stream-auth/validate",
                            params={"stream_key": "K1", "node": "co-main"}, headers=H)
            assert r.status_code == 200, r.text
            # segment of another stream (no token) → 401
            r = await c.get("/internal/stream-auth/validate",
                            params={"stream_key": "K99", "node": "co-main"}, headers=H)
            assert r.status_code == 401
            # segment from another IP → 401
            r = await c.get("/internal/stream-auth/validate",
                            params={"stream_key": "K1", "node": "co-main"}, headers={"X-Real-IP": OTHER_IP})
            assert r.status_code == 401
    finally:
        app.dependency_overrides.clear()
