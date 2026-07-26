"""PROD-2C — GET /api/client/playback/{channel_id} (token reissue) hardening.

Two bugs fixed here:
  1. The reissue returned an UNSIGNED playback_url even with SIGNED_URL_ENFORCE on
     (only /authorize went through _maybe_sign). It only worked because the
     segment grant covered the gap.
  2. The reissued token was minted without 'chn', 'node' and 'cip', so every
     reissued token (a player renews every ~45s → most tokens in flight) was
     exempt from node binding and from IP binding — the check requires a truthy
     'cip'. That would have silently gutted PLAYBACK_IP_BINDING_MODE=strict.

Defaults must keep behaving exactly as before; the optional entitlement recheck
sits behind playback_reissue_entitlement_check (default False).
"""
import httpx
import jwt
import pytest
from httpx import ASGITransport
from sqlalchemy import delete

from app.config import get_settings
from app.core.exceptions import NexoraException
from app.api.client import playback as pb_mod
from app.services.stream_auth_service import StreamAuthService
from app.services import stream_auth_service as sas_mod

pytestmark = pytest.mark.asyncio
settings = get_settings()

IP = "1.2.3.4"
OTHER_IP = "9.9.9.9"
NODE = "ec-main"          # entitlement_world channels use the model default
STREAM_KEY = "K1"
CHANNEL_KEY = "canal-1"


def _claims(token: str) -> dict:
    return jwt.decode(
        token, settings.secret_key,
        algorithms=[settings.jwt_algorithm], options={"verify_aud": False},
    )


async def _open_session(world, redis, *, ip=IP, device_id=None):
    """authorize() first — the reissue requires an active ZSET slot + DB session."""
    svc = StreamAuthService(world["session"], redis)
    res = await svc.authorize(
        subscriber_id=world["subscriber"].id,
        device_id_str=device_id or world["device"].device_id,
        channel_id=STREAM_KEY,
        ip=ip,
        channel_key=CHANNEL_KEY,
        node=NODE,
    )
    return svc, res


async def _reissue(svc, world, *, ip=IP, node=NODE, channel_key=CHANNEL_KEY, device_id=None):
    return await svc.create_token(
        subscriber_id=world["subscriber"].id,
        device_id_str=device_id or world["device"].device_id,
        channel_id=STREAM_KEY,
        channel_key=channel_key,
        node=node,
        ip=ip,
    )


async def _endpoint_device(world) -> str:
    """The endpoint enforces device_id min_length=6; the fixture device is 'dev-1'."""
    from app.models.device import Device
    dev = Device(
        subscriber_id=world["subscriber"].id, device_id="dev-web-01",
        device_type="web_player", is_blocked=False,
    )
    world["session"].add(dev)
    await world["session"].commit()
    return dev.device_id


# ── BUG 2: reissued token claims ─────────────────────────────────────────────

async def test_reissued_token_carries_chn_node_and_cip(entitlement_world, redis_client):
    svc, _ = await _open_session(entitlement_world, redis_client)
    res = await _reissue(svc, entitlement_world)
    c = _claims(res.token)
    assert c["chn"] == CHANNEL_KEY
    assert c["sk"] == STREAM_KEY
    assert c["node"] == NODE
    assert c["cip"], "reissued token must carry the hashed client IP"


async def test_reissued_token_claims_match_authorize(entitlement_world, redis_client):
    svc, authorized = await _open_session(entitlement_world, redis_client)
    reissued = await _reissue(svc, entitlement_world)
    a, r = _claims(authorized.token), _claims(reissued.token)
    for claim in ("chn", "sk", "node", "cip", "sub", "dev", "ses", "aud", "iss", "type"):
        assert a[claim] == r[claim], f"claim {claim} differs between authorize and reissue"


