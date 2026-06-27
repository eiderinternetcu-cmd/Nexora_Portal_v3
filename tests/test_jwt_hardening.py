"""FASE 4 — JWT hardening: aud/iss/type strict per surface (flag-gated)."""
import time
import uuid

import jwt
import pytest
from fastapi.security import HTTPAuthorizationCredentials

from app.config import get_settings
from app.core import security
from app.core.dependencies import _get_token_payload, get_client_token_payload
from app.core.exceptions import NexoraException
from app.redis_client import key_client

pytestmark = pytest.mark.asyncio
settings = get_settings()


def _creds(token: str) -> HTTPAuthorizationCredentials:
    return HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)


def _raw(**claims) -> str:
    base = {"jti": str(uuid.uuid4()), "sub": str(uuid.uuid4()),
            "iat": int(time.time()), "exp": int(time.time()) + 3600}
    base.update(claims)
    return jwt.encode(base, settings.secret_key, algorithm=settings.jwt_algorithm)


# ── Token shape ──────────────────────────────────────────────────────────────

def test_client_token_contains_iss_aud_type_jti():
    token, jti, _ = security.create_client_access_token(str(uuid.uuid4()))
    claims = jwt.decode(token, settings.secret_key, algorithms=[settings.jwt_algorithm],
                        options={"verify_aud": False})
    assert claims["iss"] == "nexora-api"
    assert claims["aud"] == "nexora-client"
    assert claims["type"] == "client_access"
    assert claims["jti"] == jti
    assert claims["sub"] and claims["iat"] and claims["exp"]


def test_admin_token_contains_iss_aud_type_jti():
    token, jti, _ = security.create_access_token(str(uuid.uuid4()), "admin")
    claims = jwt.decode(token, settings.secret_key, algorithms=[settings.jwt_algorithm],
                        options={"verify_aud": False})
    assert claims["iss"] == "nexora-api"
    assert claims["aud"] == "nexora-admin"
    assert claims["type"] == "admin_access"
    assert claims["jti"] == jti


def test_refresh_token_uses_correct_type_and_audience():
    a_tok, _ = security.create_refresh_token(str(uuid.uuid4()), "admin")
    c_tok, _ = security.create_client_refresh_token(str(uuid.uuid4()))
    a = jwt.decode(a_tok, settings.secret_key, algorithms=[settings.jwt_algorithm], options={"verify_aud": False})
    c = jwt.decode(c_tok, settings.secret_key, algorithms=[settings.jwt_algorithm], options={"verify_aud": False})
    assert a["type"] == "admin_refresh" and a["aud"] == "nexora-admin"
    assert c["type"] == "client_refresh" and c["aud"] == "nexora-client"


def test_playback_token_type_reserved_for_fase_5():
    # Constants reserved; not yet wired into /stream (FASE 5).
    assert security.TYPE_PLAYBACK == "playback_token"
    assert security.AUD_PLAYBACK == "nexora-playback"


# ── Strict mode (jwt_require_aud=True) ───────────────────────────────────────

async def test_client_token_rejected_on_admin_route_when_jwt_require_aud_true(redis_client, monkeypatch):
    monkeypatch.setattr(security.settings, "jwt_require_aud", True)
    token, _, _ = security.create_client_access_token(str(uuid.uuid4()))
    with pytest.raises(NexoraException) as ei:
        await _get_token_payload(credentials=_creds(token), redis=redis_client)
    assert ei.value.status_code == 401


async def test_admin_token_rejected_on_client_route_when_jwt_require_aud_true(redis_client, monkeypatch):
    monkeypatch.setattr(security.settings, "jwt_require_aud", True)
    token, _, _ = security.create_access_token(str(uuid.uuid4()), "admin")
    with pytest.raises(NexoraException) as ei:
        await get_client_token_payload(credentials=_creds(token), redis=redis_client)
    assert ei.value.status_code == 401


async def test_missing_aud_rejected_when_jwt_require_aud_true(redis_client, monkeypatch):
    monkeypatch.setattr(security.settings, "jwt_require_aud", True)
    token = _raw(type="client_access")  # no aud, no iss
    with pytest.raises(NexoraException):
        await get_client_token_payload(credentials=_creds(token), redis=redis_client)


async def test_wrong_issuer_rejected_when_jwt_require_aud_true(redis_client, monkeypatch):
    monkeypatch.setattr(security.settings, "jwt_require_aud", True)
    token = _raw(type="client_access", aud="nexora-client", iss="evil-issuer")
    with pytest.raises(NexoraException):
        await get_client_token_payload(credentials=_creds(token), redis=redis_client)


async def test_wrong_type_rejected_when_jwt_require_aud_true(redis_client, monkeypatch):
    monkeypatch.setattr(security.settings, "jwt_require_aud", True)
    token = _raw(type="client_refresh", aud="nexora-client", iss="nexora-api")
    with pytest.raises(NexoraException):
        await get_client_token_payload(credentials=_creds(token), redis=redis_client)


# ── Compat mode (jwt_require_aud=False) ──────────────────────────────────────

async def test_legacy_token_allowed_when_jwt_require_aud_false(redis_client, monkeypatch):
    monkeypatch.setattr(security.settings, "jwt_require_aud", False)
    jti = str(uuid.uuid4())
    token = _raw(jti=jti, type="client_access")  # legacy: no aud/iss
    await redis_client.set(key_client(jti), "subscriber-x")  # allowlist
    claims = await get_client_token_payload(credentials=_creds(token), redis=redis_client)
    assert claims["type"] == "client_access"
