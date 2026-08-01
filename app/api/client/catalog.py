"""Client catalog — DB-backed channel list and programme guide."""
from datetime import datetime, timezone, timedelta
from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import get_db
from app.core.dependencies import get_current_subscriber
from app.models.subscriber import Subscriber
from app.schemas.channel import ChannelPublic, ChannelPublicParental
from app.schemas.client import EpgEntry
from app.services.channel_service import ChannelService
from app.services.epg_service import EpgService

router = APIRouter(prefix="/catalog", tags=["Client Catalog"])

settings = get_settings()

# NX-EPG — LEGACY. Three invented programmes covering three of the 41 channels,
# which every real client rendered as if it were the schedule. Superseded by the
# `epg_programmes` table (migration 013) and reachable only while EPG_ENABLED is
# False, i.e. it is now the OLD behaviour a rollback returns to, not a fallback:
# `get_epg` never serves this when the feature is on, not even for a channel with
# no data. Delete once EPG_ENABLED has been true in production long enough that
# rolling back is not a consideration.
_MOCK_EPG: dict[str, list[dict]] = {
    "canal-1":  [{"title": "Morning Show", "hour_offset": 0, "duration": 2}],
    "canal-2":  [{"title": "Breaking News", "hour_offset": 0, "duration": 1},
                 {"title": "World Report",  "hour_offset": 1, "duration": 2}],
    "canal-3":  [{"title": "Live Sport",    "hour_offset": 0, "duration": 3}],
}


def _mock_epg(channel_key: str) -> list[EpgEntry]:
    """The pre-NX-EPG payload, byte for byte. Only reachable with EPG_ENABLED off."""
    base = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    return [
        EpgEntry(
            channel_id=channel_key,
            title=e["title"],
            start_at=base + timedelta(hours=e["hour_offset"]),
            end_at=base + timedelta(hours=e["hour_offset"] + e["duration"]),
        )
        for e in _MOCK_EPG.get(channel_key, [])
    ]


@router.get("/channels", response_model=None)
async def list_channels(
    subscriber: Subscriber = Depends(get_current_subscriber),
    db: AsyncSession = Depends(get_db),
) -> list[ChannelPublic] | list[ChannelPublicParental]:
    """"My channels" — the catalog this subscriber can actually play.

    Gated by CATALOG_ENTITLEMENT_FILTER (default OFF → full active catalog,
    i.e. the historical behaviour). See ChannelService for the plan-resolution
    rules and for why a subscriber with no plan in force still gets the full
    catalog instead of an empty list.

    NX-PARENTAL — with PARENTAL_CONTROL_ENFORCE on, each row also carries
    `censored`, so a client can blur an adult tile and prompt for the PIN. A
    censored channel is listed, never hidden: hiding leaves gaps in a grid the
    household pays for, and hiding is not a control (the gate is at
    /playback/*). Listing it leaks nothing playable — see ChannelPublicParental.

    `response_model` is None and the rows are built EXPLICITLY below because the
    schema has to vary per request: with the flag off the payload must keep
    exactly today's key set (a new JSON key is a change, however additive), and
    a declared response_model cannot be chosen at call time. Building the schema
    by hand is also what keeps `stream_key`/`source_url` out — returning ORM
    rows under `response_model=None` would serialise the whole model, secrets
    included.
    """
    svc = ChannelService(db)
    channels = await svc.list_visible_for_subscriber(subscriber.id)
    if not settings.parental_control_enforce:
        return [ChannelPublic.model_validate(ch) for ch in channels]
    return [ChannelPublicParental.model_validate(ch) for ch in channels]


@router.get("/channels/{channel_key}/epg", response_model=list[EpgEntry])
async def get_epg(
    channel_key: str,
    start: datetime | None = Query(
        None, description="Window start (ISO-8601, UTC assumed). Defaults to now."
    ),
    end: datetime | None = Query(
        None,
        description="Window end (ISO-8601, UTC assumed). Defaults to "
                    "start + EPG_DEFAULT_WINDOW_HOURS.",
    ),
    subscriber: Subscriber = Depends(get_current_subscriber),
    db: AsyncSession = Depends(get_db),
):
    """The channel's programme guide for a time window.

    NX-EPG — gated by EPG_ENABLED. OFF (default) → the legacy `_MOCK_EPG`
    payload, unchanged, so deploying this alters nothing. ON → the programmes
    ingested from XMLTV into `epg_programmes`.

    A channel with no ingested data returns an EMPTY LIST, and that is the whole
    point of the feature: the previous behaviour handed clients invented
    programmes for three channels and rendered them as the real schedule. An
    empty guide is honest and a client can say "no information available"; a
    fabricated one it cannot detect at all. It is a 200, not a 404 — the CHANNEL
    exists (that is checked below), only its guide is unknown, and a 404 here
    would read as "no such channel" and break the grid.

    `start`/`end` are optional and additive: a caller that sends neither gets
    today's grid, so existing clients are unaffected.

    NX-PARENTAL, recorded limitation (unchanged by NX-EPG): this endpoint is NOT
    gated. It returns programme TITLES, which is content metadata rather than
    anything playable — the parental gate's job is to stop a stream being
    authorised, and no token is minted here. Gating it properly needs a device to
    check the unlock grant against, which this route does not take. Now that the
    titles are REAL, hiding a censored channel's guide behind the same unlock is
    worth doing, and the route will need a device_id for it.
    """
    svc = ChannelService(db)
    channel = await svc.get_active_by_key(channel_key)  # 404 if absent or inactive

    if not settings.epg_enabled:
        return _mock_epg(channel_key)

    window_start = _aware(start) if start else datetime.now(timezone.utc)
    window_end = (
        _aware(end)
        if end
        else window_start + timedelta(hours=settings.epg_default_window_hours)
    )
    # Bound what one call can ask for: the window is caller-controlled and the
    # table holds every channel's full guide, so an unbounded range is a cheap
    # way to make the API serialise thousands of rows per request.
    max_end = window_start + timedelta(hours=settings.epg_max_window_hours)
    window_end = min(max(window_end, window_start), max_end)

    programmes = await EpgService(db).programmes_in_window(
        channel.id, window_start, window_end
    )
    return [
        EpgEntry(
            channel_id=channel_key,
            title=p.title,
            description=p.description,
            start_at=p.start_at,
            end_at=p.end_at,
        )
        for p in programmes
    ]


def _aware(dt: datetime) -> datetime:
    """Treat a naive query parameter as UTC.

    Same convention as the XMLTV parser and as ChannelService._aware: comparing a
    naive datetime against a timestamptz column raises, and guessing the server's
    local zone would make the same request mean different things on two hosts.
    """
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)
