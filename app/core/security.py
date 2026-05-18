"""
Security — JWT con PyJWT + Argon2id via passlib.
Reemplaza python-jose (incompatible con Python 3.14 sin compilador Rust).
"""
import uuid
from datetime import datetime, timezone, timedelta

import jwt
from jwt.exceptions import InvalidTokenError
from passlib.context import CryptContext

from app.config import get_settings
from app.schemas.auth import TokenPayload

settings = get_settings()

# Argon2id — más seguro que bcrypt, resistente a GPU attacks
pwd_context = CryptContext(
    schemes=["argon2"],
    deprecated="auto",
    argon2__memory_cost=65536,   # 64 MB
    argon2__time_cost=3,
    argon2__parallelism=4,
    argon2__type="ID",           # Argon2id
)


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)


def _make_token(sub: str, role: str, token_type: str, expire_delta: timedelta) -> tuple[str, str]:
    jti = str(uuid.uuid4())
    now = datetime.now(timezone.utc)
    payload = {
        "sub":  sub,
        "jti":  jti,
        "role": role,
        "type": token_type,
        "iat":  int(now.timestamp()),
        "exp":  int((now + expire_delta).timestamp()),
    }
    token = jwt.encode(payload, settings.secret_key, algorithm=settings.jwt_algorithm)
    return token, jti


def create_access_token(user_id: str, role: str) -> tuple[str, str, int]:
    """Returns (token, jti, expires_in_seconds)."""
    delta = timedelta(minutes=settings.access_token_expire_minutes)
    token, jti = _make_token(user_id, role, "access", delta)
    return token, jti, settings.access_token_expire_minutes * 60


def create_refresh_token(user_id: str, role: str) -> tuple[str, str]:
    """Returns (token, jti)."""
    delta = timedelta(days=settings.refresh_token_expire_days)
    token, jti = _make_token(user_id, role, "refresh", delta)
    return token, jti


def decode_token(token: str) -> TokenPayload:
    """Raises jwt.InvalidTokenError on failure."""
    payload = jwt.decode(
        token,
        settings.secret_key,
        algorithms=[settings.jwt_algorithm],
    )
    return TokenPayload(**payload)


def create_client_access_token(subscriber_id: str) -> tuple[str, str, int]:
    """Returns (token, jti, expires_in_seconds). type='client_access'."""
    jti = str(uuid.uuid4())
    ttl = settings.client_access_token_expire_hours * 3600
    now = datetime.now(timezone.utc)
    payload = {
        "sub": subscriber_id,
        "jti": jti,
        "type": "client_access",
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=ttl)).timestamp()),
    }
    token = jwt.encode(payload, settings.secret_key, algorithm=settings.jwt_algorithm)
    return token, jti, ttl


def create_client_refresh_token(subscriber_id: str) -> tuple[str, str]:
    """Returns (token, jti). type='client_refresh'."""
    jti = str(uuid.uuid4())
    ttl = settings.client_refresh_token_expire_days * 86400
    now = datetime.now(timezone.utc)
    payload = {
        "sub": subscriber_id,
        "jti": jti,
        "type": "client_refresh",
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=ttl)).timestamp()),
    }
    token = jwt.encode(payload, settings.secret_key, algorithm=settings.jwt_algorithm)
    return token, jti


def decode_client_token(token: str) -> dict:
    """Decode a client token. Raises jwt.InvalidTokenError on failure."""
    return jwt.decode(token, settings.secret_key, algorithms=[settings.jwt_algorithm])
