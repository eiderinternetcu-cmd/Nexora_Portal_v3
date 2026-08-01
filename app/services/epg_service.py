"""EpgService — ingest an XMLTV guide and serve it back per channel (NX-EPG).

Before this, `GET /api/client/catalog/channels/{key}/epg` served a hard-coded
dict of three invented programmes for three of the 41 channels. Clients rendered
it as if it were the real grid.

INGEST MODEL
────────────
Whole-source, idempotent UPSERT — not "delete the channel's grid, then insert".

  * The no-duplicates guarantee is `uq_epg_programmes_channel_start`
    (channel_id, start_at), a real UNIQUE CONSTRAINT in the schema. Re-running
    the same source therefore CANNOT duplicate the grid; the second run simply
    refreshes title/description/end_at on the rows it already wrote. That
    property belongs in the database and not in this file, so that it survives
    a bug here, two ingests racing, and whatever replaces this code later — the
    lesson `plan_channels` taught this project (migrations 005/008).
  * `INSERT … ON CONFLICT ON CONSTRAINT uq_epg_programmes_channel_start` names
    the constraint in SQL. Postgres resolves that through `pg_constraint`, where
    a unique INDEX of the same name does not appear — which is why migration 013
    uses `create_unique_constraint` and why there is a regression test pinning
    `contype = 'u'`. Two migrations on this branch (008, 010) exist only to
    repair that exact mistake elsewhere.
  * Delete-then-insert was rejected: it makes the guide briefly EMPTY for every
    channel on every refresh, which clients would render as "no programming",
    and it turns a partially-failed ingest into data loss instead of a stale row.

CHANNEL MAPPING — WHERE IT LIVES AND WHY
────────────────────────────────────────
XMLTV carries the FEED's channel ids (`<programme channel="tvn.ec">`), which
have nothing to do with our `channel_key`. The correspondence is stored in
`channels.epg_id` — a column that has existed since migration 003, is already
exposed to admins through `ChannelAdminOut`, and was never read by any code.

Chosen over the alternatives:
  * a new `epg_channel_map` table — a second place to keep in sync with
    `channels`, a migration, an admin surface, and a join, all to hold one
    string per channel that the channels table already has a column for;
  * a pure naming convention — free, but it forces the operator to rename our
    channel keys to match whatever the provider chose, and it breaks the day the
    provider renumbers.

So: `epg_id` is authoritative when set, and `channel_key` is accepted as a
fallback (case-insensitively) so a feed whose ids already match our keys needs
no configuration at all. Explicit `epg_id` always wins over the convention.

An XMLTV channel with NO correspondence is counted and skipped. It cannot fail
the ingest: a provider adding channels we do not carry is the normal case, not
an error, and 40 channels must not lose their guide because of the 41st.

SECURITY
────────
Parsing is delegated to app/services/xmltv_parser.py, which drives expat with
external entities, entity declarations and internal DTD subsets refused
outright. Read its module docstring: the source is a third-party URL parsed
unattended inside the production network, and the default XML parser turns that
into local-file disclosure, SSRF and an OOM kill. Nothing in this file may parse
XML by any other route.
"""
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models.channel import Channel
from app.models.epg_programme import (
    EPG_DESCRIPTION_MAX_LEN,
    EPG_TITLE_MAX_LEN,
    EPG_XMLTV_CHANNEL_ID_MAX_LEN,
    EpgProgramme,
)
from app.services.xmltv_parser import (
    EPG_MAX_SOURCE_BYTES,
    ParsedProgramme,
    XmltvSecurityError,
    parse_xmltv,
)

logger = logging.getLogger(__name__)

#: Name of the UNIQUE CONSTRAINT the upsert targets. A constant so the SQL and
#: the migration cannot drift apart silently.
EPG_UNIQUE_CONSTRAINT = "uq_epg_programmes_channel_start"

#: Rows per INSERT. Postgres caps a statement at 65535 bound parameters and each
#: row binds 8, so 2000 rows (~16k parameters) stays well clear while keeping the
#: number of round-trips low for a full week of guide.
_UPSERT_CHUNK = 2000


class EpgSourceError(Exception):
    """The configured EPG source could not be read (HTTP error, missing file,
    oversized body). Distinct from a parse failure so operators can tell
    "we never got the file" from "the file is not XMLTV"."""


@dataclass(slots=True)
class EpgIngestResult:
    parsed: int = 0
    upserted: int = 0
    pruned: int = 0
    unmapped_channel_ids: list[str] = field(default_factory=list)

    @property
    def skipped_unmapped(self) -> int:
        return self.parsed - self.upserted

    def __str__(self) -> str:
        return (
            f"parsed={self.parsed} upserted={self.upserted} "
            f"skipped_unmapped={self.skipped_unmapped} pruned={self.pruned} "
            f"unmapped_channels={self.unmapped_channel_ids or '-'}"
        )


# ── Source loading ───────────────────────────────────────────────────────────


