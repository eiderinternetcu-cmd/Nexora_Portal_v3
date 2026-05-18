"""
ChannelService — local channel catalog.
READ-ONLY with respect to Flussonic/Astra — no stream modification ever.
"""
import uuid
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.channel import Channel
from app.core.exceptions import not_found


class ChannelService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def list_active(self) -> list[Channel]:
        result = await self.db.execute(
            select(Channel)
            .where(Channel.is_active.is_(True))
            .order_by(Channel.number)
        )
        return list(result.scalars().all())

    async def list_all(self) -> list[Channel]:
        result = await self.db.execute(select(Channel).order_by(Channel.number))
        return list(result.scalars().all())

    async def get_by_key(self, channel_key: str) -> Channel | None:
        result = await self.db.execute(
            select(Channel).where(Channel.channel_key == channel_key)
        )
        return result.scalar_one_or_none()

    async def get_by_id(self, channel_id: uuid.UUID) -> Channel | None:
        result = await self.db.execute(
            select(Channel).where(Channel.id == channel_id)
        )
        return result.scalar_one_or_none()

    async def get_active_by_key(self, channel_key: str) -> Channel:
        """404 if channel doesn't exist or is inactive (same error — no info leak)."""
        ch = await self.get_by_key(channel_key)
        if ch is None or not ch.is_active:
            raise not_found("Channel")
        return ch
