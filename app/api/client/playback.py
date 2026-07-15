"""Client playback authorization endpoints.

playback_url priority (never exposes Flussonic credentials):
  1. Flussonic HLS URL built from stream_key (when Flussonic is configured)
  2. channel.source_url from local DB (fallback manual URL)
  3. None  → frontend uses VITE_NEXORA_PLAYBACK_URL_TEMPLATE or shows error
"""
from fastapi import APIRouter, Depends, Query, Request
import redis.asyncio as aioredis
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import get_db
from app.redis_client import get_redis
from app.core.dependencies import get_current_subscriber
from app.core.exceptions import NexoraException
from app.models.subscriber import Subscriber
from app.schemas.client import PlaybackAuthorizeRequest, PlaybackResponse
from app.services.stream_auth_service import StreamAuthService
from app.services.metrics_service import MetricsService
from app.services.channel_service import ChannelService
from app.integrations.flussonic_client import get_flussonic_node_client
from app.models.channel import Channel

router = APIRouter(prefix="/playback", tags=["Client Playback"])

settings = get_settings()


def _maybe_sign(playback_url: str | None, token: str) -> str | None:
    """Append ?token= to the playback_url only when SIGNED_URL_ENFORCE is on.

    With the flag off the URL is returned unchanged (current behavior preserved);
    the token still travels in the response body for the player to use.
    """
    if not playback_url or not settings.signed_url_enforce:
        return playback_url
    sep = "&" if "?" in playback_url else "?"
    return f"{playback_url}{sep}token={token}"


def _get_ip(request: Request) -> str:
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _resolve_playback_url(channel: Channel | None, stream_key: str | None) -> str | None:
    """Build the HLS URL using the channel's assigned Flussonic node.

    Priority:
      1. FlussonicClient for channel.flussonic_node → stream_key URL
      2. channel.source_url (stored fallback — full URL from import)
      3. None → frontend shows error
    Flussonic credentials are never included in the returned URL.
    """
    if channel is None:
        return None

    node_id = channel.flussonic_node or "ec-main"
    client = get_flussonic_node_client(node_id)

    if stream_key and client and client.is_configured:
        hls_path = channel.hls_path or "index.m3u8"
        return client.stream_hls_url(stream_key, hls_path)

    return channel.source_url


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

    node = ch.flussonic_node if ch is not None else None

    svc = StreamAuthService(db, redis)
    metrics = MetricsService(redis)
    try:
        result = await svc.authorize(
            subscriber_id=subscriber.id,
            device_id_str=data.device_id,
            channel_id=stream_key,
            ip=_get_ip(request),
            user_agent=request.headers.get("User-Agent"),
            channel_key=data.channel_id,  # public key → EntitlementService
            node=node,
        )
    except NexoraException as exc:
        await metrics.record_playback_failure(exc.detail)
        raise
    await metrics.record_playback_success()
    await db.commit()

    base_url = _resolve_playback_url(ch, stream_key)
    return PlaybackResponse(
        token=result.token,
        expires_in=result.expires_in,
        channel_id=data.channel_id,  # echo channel_key back — never stream_key
        subscriber_id=str(result.subscriber_id),
        playback_url=_maybe_sign(base_url, result.token),
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
        playback_url=_resolve_playback_url(ch, ch.stream_key),
    )