async def load_source(source: str, timeout: float = 30.0) -> bytes:
    """Read the configured EPG source: an http(s) URL or a local file path.

    Both shapes are supported because both are real: a provider URL in
    production, a file dropped on a volume for the operator who receives the
    guide by other means (and for reproducing a bad ingest offline).

    The body is bounded at EPG_MAX_SOURCE_BYTES *while streaming*, not after the
    fact — a hostile or broken source must not be able to make this process
    allocate an unbounded response before anyone gets to check its size.
    """
    source = source.strip()
    if not source:
        raise EpgSourceError("EPG_SOURCE_URL is empty")

    if source.startswith(("http://", "https://")):
        return await _load_http(source, timeout)
    return _load_file(source)


async def _load_http(url: str, timeout: float) -> bytes:
    chunks: list[bytes] = []
    size = 0
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            async with client.stream("GET", url) as response:
                response.raise_for_status()
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > EPG_MAX_SOURCE_BYTES:
                        # Abort mid-download: the point of streaming is to stop
                        # paying for bytes we have already decided to refuse.
                        raise EpgSourceError(
                            f"EPG source {url!r} exceeds the "
                            f"{EPG_MAX_SOURCE_BYTES}-byte ceiling; download aborted"
                        )
                    chunks.append(chunk)
    except httpx.HTTPError as exc:
        raise EpgSourceError(f"could not fetch EPG source {url!r}: {exc}") from exc
    return b"".join(chunks)


def _load_file(path_str: str) -> bytes:
    path = Path(path_str)
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise EpgSourceError(f"could not read EPG source {path_str!r}: {exc}") from exc
    if size > EPG_MAX_SOURCE_BYTES:
        raise EpgSourceError(
            f"EPG source {path_str!r} is {size} bytes, over the "
            f"{EPG_MAX_SOURCE_BYTES}-byte ceiling"
        )
    try:
        return path.read_bytes()
    except OSError as exc:
        raise EpgSourceError(f"could not read EPG source {path_str!r}: {exc}") from exc


def _clip(value: str | None, cap: int) -> str | None:
    """Truncate external free text to its documented cap (app/models/epg_programme)."""
    if value is None:
        return None
    return value[:cap]


class EpgService:
    def __init__(self, db: AsyncSession):
        self.db = db

    # ── Reads ────────────────────────────────────────────────────────────────

    async def programmes_in_window(
        self,
        channel_id: uuid.UUID,
        window_start: datetime,
        window_end: datetime,
    ) -> list[EpgProgramme]:
        """The channel's programmes OVERLAPPING [window_start, window_end).

        Overlap (`start_at < window_end AND end_at > window_start`), not
        containment: the programme a viewer most wants is the one on air RIGHT
        NOW, which started before the window opened. Asking for programmes that
        *begin* inside the window would drop exactly that one.

        One index range scan. The (channel_id, start_at) btree behind
        `uq_epg_programmes_channel_start` has equality on the leading column and
        the range on the second, so this touches the few dozen rows of one
        channel's window rather than the tens of thousands in the table.
        """
        result = await self.db.execute(
            select(EpgProgramme)
            .where(
                EpgProgramme.channel_id == channel_id,
                EpgProgramme.start_at < window_end,
                EpgProgramme.end_at > window_start,
            )
            .order_by(EpgProgramme.start_at)
        )
        return list(result.scalars().all())

    # ── Ingest ───────────────────────────────────────────────────────────────

    async def channel_map(self) -> dict[str, uuid.UUID]:
        """{lowercased XMLTV channel id → our channel id}.

        Two passes on one query's rows so that an explicitly configured `epg_id`
        ALWAYS beats the `channel_key` convention, whatever order the rows came
        back in. Without the ordering, a channel whose key happens to equal
        another channel's epg_id would win or lose at random.
        """
        rows = (
            await self.db.execute(
                select(Channel.id, Channel.channel_key, Channel.epg_id)
            )
        ).all()

        mapping: dict[str, uuid.UUID] = {}
        for channel_id, channel_key, _ in rows:                 # convention
            if channel_key:
                mapping.setdefault(channel_key.strip().lower(), channel_id)
        for channel_id, _, epg_id in rows:                      # explicit wins
            if epg_id and epg_id.strip():
                mapping[epg_id.strip().lower()] = channel_id
        return mapping

    async def ingest(
        self, data: bytes, retention_days: int | None = None
    ) -> EpgIngestResult:
        """Parse an XMLTV document and upsert it. Idempotent by construction.

        Raises XmltvSecurityError / XmltvParseError for a hostile or unusable
        document — deliberately NOT swallowed, because "the feed tried to read
        our .env" must never be indistinguishable from "the guide is empty".

        Does not commit: the caller owns the transaction, so the whole guide
        lands as one unit and a failure halfway through leaves the previous
        guide intact.
        """
        programmes = parse_xmltv(data)
        result = EpgIngestResult(parsed=len(programmes))

        mapping = await self.channel_map()
        unmapped: set[str] = set()
        rows: list[dict] = []
        now = datetime.now(timezone.utc)

        for programme in programmes:
            channel_id = mapping.get(programme.xmltv_channel_id.strip().lower())
            if channel_id is None:
                # Not an error: providers carry channels we do not. Counted so
                # a *mapping* mistake (nothing matches at all) is visible in the
                # logs instead of looking like an empty feed.
                unmapped.add(programme.xmltv_channel_id)
                continue
            rows.append(_row(programme, channel_id, now))

        result.unmapped_channel_ids = sorted(unmapped)
        result.upserted = len(rows)

        for start in range(0, len(rows), _UPSERT_CHUNK):
            await self._upsert(rows[start : start + _UPSERT_CHUNK])

        if retention_days is not None and retention_days > 0:
            result.pruned = await self.prune(now - timedelta(days=retention_days))

        return result

    async def _upsert(self, rows: list[dict]) -> None:
        """One INSERT … ON CONFLICT ON CONSTRAINT for a chunk of rows.

        DO UPDATE, not DO NOTHING: a provider legitimately revises a programme's
        title, description or end time between refreshes, and DO NOTHING would
        pin the guide to whatever the first ingest happened to see.

        Note `id` / `created_at` are NOT in the update set — a revision must not
        renumber an existing row or rewrite when we first saw it.
        """
        if not rows:
            return
        stmt = pg_insert(EpgProgramme).values(rows)
        await self.db.execute(
            stmt.on_conflict_do_update(
                constraint=EPG_UNIQUE_CONSTRAINT,
                set_={
                    "end_at": stmt.excluded.end_at,
                    "title": stmt.excluded.title,
                    "description": stmt.excluded.description,
                    "xmltv_channel_id": stmt.excluded.xmltv_channel_id,
                    "updated_at": stmt.excluded.updated_at,
                },
            )
        )

    async def prune(self, cutoff: datetime) -> int:
        """Drop programmes that ended before `cutoff`. Returns rows removed.

        A guide refreshed every few hours would otherwise grow without bound.
        `ix_epg_programmes_end_at` exists for this statement — see the model's
        module docstring.
        """
        result = await self.db.execute(
            delete(EpgProgramme).where(EpgProgramme.end_at < cutoff)
        )
        return result.rowcount or 0


