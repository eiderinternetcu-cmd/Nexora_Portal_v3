"""Admin channel endpoints — read-only catalog + Flussonic stream status.

Flussonic/Astra are NEVER modified from here. This is local catalog only.
Stream status is fetched read-only from Flussonic via FlussonicClient.
Flussonic credentials are never returned in any response.

SECRETS (P0.8)
--------------
`stream_key` and `source_url` never leave this module in the clear except through
/{channel_id}/secrets, which is admin-only and audited. Everything else goes out
masked — see app/schemas/channel.py for why masking beats omitting and why the
mask drops the host and the path rather than only the credentials.
"""
import uuid
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.core.dependencies import (
    get_client_ip,
    require_admin,
    require_admin_or_reseller,
)
from app.core.exceptions import not_found
from app.models.user import User
from app.schemas.channel import ChannelAdminOut, ChannelSecretsOut, StreamStatusOut
from app.services.audit_service import AuditService
from app.services.channel_service import ChannelService
from app.integrations.flussonic_client import get_flussonic_client

router = APIRouter(prefix="/channels", tags=["Admin Channels"])

_flussonic = get_flussonic_client()


@router.get("", response_model=list[ChannelAdminOut])
async def list_channels(
    user: User = Depends(require_admin_or_reseller),
    db: AsyncSession = Depends(get_db),
):
    """The catalog with stream_key/source_url MASKED, for admin and reseller
    alike. The frontend never painted them, but the response did carry them —
    visible in devtools to anyone with panel access."""
    svc = ChannelService(db)
    return [ChannelAdminOut.from_channel(c) for c in await svc.list_all()]


@router.get("/{channel_id}/secrets", response_model=ChannelSecretsOut)
async def reveal_channel_secrets(
    channel_id: uuid.UUID,
    request: Request,
    response: Response,
    actor: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Reveal the real stream_key / source_url of ONE channel. Admin only, always
    audited. Must be triggered by an explicit user action — never on page load.

    A reseller gets 403, not 404. The channel plainly exists: it is in the
    listing they just read. Hiding it behind a 404 would be a lie that buys
    nothing, because there is no existence to conceal here — only a privilege the
    caller does not have.

    The audit entry records WHO revealed WHICH channel and WHEN. It deliberately
    does NOT record the values: GET /api/admin/audit is readable by resellers
    too, so putting a secret in `details` would reopen the exact hole through the
    audit trail itself. Only an INSERT happens here — migration 007 makes
    audit_logs append-only (UPDATE/DELETE are blocked by trigger).
    """
    svc = ChannelService(db)
    channel = await svc.get_by_id(channel_id)
    if channel is None:
        raise not_found("Channel")

    await AuditService(db).log(
        "channel.secrets_reveal",
        actor,
        "channel",
        str(channel_id),
        {
            "channel_key": channel.channel_key,
            "has_source_url": channel.source_url is not None,
        },
        get_client_ip(request),
        request.headers.get("User-Agent"),
    )

    # Secrets must not sit in an intermediary cache.
    response.headers["Cache-Control"] = "no-store"
    return ChannelSecretsOut(
        channel_id=channel.id,
        channel_key=channel.channel_key,
        stream_key=channel.stream_key,
        source_url=channel.source_url,
    )


@router.get("/{channel_id}", response_model=ChannelAdminOut)
async def get_channel(
    channel_id: uuid.UUID,
    user: User = Depends(require_admin_or_reseller),
    db: AsyncSession = Depends(get_db),
):
    """Single-channel detail — masked exactly like the listing. Plugging the
    listing while leaving the detail open would just move the hole."""
    svc = ChannelService(db)
    channel = await svc.get_by_id(channel_id)
    if channel is None:
        raise not_found("Channel")
    return ChannelAdminOut.from_channel(channel)


@router.get("/{channel_id}/stream-status", response_model=StreamStatusOut)
async def get_stream_status(
    channel_id: uuid.UUID,
    user: User = Depends(require_admin_or_reseller),
    db: AsyncSession = Depends(get_db),
):
    """
    Query Flussonic for the live status of a channel's stream.

    Returns: alive, client_count, input_alive — keyed by the PUBLIC channel_key.
    Never returns the stream_key, Flussonic credentials or internal API details.

    503 if Flussonic is not configured.
    404 if the channel or stream is not found in Flussonic.
    """
    if not _flussonic.is_configured:
        raise HTTPException(
            status_code=503,
            detail="Flussonic integration is not configured.",
        )

    svc = ChannelService(db)
    channel = await svc.get_by_id(channel_id)
    if channel is None:
        raise not_found("Channel")

    status = await _flussonic.get_stream_status(channel.stream_key)
    if status is None:
        # The 404 body names the PUBLIC key: it used to echo the stream_key,
        # which turned a plain error message into a secret oracle.
        raise HTTPException(
            status_code=404,
            detail=f"Stream for channel '{channel.channel_key}' not found in Flussonic.",
        )

    return StreamStatusOut(
        channel_key=channel.channel_key,
        alive=status.alive,
        client_count=status.client_count,
        input_alive=status.input_alive,
        flussonic_configured=True,
    )
