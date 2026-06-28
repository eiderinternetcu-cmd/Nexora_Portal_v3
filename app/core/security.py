"""
Security — JWT con PyJWT + Argon2id via passlib.
Reemplaza python-jose (incompatible con Python 3.14 sin compilador Rust).
"""
import hashlib
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


def hash_ip(ip: str | None) -> str:
    """Non-reversible, salted hash of a client IP (for token binding / grant keys).
    Never store the raw IP. Empty/None → stable hash of empty (callers should
    refuse correlation when the IP is unknown)."""
    return hashlib.sha256(f"{settings.jwt_issuer}:{ip or ''}".encode()).hexdigest()[:32]


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)


# ── JWT identity constants ──────────────────────────────────────────────────
# Audiences (one per surface) + token types. Issuer comes from settings.jwt_issuer.
AUD_ADMIN = "nexora-admin"
AUD_CLIENT = "nexora-client"
AUD_STB = "nexora-stb"
AUD_PLAYBACK = "nexora-playback"

TYPE_ADMIN_ACCESS = "admin_access"
TYPE_ADMIN_REFRESH = "admin_refresh"
TYPE_CLIENT_ACCESS = "client_access"
TYPE_CLIENT_REFRESH = "client_refresh"
TYPE_STB_ACCESS = "stb_access"
TYPE_PLAYBACK = "playback_token"   # reserved for FASE 5 (current playback flow still uses "playback")

# Types accepted per surface while jwt_require_aud is False (legacy compatibility).
LEGACY_ADMIN_ACCESS = {"admin_access", "access"}
LEGACY_ADMIN_REFRESH = {"admin_refresh", "refresh"}
LEGACY_CLIENT_ACCESS = {"client_access"}
LEGACY_CLIENT_REFRESH = {"client_refresh"}


def _encode(sub: str, jti: str, token_type: str, aud: str, ttl_seconds: int,
            extra: dict | None = None) -> str:
    """Build a JWT with the mandatory claims: sub, jti, type, aud, iss, iat, exp."""
    now = datetime.now(timezone.utc)
    payload = {
        "sub": sub,
        "jti": jti,
        "type": token_type,
        "aud": aud,
        "iss": settings.jwt_issuer,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=ttl_seconds)).timestamp()),
    }
    if extra:
        payload.update(extra)
    return jwt.encode(payload, settings.secret_key, algorithm=settings.jwt_algorithm)


# ── Admin tokens ────────────────────────────────────────────────────────────

def create_access_token(user_id: str, role: str) -> tuple[str, str, int]:
    """Admin access token (type=admin_access, aud=nexora-admin). Returns (token, jti, ttl)."""
    jti = str(uuid.uuid4())
    ttl = settings.access_token_expire_minutes * 60
    token = _encode(user_id, jti, TYPE_ADMIN_ACCESS, AUD_ADMIN, ttl, {"role": role})
    return token, jti, ttl


def create_refresh_token(user_id: str, role: str) -> tuple[str, str]:
    """Admin refresh token (type=admin_refresh, aud=nexora-admin). Returns (token, jti)."""
    jti = str(uuid.uuid4())
    ttl = settings.refresh_token_expire_days * 86400
    token = _encode(user_id, jti, TYPE_ADMIN_REFRESH, AUD_ADMIN, ttl, {"role": role})
    return token, jti


# ── Client tokens ───────────────────────────────────────────────────────────

def create_client_access_token(subscriber_id: str) -> tuple[str, str, int]:
    """Client access token (type=client_access, aud=nexora-client). Returns (token, jti, ttl)."""
    jti = str(uuid.uuid4())
    ttl = settings.client_access_token_expire_hours * 3600
    token = _encode(subscriber_id, jti, TYPE_CLIENT_ACCESS, AUD_CLIENT, ttl)
    return token, jti, ttl


def create_client_refresh_token(subscriber_id: str) -> tuple[str, str]:
    """Client refresh token (type=client_refresh, aud=nexora-client). Returns (token, jti)."""
    jti = str(uuid.uuid4())
    ttl = settings.client_refresh_token_expire_days * 86400
    token = _encode(subscriber_id, jti, TYPE_CLIENT_REFRESH, AUD_CLIENT, ttl)
    return token, jti


def create_stb_access_token(subscriber_id: str, device_id: str,
                            ttl_seconds: int | None = None) -> tuple[str, str, int]:
    """STB access token (type=stb_access, aud=nexora-stb). Reserved; wired in STB hardening."""
    jti = str(uuid.uuid4())
    ttl = ttl_seconds or (settings.client_access_token_expire_hours * 3600)
    token = _encode(subscriber_id, jti, TYPE_STB_ACCESS, AUD_STB, ttl, {"dev": device_id})
    return token, jti, ttl


# ── Decode + per-surface validation ─────────────────────────────────────────

def decode_claims(token: str) -> dict:
    """Decode + verify signature/exp with a FIXED algorithm. Audience is NOT
    auto-verified here — surface validation is done by enforce_surface().
    Raises jwt.InvalidTokenError on signature/exp/format failure.
    """
    return jwt.decode(
        token,
        settings.secret_key,
        algorithms=[settings.jwt_algorithm],   # fixed algorithm; header alg is not trusted
        options={"verify_aud": False},
    )


def decode_token(token: str) -> TokenPayload:
    """Admin token decode → TokenPayload. Raises jwt.InvalidTokenError on failure."""
    return TokenPayload(**decode_claims(token))


def decode_client_token(token: str) -> dict:
    """Client token decode → raw claims dict. Raises jwt.InvalidTokenError on failure."""
    return decode_claims(token)


def enforce_surface(payload: dict, expected_type: str, expected_aud: str,
                    legacy_types: set[str]) -> None:
    """Validate per-surface claims, honoring the jwt_require_aud flag.

    - jwt_require_aud=True  → require iss==issuer, aud==expected_aud, type==expected_type,
      and the presence of jti+sub. Cross-surface / legacy tokens are rejected.
    - jwt_require_aud=False → accept any type in legacy_types (compat); aud/iss optional.

    Raises jwt.InvalidTokenError on failure.
    """
    ttype = payload.get("type")
    if settings.jwt_require_aud:
        if (
            ttype != expected_type
            or payload.get("aud") != expected_aud
            or payload.get("iss") != settings.jwt_issuer
            or not payload.get("jti")
            or not payload.get("sub")
        ):
            raise InvalidTokenError("strict claim validation failed")
    else:
        if ttype not in legacy_types:
            raise InvalidTokenError("unexpected token type for this surface")