def _row(programme: ParsedProgramme, channel_id: uuid.UUID, now: datetime) -> dict:
    """ParsedProgramme → INSERT row.

    Every column is stated explicitly, including `id` and the timestamps:
    SQLAlchemy's Python-side `default=` is not reliably applied to a multi-values
    Core INSERT, and `onupdate=` never fires for the ON CONFLICT branch, so
    relying on either would leave nulls in a NOT NULL column.
    """
    return {
        "id": uuid.uuid4(),
        "channel_id": channel_id,
        "start_at": programme.start_at,
        "end_at": programme.end_at,
        "title": _clip(programme.title, EPG_TITLE_MAX_LEN),
        "description": _clip(programme.description, EPG_DESCRIPTION_MAX_LEN),
        "xmltv_channel_id": _clip(
            programme.xmltv_channel_id, EPG_XMLTV_CHANNEL_ID_MAX_LEN
        ),
        "created_at": now,
        "updated_at": now,
    }


# ── Background ingest entry point ────────────────────────────────────────────


async def run_ingest_once(db: AsyncSession) -> EpgIngestResult:
    """Fetch the configured source and ingest it, then COMMIT.

    The one place the source, the parser and the database meet, so the
    background loop in app/main.py stays as thin as the node-health monitor
    beside it. Refuses to run unless the feature is configured — an unconfigured
    deployment must never reach out to the network on a timer.
    """
    settings = get_settings()
    if not settings.epg_source_url.strip():
        raise EpgSourceError(
            "EPG ingest requested with no EPG_SOURCE_URL configured"
        )

    data = await load_source(
        settings.epg_source_url, timeout=settings.epg_fetch_timeout_seconds
    )
    service = EpgService(db)
    result = await service.ingest(data, retention_days=settings.epg_retention_days)
    await db.commit()

    if result.unmapped_channel_ids:
        logger.warning(
            "[epg] %d XMLTV channel(s) had no match in channels.epg_id or "
            "channels.channel_key and were skipped: %s",
            len(result.unmapped_channel_ids),
            ", ".join(result.unmapped_channel_ids[:20]),
        )
    if result.parsed and not result.upserted:
        # Every single programme was dropped: that is a mapping misconfiguration,
        # not a quiet no-op, and it is the failure an operator would otherwise
        # only notice as "the guide is still empty".
        logger.error(
            "[epg] parsed %d programme(s) but matched NONE to a channel — check "
            "channels.epg_id against the feed's channel ids",
            result.parsed,
        )
    return result


__all__ = [
    "EPG_UNIQUE_CONSTRAINT",
    "EpgIngestResult",
    "EpgService",
    "EpgSourceError",
    "XmltvSecurityError",
    "load_source",
    "run_ingest_once",
]
