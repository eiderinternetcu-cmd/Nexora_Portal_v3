"""Admin channel endpoints — read-only.

Flussonic/Astra are NEVER modified from here. This is local catalog only.
"""
import uuid
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.core.dependencies import require_admin_or_reseller
from app.core.exceptions import not_found
from app.models.user import User
from app.schemas.channel import ChannelAdminOut
from app.services.channel_service import ChannelService

router = APIRouter(prefix="/channels", tags=["Admin Channels"])


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
