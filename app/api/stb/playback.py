"""
STB Playback Auth endpoints.

POST /api/stb/auth/play       full authorization + IPTV session in DB + playback token
POST /api/stb/auth/validate   validate token (Flussonic backend-auth callback)
POST /api/stb/auth/token      reissue playback token for already-connected device

None of these endpoints require admin credentials — they are device/player facing.
Flussonic calls /validate as a backend service using the token passed by the player.
"""
from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession
import redis.asyncio as aioredis

from app.database import get_db
from app.redis_client import get_redis
from app.core.dependencies import get_client_ip
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


@router.post("/play", response_model=ApiResponse[PlaybackTokenOut])
async def play_authorize(
    body: PlayRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    svc: StreamAuthService = Depends(_svc),
):
    """
    Full IPTV authorization flow:

    1. subscriber.status == active
    2. subscription active + not expired → plan.max_connections
    3. device not blocked + belongs to subscriber
    4. concurrent slot available in Redis ZSET
    5. IPTV session created in PostgreSQL (replaces existing session for device)
    6. Short-lived playback JWT issued (default 60s), linked to DB session via 'ses' claim

    Use /auth/token to reissue without the full DB round-trip.
    """
    ip = get_client_ip(request)
    user_agent = request.headers.get("User-Agent")

    result = await svc.authorize(
        body.subscriber_id, body.device_id, body.channel_id, ip, user_agent
    )
    await db.commit()
    return ApiResponse(
        data=PlaybackTokenOut(
            token=result.token,
            expires_in=result.expires_in,
            subscriber_id=str(result.subscriber_id),
            device_id=str(result.device_id),
            channel_id=result.channel_id,
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
    svc: StreamAuthService = Depends(_svc),
):
    """
    Reissue a playback token for a device already connected (ZSET + DB session active).

    Lighter than /play — skips subscriber, subscription, and plan DB queries.
    Use this to refresh the playback token mid-session without a full reauth.
    """
    result = await svc.create_token(body.subscriber_id, body.device_id, body.channel_id)
    return ApiResponse(
        data=PlaybackTokenOut(
            token=result.token,
            expires_in=result.expires_in,
            subscriber_id=str(result.subscriber_id),
            device_id=str(result.device_id),
            channel_id=result.channel_id,
        )
    )