async def test_reissued_token_defaults_keep_legacy_shape(entitlement_world, redis_client):
    """STB calls create_token(sub, dev, channel_id) positionally — unchanged."""
    svc, _ = await _open_session(entitlement_world, redis_client)
    res = await svc.create_token(
        entitlement_world["subscriber"].id,
        entitlement_world["device"].device_id,
        STREAM_KEY,
    )
    c = _claims(res.token)
    assert c["sk"] == STREAM_KEY
    assert c["chn"] is None and c["node"] is None and c["cip"] is None


# ── BUG 2 consequence: reissued tokens honour IP / node binding ───────────────

async def test_reissued_token_rejected_from_other_ip_strict(entitlement_world, redis_client, monkeypatch):
    monkeypatch.setattr(sas_mod.settings, "playback_ip_binding_mode", "strict")
    svc, _ = await _open_session(entitlement_world, redis_client)
    res = await _reissue(svc, entitlement_world, ip=IP)
    with pytest.raises(NexoraException) as ei:
        await svc.validate_stream_request(
            res.token, stream_key=STREAM_KEY, node=NODE, client_ip=OTHER_IP
        )
    assert ei.value.status_code == 403


async def test_reissued_token_allowed_from_same_ip_strict(entitlement_world, redis_client, monkeypatch):
    monkeypatch.setattr(sas_mod.settings, "playback_ip_binding_mode", "strict")
    svc, _ = await _open_session(entitlement_world, redis_client)
    res = await _reissue(svc, entitlement_world, ip=IP)
    out = await svc.validate_stream_request(
        res.token, stream_key=STREAM_KEY, node=NODE, client_ip=IP
    )
    assert out["stream_key"] == STREAM_KEY
    assert out["channel_id"] == CHANNEL_KEY


async def test_reissued_token_rejected_on_other_node(entitlement_world, redis_client):
    svc, _ = await _open_session(entitlement_world, redis_client)
    res = await _reissue(svc, entitlement_world)
    with pytest.raises(NexoraException) as ei:
        await svc.validate_stream_request(res.token, stream_key=STREAM_KEY, node="co-main")
    assert ei.value.status_code == 403


async def test_reissued_token_ipbinding_off_still_allows_other_ip(entitlement_world, redis_client, monkeypatch):
    """Default flag state: cip present but binding off → no behavior change."""
    monkeypatch.setattr(sas_mod.settings, "playback_ip_binding_mode", "off")
    svc, _ = await _open_session(entitlement_world, redis_client)
    res = await _reissue(svc, entitlement_world, ip=IP)
    out = await svc.validate_stream_request(
        res.token, stream_key=STREAM_KEY, node=NODE, client_ip=OTHER_IP
    )
    assert out["stream_key"] == STREAM_KEY


# ── Optional entitlement recheck (flagged, default OFF) ──────────────────────

async def _drop_channel_from_plan(world):
    from app.models.plan_channel import PlanChannel
    await world["session"].execute(
        delete(PlanChannel).where(PlanChannel.plan_id == world["plan"].id)
    )
    await world["session"].commit()


async def test_reissue_does_not_recheck_entitlement_by_default(entitlement_world, redis_client, monkeypatch):
    monkeypatch.setattr(sas_mod.settings, "playback_reissue_entitlement_check", False)
    monkeypatch.setattr(sas_mod.settings, "entitlement_enforce", True)
    svc, _ = await _open_session(entitlement_world, redis_client)
    await _drop_channel_from_plan(entitlement_world)
    res = await _reissue(svc, entitlement_world)  # legacy: session survives the plan change
    assert res.token


async def test_reissue_rechecks_entitlement_when_flag_on(entitlement_world, redis_client, monkeypatch):
    monkeypatch.setattr(sas_mod.settings, "playback_reissue_entitlement_check", True)
    monkeypatch.setattr(sas_mod.settings, "entitlement_enforce", True)
    svc, _ = await _open_session(entitlement_world, redis_client)
    await _drop_channel_from_plan(entitlement_world)
    with pytest.raises(NexoraException) as ei:
        await _reissue(svc, entitlement_world)
    assert ei.value.status_code == 403
    assert ei.value.detail == "CHANNEL_NOT_INCLUDED"


