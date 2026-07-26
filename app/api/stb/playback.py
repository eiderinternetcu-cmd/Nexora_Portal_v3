"""
STB Playback Auth endpoints.

POST /api/stb/auth/play       full authorization + IPTV session in DB + playback token
POST /api/stb/auth/validate   validate token (Flussonic backend-auth callback)
POST /api/stb/auth/token      reissue playback token for already-connected device

/play and /token are DEVICE facing and require an STB token (type=stb_access,
aud=nexora-stb); the token's `sub`/`dev` claims are the authoritative identity.
They used to require nothing at all and read the identity from the body, which
made /play an IDOR against every subscriber. See app/api/stb/deps.py.

/validate is the Flussonic backend-auth callback: it is a *validator*, not an
issuer — it grants nothing and only reports on a token the caller already holds,
so it deliberately keeps its own trust model (bearer = the playback token in the
body) and is unchanged here.
"""
from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession
import redis.asyncio as aioredis

from app.database import get_db
from app.redis_client import get_redis
from app.core.dependencies import get_client_ip
from app.api.stb.deps import stb_claims, resolve_stb_identity
from app.services.channel_service import ChannelService
from app.services.stream_auth_service import StreamAuthService
from app.schemas.playback import (
    PlayRequest,
    PlaybackTokenOut,
    ValidateRequest,
    ValidateResponse,
    TokenRequest,
)
from app.schemas.common import ApiResponse

router = APIRouter(prefix="/auth", tags=["STB — Playback Auth"])


def _svc(
    db: AsyncSession = Depends(get_db),
    redis: aioredis.Redis = Depends(get_redis),
) -> StreamAuthService:
    return StreamAuthService(db, redis)


async def _resolve_channel(db: AsyncSession, channel_key: str | None):
    """Resolve the PUBLIC channel key to (stream_key, node), like the client route.

    The STB body's `channel_id` is a catalog channel_key, but
    StreamAuthService.authorize's positional `channel_id` parameter is the
    internal stream_key. This route used to pass the two straight through each
    other, which left `channel_key=None` — and `_check_entitlement` returns
    early on a None channel_key, so EntitlementService was NEVER consulted from
    the STB surface, with ENTITLEMENT_ENFORCE on or off. Resolving here is what
    puts the entitlement gate back in the path (and populates the chn/node
    claims, without which the token is exempt from node binding too).
    """
    if not channel_key:
        return None, None
    ch = await ChannelService(db).get_active_by_key(channel_key)
    return ch.stream_key, ch.flussonic_node


@router.post("/play", response_model=ApiResponse[PlaybackTokenOut])
async def play_authorize(
    body: PlayRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    svc: StreamAuthService = Depends(_svc),
    claims: dict | None = Depends(stb_claims),
):
    """
    Full IPTV authorization flow:

    0. STB token authenticated; subscriber_id/device_id come from its claims
    1. entitlement (channel is in the subscriber's plan) — 403 under ENTITLEMENT_ENFORCE
    2. subscriber.status == active
    3. subscription active + not expired → plan.max_connections
    4. device not blocked + belongs to subscriber
    5. concurrent slot available in Redis ZSET
    6. IPTV session created in PostgreSQL (replaces existing session for device)
    7. Short-lived playback JWT issued (default 60s), linked to DB session via 'ses' claim

    Use /auth/token to reissue without the full DB round-trip.
    """
    subscriber_id, device_id = resolve_stb_identity(
        claims, body.subscriber_id, body.device_id
    )
    ip = get_client_ip(request)
    user_agent = request.headers.get("User-Agent")

    stream_key, node = await _resolve_channel(db, body.channel_id)

    result = await svc.authorize(
        subscriber_id=subscriber_id,
        device_id_str=device_id,
        channel_id=stream_key,      # internal stream_key → 'sk' claim
        ip=ip,
        user_agent=user_agent,
        channel_key=body.channel_id,  # public key → EntitlementService + 'chn' claim
        node=node,
    )
    await db.commit()
    return ApiResponse(
        data=PlaybackTokenOut(
            token=result.token,
            expires_in=result.expires_in,
            subscriber_id=str(result.subscriber_id),
            device_id=str(result.device_id),
            channel_id=body.channel_id,  # echo the public key — never the stream_key
        )
    )


@router.post("/validate", response_model=ValidateResponse)
async def play_validate(
    body: ValidateRequest,
    svc: StreamAuthService = Depends(_svc),
):
    """
    Validate a playback token.

    Designed as the Flussonic Media Server backend-auth callback.
    Checks (fast to slow):
      1. JWT signature + expiry
      2. Redis nexora:playback:{jti} exists
      3. Redis ZSET connection active
      4. IPTV session not revoked (Redis cache → DB fallback)

    Returns 200 + payload when valid.
    Returns 401/403 via NexoraException handler when invalid/expired/revoked.
    """
    payload = await svc.validate(body.token)
    return ValidateResponse(valid=True, **payload)


@router.post("/token", response_model=ApiResponse[PlaybackTokenOut])
async def play_token(
    body: TokenRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    svc: StreamAuthService = Depends(_svc),
    claims: dict | None = Depends(stb_claims),
):
    """
    Reissue a playback token for a device already connected (ZSET + DB session active).

    Lighter than /play — skips subscriber, subscription, and plan DB queries.
    Use this to refresh the playback token mid-session without a full reauth.

    The reissued token carries the same bindings as /play (chn/sk/node/cip): a
    player renews every ~45s, so most tokens in flight are reissues and a weaker
    reissue would silently undo node and IP binding.
    """
    subscriber_id, device_id = resolve_stb_identity(
        claims, body.subscriber_id, body.device_id
    )
    stream_key, node = await _resolve_channel(db, body.channel_id)

    result = await svc.create_token(
        subscriber_id=subscriber_id,
        device_id_str=device_id,
        channel_id=stream_key,
        channel_key=body.channel_id,
        node=node,
        ip=get_client_ip(request),
    )
    return ApiResponse(
        data=PlaybackTokenOut(
            token=result.token,
            expires_in=result.expires_in,
            subscriber_id=str(result.subscriber_id),
            device_id=str(result.device_id),
            channel_id=body.channel_id,  # echo the public key — never the stream_key
        )
    )
