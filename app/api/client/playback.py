"""Client playback authorization endpoints.

playback_url priority (never exposes Flussonic credentials):
  1. Flussonic HLS URL built from stream_key (when Flussonic is configured)
  2. channel.source_url from local DB (fallback manual URL)
  3. None  → frontend uses VITE_NEXORA_PLAYBACK_URL_TEMPLATE or shows error
"""
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
from app.integrations.flussonic_client import get_flussonic_client

router = APIRouter(prefix="/playback", tags=["Client Playback"])

_flussonic = get_flussonic_client()


def _get_ip(request: Request) -> str:
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _resolve_playback_url(stream_key: str | None, source_url: str | None) -> str | None:
    """
    Build the playback URL without exposing any Flussonic credentials.
    Priority: Flussonic URL > DB source_url > None.
    """
    if stream_key and _flussonic.is_configured:
        return _flussonic.stream_hls_url(stream_key)
    return source_url


@router.post("/authorize", response_model=PlaybackResponse)
async def authorize_playback(
    data: PlaybackAuthorizeRequest,
    request: Request,
    subscriber: Subscriber = Depends(get_current_subscriber),
    db: AsyncSession = Depends(get_db),
    redis: aioredis.Redis = Depends(get_redis),
):
    """Full playback authorization.

    Validates subscriber + active subscription + device + concurrent connection slot.
    channel_id is a channel_key from the catalog; stream_key is resolved internally.

    Response contains:
      - token: short-lived JWT (60s) for Flussonic backend-auth
      - playback_url: HLS URL (no credentials embedded)
      - expires_in: token TTL in seconds

    Credentials are never included in the response.
    """
    ch = None
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
        channel_id=data.channel_id,  # echo channel_key back — never stream_key
        subscriber_id=str(result.subscriber_id),
        playback_url=_resolve_playback_url(stream_key, ch.source_url if ch else None),
    )


@router.get("/{channel_id}", response_model=PlaybackResponse)
async def reissue_playback_token(
    channel_id: str,
    device_id: str = Query(..., min_length=6, max_length=128),
    subscriber: Subscriber = Depends(get_current_subscriber),
    db: AsyncSession = Depends(get_db),
    redis: aioredis.Redis = Depends(get_redis),
):
    """Reissue a playback token for a device with an active IPTV session.

    Lighter than /authorize — skips subscription/plan reload.
    Call /authorize first if no active session exists.

    Response contains the same safe fields as /authorize.
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
        playback_url=_resolve_playback_url(ch.stream_key, ch.source_url),
    )
