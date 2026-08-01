"""EPG programmes — the real programme guide, ingested from XMLTV (NX-EPG).

Replaces the hard-coded `_MOCK_EPG` dict in app/api/client/catalog.py, which
served three invented programmes for three channel keys and nothing at all for
the other 38 channels. Real clients rendered that as if it were the schedule.

SCHEMA DECISIONS
────────────────

uq_epg_programmes_channel_start — (channel_id, start_at) UNIQUE **CONSTRAINT**

  This is the no-duplicates guarantee, and it lives in the schema on purpose.
  "A channel airs exactly one programme starting at a given instant" is the
  natural key of XMLTV itself: `<programme start="…" channel="…">` is how the
  format identifies a broadcast. Re-ingesting the same source therefore cannot
  duplicate the grid even if the ingest code is wrong, is run twice
  concurrently, or is replaced later by something else — which is the lesson
  `plan_channels` taught this project the expensive way (see migrations 005 and
  008, and tests/test_migration_schema_parity.py).

  It MUST be a CONSTRAINT and not a bare unique INDEX. EpgService upserts with
  `INSERT … ON CONFLICT ON CONSTRAINT uq_epg_programmes_channel_start`, and
  Postgres resolves that clause through `pg_constraint`, where a unique index of
  the same name is invisible: the write would fail with
  `UndefinedObject: constraint "…" does not exist`. Migration 013 uses
  `op.create_unique_constraint` for exactly this reason — two migrations on this
  branch (008 and 010) exist solely to repair that mistake elsewhere in the
  schema, so it is not a hypothetical.

  The constraint pulls double duty as the ACCESS PATH. Postgres backs every
  unique constraint with a btree, here on (channel_id, start_at) — leading
  equality column, then the range column — which is precisely the shape the
  dominant query needs:

      WHERE channel_id = :ch AND start_at < :window_end AND end_at > :window_start

  With 41 channels × ~7 days of grid the table holds tens of thousands of rows;
  that index turns the per-channel window lookup into a single range scan over
  the few dozen rows of one channel instead of a seq scan over all of them. No
  separate index is declared for the window query because it would be a byte-for
  -byte duplicate of the constraint's own index — pure write amplification.

ix_epg_programmes_end_at

  The one index that is NOT covered by the constraint. It serves the retention
  sweep, `DELETE … WHERE end_at < :cutoff`, which EpgService runs after every
  ingest so a guide refreshed every few hours does not accumulate months of dead
  rows. Without it that DELETE degrades to a full scan on a table that only ever
  grows.

title/description are TEXT, not VARCHAR(n)

  Both are free text from an EXTERNAL feed we do not control. migration 009
  (audit_logs.user_agent) is this project's record of what a length-capped
  column does with external text: values that pass an application-side
  truncation and then overflow the column turn an INSERT into a 500. The column
  is unbounded and the bound lives in ONE place next to the model, below, exactly
  as app/models/audit.py does it — so the two can never drift apart again.

No ORM relationship to Channel

  Deliberate. A `programmes` collection on Channel would be a lazy-loaded list
  of tens of thousands of rows one attribute access away from the catalog
  endpoint. Programmes are always read through EpgService with an explicit
  channel + time window. The FK still carries ON DELETE CASCADE, so deleting a
  channel takes its grid with it in the database, not in Python.
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class EpgProgramme(Base):
    __tablename__ = "epg_programmes"
    __table_args__ = (
        UniqueConstraint(
            "channel_id", "start_at", name="uq_epg_programmes_channel_start"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    channel_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("channels.id", ondelete="CASCADE"),
        nullable=False,
    )
    start_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    end_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)

    #: The `channel` attribute of the XMLTV `<programme>` that produced this row.
    #: Kept for provenance: when an operator asks "why is this show on the wrong
    #: channel", the answer is in the feed's own id, not in ours.
    xmltv_channel_id: Mapped[str | None] = mapped_column(String(128))

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    def __repr__(self) -> str:
        return (
            f"<EpgProgramme channel={self.channel_id} "
            f"{self.start_at:%Y-%m-%dT%H:%M}→{self.end_at:%H:%M} {self.title!r}>"
        )


# ── Application-side caps for external free text ─────────────────────────────
#
# The columns are unbounded (TEXT) so a long title can never turn an ingest into
# a failed INSERT — see the module docstring. These caps exist for a different
# reason: an XMLTV feed is fetched over the network from a third party, and a
# hostile or broken source must not be able to write rows of arbitrary size into
# our database. Truncation happens once, in EpgService, on the way in.
EPG_TITLE_MAX_LEN = 512
EPG_DESCRIPTION_MAX_LEN = 4096
#: `channel` attribute of an XMLTV <programme> — this one IS a bounded column.
EPG_XMLTV_CHANNEL_ID_MAX_LEN = 128


def _assert_fits(column_name: str, cap: int) -> None:
    """Fail at import if a cap could no longer fit its column.

    Same guard, and the same reasoning, as app/models/audit.py: `length` is None
    for TEXT and an int for String(n), so narrowing a column below its cap (or
    raising a cap above a bounded column) stops being shippable instead of
    surfacing later as StringDataRightTruncation inside the background ingest,
    where nobody is watching.
    """
    length = getattr(EpgProgramme.__table__.c[column_name].type, "length", None)
    if length is not None and cap > length:
        raise RuntimeError(
            f"epg_programmes.{column_name} holds {length} chars but the "
            f"application cap is {cap}: values between {length + 1} and {cap} "
            "would pass truncation and then fail the INSERT. Align the column "
            "(migration) and the cap in app/models/epg_programme.py."
        )


_assert_fits("title", EPG_TITLE_MAX_LEN)
_assert_fits("description", EPG_DESCRIPTION_MAX_LEN)
_assert_fits("xmltv_channel_id", EPG_XMLTV_CHANNEL_ID_MAX_LEN)