async def test_reissue_recheck_allows_entitled_channel(entitlement_world, redis_client, monkeypatch):
    monkeypatch.setattr(sas_mod.settings, "playback_reissue_entitlement_check", True)
    monkeypatch.setattr(sas_mod.settings, "entitlement_enforce", True)
    svc, _ = await _open_session(entitlement_world, redis_client)
    res = await _reissue(svc, entitlement_world)  # canal-1 IS in the plan
    assert res.token


# ── BUG 1: the endpoint signs playback_url like /authorize ───────────────────

async def _app(world, redis):
    from app.main import app
    from app.database import get_db
    from app.redis_client import get_redis
    from app.core.dependencies import get_current_subscriber

    app.dependency_overrides[get_db] = lambda: world["session"]
    app.dependency_overrides[get_redis] = lambda: redis
    app.dependency_overrides[get_current_subscriber] = lambda: world["subscriber"]
    return app


async def _get_reissue(world, redis, device_id, *, ip=IP):
    app = await _app(world, redis)
    try:
        async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get(
                f"/api/client/playback/{CHANNEL_KEY}",
                params={"device_id": device_id},
                headers={"X-Forwarded-For": ip},
            )
        return r
    finally:
        app.dependency_overrides.clear()


async def test_reissue_endpoint_signs_url_when_enforced(entitlement_world, redis_client, monkeypatch):
    monkeypatch.setattr(pb_mod.settings, "signed_url_enforce", True)
    entitlement_world["ch_in"].source_url = "https://nexoraplay.net/stream/ec-main/K1/index.m3u8"
    dev = await _endpoint_device(entitlement_world)
    await _open_session(entitlement_world, redis_client, device_id=dev)
    await entitlement_world["session"].commit()

    r = await _get_reissue(entitlement_world, redis_client, dev)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["playback_url"], "channel has a source_url → a URL must come back"
    assert f"token={body['token']}" in body["playback_url"]


async def test_reissue_endpoint_url_unsigned_when_flag_off(entitlement_world, redis_client, monkeypatch):
    """Default state: URL unchanged, token still travels in the body."""
    monkeypatch.setattr(pb_mod.settings, "signed_url_enforce", False)
    entitlement_world["ch_in"].source_url = "https://nexoraplay.net/stream/ec-main/K1/index.m3u8"
    dev = await _endpoint_device(entitlement_world)
    await _open_session(entitlement_world, redis_client, device_id=dev)
    await entitlement_world["session"].commit()

    r = await _get_reissue(entitlement_world, redis_client, dev)
    assert r.status_code == 200, r.text
    body = r.json()
    assert "token=" not in (body["playback_url"] or "")
    assert body["token"]


async def test_reissue_endpoint_binds_token_to_forwarded_ip(entitlement_world, redis_client, monkeypatch):
    """The endpoint must read the client IP the same way /authorize does."""
    monkeypatch.setattr(sas_mod.settings, "playback_ip_binding_mode", "strict")
    dev = await _endpoint_device(entitlement_world)
    await _open_session(entitlement_world, redis_client, device_id=dev)
    await entitlement_world["session"].commit()

    r = await _get_reissue(entitlement_world, redis_client, dev, ip=IP)
    assert r.status_code == 200, r.text
    token = r.json()["token"]
    from app.core.security import hash_ip
    assert _claims(token)["cip"] == hash_ip(IP)

    svc = StreamAuthService(entitlement_world["session"], redis_client)
    with pytest.raises(NexoraException) as ei:
        await svc.validate_stream_request(
            token, stream_key=STREAM_KEY, node=NODE, client_ip=OTHER_IP
        )
    assert ei.value.status_code == 403
