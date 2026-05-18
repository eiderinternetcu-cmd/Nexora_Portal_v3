"""Client catalog — DB-backed channel list and mock EPG."""
from datetime import datetime, timezone, timedelta
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.core.dependencies import get_current_subscriber
from app.models.subscriber import Subscriber
from app.schemas.channel import ChannelPublic
from app.schemas.client import EpgEntry
from app.services.channel_service import ChannelService

router = APIRouter(prefix="/catalog", tags=["Client Catalog"])

_MOCK_EPG: dict[str, list[dict]] = {
    "canal-1":  [{"title": "Morning Show", "hour_offset": 0, "duration": 2}],
    "canal-2":  [{"title": "Breaking News", "hour_offset": 0, "duration": 1},
                 {"title": "World Report",  "hour_offset": 1, "duration": 2}],
    "canal-3":  [{"title": "Live Sport",    "hour_offset": 0, "duration": 3}],
}


@router.get("/channels", response_model=list[ChannelPublic])
async def list_channels(
    subscriber: Subscriber = Depends(get_current_subscriber),
    db: AsyncSession = Depends(get_db),
):
    svc = ChannelService(db)
    return await svc.list_active()


@router.get("/channels/{channel_key}/epg", response_model=list[EpgEntry])
async def get_epg(
    channel_key: str,
    subscriber: Subscriber = Depends(get_current_subscriber),
    db: AsyncSession = Depends(get_db),
):
    svc = ChannelService(db)
    await svc.get_active_by_key(channel_key)  # 404 if not found or inactive

    entries_raw = _MOCK_EPG.get(channel_key, [])
    base = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    return [
        EpgEntry(
            channel_id=channel_key,
            title=e["title"],
            start_at=base + timedelta(hours=e["hour_offset"]),
            end_at=base + timedelta(hours=e["hour_offset"] + e["duration"]),
        )
        for e in entries_raw
    ]
