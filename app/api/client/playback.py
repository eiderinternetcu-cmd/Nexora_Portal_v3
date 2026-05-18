"""Client playback authorization endpoints."""
from fastapi import APIRouter, Depends, Query, Request
import redis.asyncio as aioredis
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.redis_client import get_redis
from app.core.dependencies import get_current_subscriber
from app.models.subscriber import Subscriber
from app.schemas.client import PlaybackAuthorizeRequest, PlaybackResponse
from app.services.stream_auth_service import StreamAuthService
from app.services.channel_service import ChannelService

router = APIRouter(prefix="/playback", tags=["Client Playback"])


def _get_ip(request: Request) -> str:
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


@router.post("/authorize", response_model=PlaybackResponse)
async def authorize_playback(
    data: PlaybackAuthorizeRequest,
    request: Request,
    subscriber: Subscriber = Depends(get_current_subscriber),
    db: AsyncSession = Depends(get_db),
    redis: aioredis.Redis = Depends(get_redis),
):
    """Full authorization: validates subscriber + subscription + device + concurrency.
    channel_id must be a valid channel_key from the catalog.
    Internally maps channel_key → stream_key before calling StreamAuthService.
    """
    stream_key: str | None = None
    if data.channel_id:
        ch = await ChannelService(db).get_active_by_key(data.channel_id)
        stream_key = ch.stream_key

    svc = StreamAuthService(db, redis)
    result = await svc.authorize(
        subscriber_id=subscriber.id,
        device_id_str=data.device_id,
        channel_id=stream_key,
        ip=_get_ip(request),
        user_agent=request.headers.get("User-Agent"),
    )
    await db.commit()
    return PlaybackResponse(
        token=result.token,
        expires_in=result.expires_in,
        channel_id=data.channel_id,  # echo channel_key back to client, not stream_key
        subscriber_id=str(result.subscriber_id),
    )


@router.get("/{channel_id}", response_model=PlaybackResponse)
async def reissue_playback_token(
    channel_id: str,
    device_id: str = Query(..., min_length=6, max_length=128),
    subscriber: Subscriber = Depends(get_current_subscriber),
    db: AsyncSession = Depends(get_db),
    redis: aioredis.Redis = Depends(get_redis),
):
    """Reissue a playback token for a device already connected.
    channel_id must be a valid active channel_key.
    Call /authorize first if no active IPTV session exists.
    """
    ch = await ChannelService(db).get_active_by_key(channel_id)

    svc = StreamAuthService(db, redis)
    result = await svc.create_token(
        subscriber_id=subscriber.id,
        device_id_str=device_id,
        channel_id=ch.stream_key,
    )
    return PlaybackResponse(
        token=result.token,
        expires_in=result.expires_in,
        channel_id=channel_id,
        subscriber_id=str(result.subscriber_id),
    )
