"""FASE 5 — signed playback URL + /stream/* validation (anti-hotlink)."""
import time
import uuid

import jwt
import pytest

from app.config import get_settings
from app.core.exceptions import NexoraException
from app.api.client import playback as pb_mod
from app.services.stream_auth_service import StreamAuthService

pytestmark = pytest.mark.asyncio
settings = get_settings()


async def _authorize(world, redis, *, channel_key="canal-1", stream_key="K1", node="co-main"):
    svc = StreamAuthService(world["session"], redis)
    result = await svc.authorize(
        subscriber_id=world["subscriber"].id,
        device_id_str=world["device"].device_id,
        channel_id=stream_key,
        channel_key=channel_key,
        node=node,
    )
    return svc, result


def _craft_playback(**over) -> str:
    claims = {
        "sub": str(uuid.uuid4()), "dev": str(uuid.uuid4()), "ses": str(uuid.uuid4()),
        "chn": "canal-1", "sk": "K1", "node": "co-main",
        "type": "playback_token", "aud": "nexora-playback", "iss": "nexora-api",
        "jti": str(uuid.uuid4()), "iat": int(time.time()), "exp": int(time.time()) + 60,
    }
    claims.update(over)
    return jwt.encode(claims, settings.secret_key, algorithm=settings.jwt_algorithm)


# ── Token shape ──────────────────────────────────────────────────────────────

async def test_playback_token_contains_iss_aud_type_jti(entitlement_world, redis_client):
    _, result = await _authorize(entitlement_world, redis_client)
    claims = jwt.decode(result.token, settings.secret_key,
                        algorithms=[settings.jwt_algorithm], options={"verify_aud": False})
    assert claims["type"] == "playback_token"
    assert claims["aud"] == "nexora-playback"
    assert claims["iss"] == "nexora-api"
    assert claims["jti"] and claims["sub"]
    assert claims["sk"] == "K1" and claims["chn"] == "canal-1" and claims["node"] == "co-main"


# ── playback_url shaping ─────────────────────────────────────────────────────

def test_playback_url_contains_token_when_signed_url_enforced(monkeypatch):
    monkeypatch.setattr(pb_mod.settings, "signed_url_enforce", True)
    url = pb_mod._maybe_sign("https://nexoraplay.net/stream/co-main/K1/index.m3u8", "TOK123")
    assert "?token=TOK123" in url


def test_playback_url_same_origin_https_no_origin_ip(monkeypatch):
    base = "https://nexoraplay.net/stream/co-main/TeleNostalgia/index.m3u8"
    monkeypatch.setattr(pb_mod.settings, "signed_url_enforce", True)
    url = pb_mod._maybe_sign(base, "TOK")
    assert url.startswith("https://nexoraplay.net/stream/")
    assert "38.210" not in url and "181.78" not in url  # never expose origin IP


async def test_signed_url_enforce_false_does_not_break_current_playback(entitlement_world, redis_client, monkeypatch):
    monkeypatch.setattr(pb_mod.settings, "signed_url_enforce", False)
    # base URL is returned unchanged (no token appended); token still in body
    url = pb_mod._maybe_sign("https://nexoraplay.net/stream/co-main/K1/index.m3u8", "TOK")
    assert "token=" not in url
    _, result = await _authorize(entitlement_world, redis_client)
    assert result.token  # authorize still issues a token


# ── /stream/* validation (validate_stream_request) ───────────────────────────

async def test_stream_auth_validate_allows_valid_token(entitlement_world, redis_client):
    svc, result = await _authorize(entitlement_world, redis_client)
    out = await svc.validate_stream_request(result.token, stream_key="K1", node="co-main")
    assert out["stream_key"] == "K1"


async def test_stream_auth_validate_denies_missing_token(entitlement_world, redis_client):
    svc = StreamAuthService(entitlement_world["session"], redis_client)
    with pytest.raises(NexoraException) as ei:
        await svc.validate_stream_request(None)
    assert ei.value.status_code == 401


async def test_stream_auth_validate_denies_expired_token(entitlement_world, redis_client):
    svc = StreamAuthService(entitlement_world["session"], redis_client)
    token = _craft_playback(exp=int(time.time()) - 10)
    with pytest.raises(NexoraException) as ei:
        await svc.validate_stream_request(token, stream_key="K1")
    assert ei.value.status_code == 401


async def test_stream_auth_validate_denies_wrong_audience(entitlement_world, redis_client):
    svc = StreamAuthService(entitlement_world["session"], redis_client)
    token = _craft_playback(aud="nexora-client")
    with pytest.raises(NexoraException) as ei:
        await svc.validate_stream_request(token, stream_key="K1")
    assert ei.value.status_code == 401


async def test_stream_auth_validate_denies_wrong_type(entitlement_world, redis_client):
    svc = StreamAuthService(entitlement_world["session"], redis_client)
    token = _craft_playback(type="client_access")
    with pytest.raises(NexoraException) as ei:
        await svc.validate_stream_request(token, stream_key="K1")
    assert ei.value.status_code == 401


async def test_stream_auth_validate_denies_revoked_session(entitlement_world, redis_client):
    svc, result = await _authorize(entitlement_world, redis_client)
    await svc._sessions.revoke_iptv_session(result.session_jti)
    # Revoking the session also purges its playback tokens, so the request is
    # denied — as 401 (token purged) or 403 (session invalid); both are a deny.
    with pytest.raises(NexoraException) as ei:
        await svc.validate_stream_request(result.token, stream_key="K1", node="co-main")
    assert ei.value.status_code in (401, 403)


async def test_stream_auth_validate_denies_stream_key_mismatch(entitlement_world, redis_client):
    svc, result = await _authorize(entitlement_world, redis_client)
    with pytest.raises(NexoraException) as ei:
        await svc.validate_stream_request(result.token, stream_key="OTHER-KEY", node="co-main")
    assert ei.value.status_code == 403
