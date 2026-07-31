import ipaddress
import uuid
from fastapi import Depends, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jwt.exceptions import InvalidTokenError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
import redis.asyncio as aioredis

from app.config import get_settings
from app.database import get_db
from app.redis_client import get_redis, key_blacklist, key_client
from app.core.security import (
    decode_claims,
    decode_client_token,
    enforce_surface,
    AUD_ADMIN,
    AUD_CLIENT,
    AUD_STB,
    TYPE_ADMIN_ACCESS,
    TYPE_CLIENT_ACCESS,
    TYPE_STB_ACCESS,
    LEGACY_ADMIN_ACCESS,
    LEGACY_CLIENT_ACCESS,
)
from app.core.exceptions import unauthorized, forbidden
from app.models.user import User, UserRole
from app.models.subscriber import Subscriber, SubscriberStatus
from app.schemas.auth import TokenPayload

settings = get_settings()

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


def _is_ip(value: str) -> bool:
    """Reject anything that is not a literal address.

    A malformed header would otherwise become a Redis key fragment (rate limit,
    lockout) and an audit row, letting a caller mint unbounded key names.
    """
    try:
        ipaddress.ip_address(value)
    except ValueError:
        return False
    return True


def _is_trusted_proxy(peer: str) -> bool:
    """True when the TCP peer is one of the proxies allowed to assert a client IP."""
    networks = settings.trusted_proxy_networks
    if not peer or not networks:
        return False
    try:
        addr = ipaddress.ip_address(peer)
    except ValueError:
        return False
    return any(addr in net for net in networks)


def get_client_ip(request: Request) -> str:
    """The caller's IP, as used for login lockout, rate limiting and audit rows.

    NX-AUTH. The legacy implementation returned the FIRST value of
    X-Forwarded-For, which the client writes. That made every per-IP control
    evadable (a new fake address per request never fills a bucket) and, worse,
    abusable: sending a colleague's address locks THEM out of the admin panel.

    Under CLIENT_IP_SOURCE=edge the header is only believed when the connection
    itself comes from a proxy listed in TRUSTED_PROXY_CIDRS:

      1. X-Real-IP — what this project's nginx sets, from $remote_addr
         (deploy/nginx/snippets/proxy-common.conf).
      2. the LAST hop of X-Forwarded-For. A forwarded chain grows to the RIGHT,
         so the rightmost entry is the one the closest trusted proxy wrote and
         everything to its left is unverified client input. This project's nginx
         overwrites the header with $remote_addr (a single hop), so both readings
         agree there; reading from the right is what stays correct if a CDN is
         ever put in front and the header starts arriving with several hops.
      3. the peer address, when the trusted proxy sent no forwarded header.

    From an UNTRUSTED peer no header is honored at all and the peer address is
    used, so a spoofed header buys nothing. That is also what makes local
    development keep working with no proxy in front: nothing is trusted, and the
    connection address is the answer.

    CLIENT_IP_SOURCE=legacy (default) keeps the old behavior byte for byte.
    """
    peer = request.client.host if request.client else ""

    if settings.client_ip_source == "edge":
        if _is_trusted_proxy(peer):
            real_ip = (request.headers.get("X-Real-IP") or "").strip()
            if _is_ip(real_ip):
                return real_ip
            forwarded = request.headers.get("X-Forwarded-For") or ""
            hops = [hop.strip() for hop in forwarded.split(",") if hop.strip()]
            if hops and _is_ip(hops[-1]):
                return hops[-1]
        # Untrusted peer, or a trusted proxy that asserted nothing usable.
        return peer or "unknown"

    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return peer or "unknown"


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


async def get_stb_token_payload(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    redis: aioredis.Redis = Depends(get_redis),
) -> dict:
    """Authenticate an STB/device caller (type=stb_access, aud=nexora-stb).

    Returns the raw claims. `sub` (subscriber UUID) and `dev` (Device.device_id)
    are the AUTHORITATIVE identity for the /api/stb/* surface — callers must
    never take those from the request body.

    The legacy set is deliberately just {stb_access}: with jwt_require_aud off,
    aud/iss are not checked, so the token *type* is what keeps admin, client and
    playback tokens off this surface. There is no cross-surface escape hatch.
    """
    if not credentials:
        raise unauthorized("Missing Bearer token")
    try:
        claims = decode_claims(credentials.credentials)
        enforce_surface(claims, TYPE_STB_ACCESS, AUD_STB, {TYPE_STB_ACCESS})
    except InvalidTokenError:
        raise unauthorized("Invalid or expired token")
    if not claims.get("sub") or not claims.get("dev"):
        raise unauthorized("STB token is missing its subscriber/device binding")
    jti = claims.get("jti")
    if jti and await redis.exists(key_blacklist(jti)):
        raise unauthorized("Token has been revoked")
    return claims


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
