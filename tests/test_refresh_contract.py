"""§7 fixes — refresh validation hardening (A-1/A-2) + error contract (M-1)."""
import json
import time
import uuid

import jwt
import pytest

from app.config import get_settings
from app.core import security
from app.core.security import create_client_refresh_token, create_refresh_token
from app.core.exceptions import NexoraException
from app.services.auth_service import AuthService
from app.services.client_auth_service import ClientAuthService
from app.main import nexora_exception_handler

pytestmark = pytest.mark.asyncio
settings = get_settings()


def _craft(**claims) -> str:
    base = {"jti": str(uuid.uuid4()), "sub": str(uuid.uuid4()),
            "iat": int(time.time()), "exp": int(time.time()) + 3600}
    base.update(claims)
    return jwt.encode(base, settings.secret_key, algorithm=settings.jwt_algorithm)


# ── A-1: no 500 on cross / incomplete token ──────────────────────────────────

async def test_admin_refresh_rejects_client_token(db_session, redis_client):
    tok, _ = create_client_refresh_token(str(uuid.uuid4()))
    with pytest.raises(NexoraException) as ei:
        await AuthService(db_session, redis_client).refresh(tok)
    assert ei.value.status_code == 401


async def test_client_refresh_rejects_admin_token(db_session, redis_client):
    tok, _ = create_refresh_token(str(uuid.uuid4()), "admin")
    with pytest.raises(NexoraException) as ei:
        await ClientAuthService(db_session, redis_client).refresh(tok)
    assert ei.value.status_code == 401


async def test_admin_refresh_malformed_no_role_returns_401_not_500(db_session, redis_client, monkeypatch):
    monkeypatch.setattr(security.settings, "jwt_require_aud", False)
    tok = _craft(type="admin_refresh", aud="nexora-admin", iss="nexora-api")  # no role
    with pytest.raises(NexoraException) as ei:
        await AuthService(db_session, redis_client).refresh(tok)
    assert ei.value.status_code == 401  # the A-1 bug would have been a 500


# ── A-2: strict aud/iss/type in refresh ──────────────────────────────────────

async def test_admin_refresh_wrong_aud_strict_returns_401(db_session, redis_client, monkeypatch):
    monkeypatch.setattr(security.settings, "jwt_require_aud", True)
    tok = _craft(type="admin_refresh", aud="nexora-client", iss="nexora-api", role="admin")
    with pytest.raises(NexoraException) as ei:
        await AuthService(db_session, redis_client).refresh(tok)
    assert ei.value.status_code == 401
    monkeypatch.setattr(security.settings, "jwt_require_aud", False)


async def test_client_refresh_wrong_aud_strict_returns_401(db_session, redis_client, monkeypatch):
    monkeypatch.setattr(security.settings, "jwt_require_aud", True)
    tok = _craft(type="client_refresh", aud="nexora-admin", iss="nexora-api")
    with pytest.raises(NexoraException) as ei:
        await ClientAuthService(db_session, redis_client).refresh(tok)
    assert ei.value.status_code == 401
    monkeypatch.setattr(security.settings, "jwt_require_aud", False)


# ── M-1: error contract shape ────────────────────────────────────────────────

async def test_device_limit_409_shape():
    exc = NexoraException(status_code=409, detail={
        "reason_code": "DEVICE_LIMIT_REACHED",
        "message": "Límite de dispositivos alcanzado. Libera un dispositivo para continuar.",
    })
    resp = await nexora_exception_handler(None, exc)
    body = json.loads(resp.body)
    assert resp.status_code == 409
    assert body["success"] is False
    assert body["error"] == "Límite de dispositivos alcanzado. Libera un dispositivo para continuar."
    assert body["reason_code"] == "DEVICE_LIMIT_REACHED"


async def test_string_detail_shape_unchanged():
    resp = await nexora_exception_handler(None, NexoraException(status_code=403, detail="CHANNEL_NOT_INCLUDED"))
    body = json.loads(resp.body)
    assert body["success"] is False
    assert body["error"] == "CHANNEL_NOT_INCLUDED"
    assert body.get("reason_code") is None  # string detail → no reason_code key
