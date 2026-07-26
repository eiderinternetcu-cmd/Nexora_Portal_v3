"""Identity for the /api/stb/* device surface.

Historically every device-facing STB endpoint took the caller's identity from
the REQUEST BODY (`subscriber_id`, `device_id`) with no token at all. That made
POST /api/stb/auth/play a straight IDOR — any caller who could reach the route
(nginx publishes it: `location ^~ /api/` in deploy/nginx/nexoraplay.conf) could
mint a playback token, open an IPTV session and consume a connection slot for
ANY subscriber, just by guessing/knowing a subscriber UUID and a device_id.

This module makes the STB token the source of truth:

  * `stb_claims` — FastAPI dependency returning validated STB claims, or None
    only when STB_AUTH_ENFORCE is off AND no token was sent. A token that IS
    presented is always validated, flag or no flag.
  * `resolve_stb_identity` — turns those claims into the (subscriber_id,
    device_id) actually used, rejecting any body field that disagrees.
"""
import logging
import uuid

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials
import redis.asyncio as aioredis

from app.config import get_settings
from app.core.dependencies import bearer_scheme, get_stb_token_payload
from app.core.exceptions import forbidden, unauthorized
from app.redis_client import get_redis

logger = logging.getLogger(__name__)

settings = get_settings()


async def stb_claims(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    redis: aioredis.Redis = Depends(get_redis),
) -> dict | None:
    """Validated STB claims for this request.

    Returns None ONLY in the legacy escape hatch: STB_AUTH_ENFORCE=false and no
    Authorization header at all. Everything else goes through the full
    per-surface check, so a client/admin/playback token is a 401 either way.
    """
    if credentials is None and not settings.stb_auth_enforce:
        logger.warning(
            "STB request accepted without a token (STB_AUTH_ENFORCE=off) — "
            "identity is taken from the request body and is NOT trustworthy"
        )
        return None
    return await get_stb_token_payload(credentials=credentials, redis=redis)


def resolve_stb_device_id(claims: dict | None, body_device_id: str | None) -> str:
    """The device_id this request is allowed to act as.

    With a token the `dev` claim wins; a body value that disagrees is a 403,
    never a silent downgrade to the body value. Without a token
    (STB_AUTH_ENFORCE=off) it falls back to the body — the pre-hardening
    behavior the flag exists to close.
    """
    if claims is None:
        if not body_device_id:
            raise unauthorized(
                "device_id is required when no STB token is presented"
            )
        return body_device_id

    token_device_id = str(claims.get("dev") or "")
    if not token_device_id:
        raise unauthorized("STB token is missing its device binding")
    if body_device_id is not None and body_device_id != token_device_id:
        raise forbidden("device_id does not match the authenticated STB token")
    return token_device_id


def resolve_stb_identity(
    claims: dict | None,
    body_subscriber_id: uuid.UUID | None,
    body_device_id: str | None,
) -> tuple[uuid.UUID, str]:
    """The (subscriber_id, device_id) this request is allowed to act as.

    With a token: the `sub`/`dev` claims win. Body fields are optional, but if
    present they must agree with the token — a mismatch is 403, never a silent
    downgrade to the body value.

    Without a token (STB_AUTH_ENFORCE=off): falls back to the body, which is the
    pre-hardening behavior and is exactly the IDOR the flag exists to close.
    """
    device_id = resolve_stb_device_id(claims, body_device_id)

    if claims is None:
        if body_subscriber_id is None:
            raise unauthorized(
                "subscriber_id is required when no STB token is presented"
            )
        return body_subscriber_id, device_id

    try:
        token_subscriber_id = uuid.UUID(str(claims.get("sub")))
    except (TypeError, ValueError):
        raise unauthorized("STB token subject is not a valid subscriber id")

    if body_subscriber_id is not None and body_subscriber_id != token_subscriber_id:
        raise forbidden("subscriber_id does not match the authenticated STB token")

    return token_subscriber_id, device_id
