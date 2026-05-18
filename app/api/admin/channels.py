"""Admin channel endpoints — read-only catalog + Flussonic stream status.

Flussonic/Astra are NEVER modified from here. This is local catalog only.
Stream status is fetched read-only from Flussonic via FlussonicClient.
Flussonic credentials are never returned in any response.
"""
import uuid
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.core.dependencies import require_admin_or_reseller
from app.core.exceptions import not_found
from app.models.user import User
from app.schemas.channel import ChannelAdminOut, StreamStatusOut
from app.services.channel_service import ChannelService
from app.integrations.flussonic_client import get_flussonic_client

router = APIRouter(prefix="/channels", tags=["Admin Channels"])

_flussonic = get_flussonic_client()


@router.get("", response_model=list[ChannelAdminOut])
async def list_channels(
    user: User = Depends(require_admin_or_reseller),
    db: AsyncSession = Depends(get_db),
):
    svc = ChannelService(db)
    return await svc.list_all()


@router.get("/{channel_id}", response_model=ChannelAdminOut)
async def get_channel(
    channel_id: uuid.UUID,
    user: User = Depends(require_admin_or_reseller),
    db: AsyncSession = Depends(get_db),
):
    svc = ChannelService(db)
    channel = await svc.get_by_id(channel_id)
    if channel is None:
        raise not_found("Channel")
    return channel


@router.get("/{channel_id}/stream-status", response_model=StreamStatusOut)
async def get_stream_status(
    channel_id: uuid.UUID,
    user: User = Depends(require_admin_or_reseller),
    db: AsyncSession = Depends(get_db),
):
    """
    Query Flussonic for the live status of a channel's stream.

    Returns: alive, client_count, input_alive.
    Never returns Flussonic credentials or internal API details.

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
        raise HTTPException(
            status_code=404,
            detail=f"Stream '{channel.stream_key}' not found in Flussonic.",
        )

    return StreamStatusOut(
        stream_key=channel.stream_key,
        alive=status.alive,
        client_count=status.client_count,
        input_alive=status.input_alive,
        flussonic_configured=True,
    )
