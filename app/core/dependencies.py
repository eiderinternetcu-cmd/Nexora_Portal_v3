import uuid
from fastapi import Depends, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jwt.exceptions import InvalidTokenError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
import redis.asyncio as aioredis

from app.database import get_db
from app.redis_client import get_redis, key_blacklist, key_client
from app.core.security import (
    decode_claims,
    decode_client_token,
    enforce_surface,
    AUD_ADMIN,
    AUD_CLIENT,
    TYPE_ADMIN_ACCESS,
    TYPE_CLIENT_ACCESS,
    LEGACY_ADMIN_ACCESS,
    LEGACY_CLIENT_ACCESS,
)
from app.core.exceptions import unauthorized, forbidden
from app.models.user import User, UserRole
from app.models.subscriber import Subscriber, SubscriberStatus
from app.schemas.auth import TokenPayload

bearer_scheme = HTTPBearer(auto_error=False)


async def _get_token_payload(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    redis: aioredis.Redis = Depends(get_redis),
) -> TokenPayload:
    if not credentials:
        raise unauthorized("Missing Bearer token")
    try:
        claims = decode_claims(credentials.credentials)
        enforce_surface(claims, TYPE_ADMIN_ACCESS, AUD_ADMIN, LEGACY_ADMIN_ACCESS)
    except InvalidTokenError:
        raise unauthorized("Invalid or expired token")
    payload = TokenPayload(**claims)
    if await redis.exists(key_blacklist(payload.jti)):
        raise unauthorized("Token has been revoked")
    return payload


async def get_current_user(
    payload: TokenPayload = Depends(_get_token_payload),
    db: AsyncSession = Depends(get_db),
) -> User:
    result = await db.execute(select(User).where(User.id == uuid.UUID(payload.sub)))
    user = result.scalar_one_or_none()
    if not user or not user.is_active:
        raise unauthorized("User not found or inactive")
    return user


async def require_admin(user: User = Depends(get_current_user)) -> User:
    if user.role != UserRole.admin:
        raise forbidden("Admin role required")
    return user


async def require_admin_or_reseller(user: User = Depends(get_current_user)) -> User:
    if user.role not in (UserRole.admin, UserRole.reseller):
        raise forbidden("Insufficient permissions")
    return user


def get_client_ip(request: Request) -> str:
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


async def get_client_token_payload(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    redis: aioredis.Redis = Depends(get_redis),
) -> dict:
    if not credentials:
        raise unauthorized("Missing Bearer token")
    try:
        payload = decode_client_token(credentials.credentials)
        enforce_surface(payload, TYPE_CLIENT_ACCESS, AUD_CLIENT, LEGACY_CLIENT_ACCESS)
    except InvalidTokenError:
        raise unauthorized("Invalid or expired token")
    jti = payload.get("jti")
    if not jti or not await redis.exists(key_client(jti)):
        raise unauthorized("Token has been revoked")
    return payload


async def get_current_subscriber(
    payload: dict = Depends(get_client_token_payload),
    db: AsyncSession = Depends(get_db),
) -> Subscriber:
    try:
        sub_id = uuid.UUID(payload["sub"])
    except (KeyError, ValueError):
        raise unauthorized("Invalid token payload")
    result = await db.execute(select(Subscriber).where(Subscriber.id == sub_id))
    sub = result.scalar_one_or_none()
    if not sub:
        raise unauthorized("Subscriber not found")
    if sub.status in (SubscriberStatus.suspended, SubscriberStatus.banned):
        raise forbidden(f"Account {sub.status.value}")
    return sub
