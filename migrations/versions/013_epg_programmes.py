"""epg_programmes — the real programme guide, replacing the mock EPG (NX-EPG).

WHAT THIS CREATES AND WHY EACH PIECE EXISTS
───────────────────────────────────────────

`GET /api/client/catalog/channels/{key}/epg` served a hard-coded dict of three
invented programmes for three of the 41 channels. This table is where the
ingested XMLTV guide actually lives. See app/models/epg_programme.py for the
full reasoning; the load-bearing points, restated here because a migration is
read on its own during an incident:

uq_epg_programmes_channel_start — UNIQUE **CONSTRAINT** on (channel_id, start_at)

  1. It is the NO-DUPLICATES guarantee, and it is in the schema rather than in
     the ingest code on purpose. "One channel airs one programme starting at a
     given instant" is XMLTV's own notion of identity, so re-ingesting the same
     source cannot duplicate the grid even if the ingest is buggy, runs twice, or
     is replaced by something else later. plan_channels is why this project now
     writes it down (005 → 008).

  2. It is created with `op.create_unique_constraint`, NOT
     `op.create_index(..., unique=True)`. THIS DISTINCTION IS THE BUG THIS BRANCH
     HAS ALREADY FIXED TWICE — 008 (plan_channels, from 005) and 010 (plans.name,
     from 001). app/services/epg_service.py upserts with
     `INSERT … ON CONFLICT ON CONSTRAINT uq_epg_programmes_channel_start`, which
     Postgres resolves through `pg_constraint`; a unique INDEX of the same name
     does not appear there and every ingest would die with
     `UndefinedObject: constraint "…" does not exist`. A unique index enforces
     the same uniqueness and would look correct in psql — which is exactly how
     this got through twice. tests/test_epg.py pins `pg_constraint.contype='u'`
     against a database built by this migration.

  3. Postgres backs the constraint with a btree on (channel_id, start_at), and
     that index IS the access path for the dominant query — equality on
     channel_id, range on start_at:

         WHERE channel_id = :ch AND start_at < :end AND end_at > :start

     41 channels × ~7 days of guide is tens of thousands of rows, so this is the
     difference between scanning one channel's few dozen rows and scanning the
     table. No separate index is created for it: it would be a byte-for-byte
     duplicate of the constraint's own index, doubling the write cost of every
     ingest for nothing.

ix_epg_programmes_end_at

  The only index NOT already provided by the constraint. It serves the retention
  sweep `DELETE FROM epg_programmes WHERE end_at < :cutoff`, which runs after
  every ingest so a guide refreshed every 6h does not accumulate months of dead
  rows. Without it that DELETE is a full scan on a table that only ever grows.

channel_id → channels.id ON DELETE CASCADE

  A deleted channel must take its grid with it. Doing that in the database rather
  than through an ORM relationship keeps tens of thousands of rows from ever
  being loaded into Python; there is deliberately no `Channel.programmes`
  collection (see the model).

title / description are TEXT, not VARCHAR(n)

  Both are free text from a third-party feed. Migration 009 is this project's
  record of what a length-capped column does with external text: a value that
  passes an application-side truncation and then overflows the column turns the
  INSERT into a 500. The bound lives once, beside the model
  (app/models/epg_programme.py: EPG_TITLE_MAX_LEN / EPG_DESCRIPTION_MAX_LEN),
  guarded by an import-time assertion so the cap and the column cannot drift.

NOTE ON `down_revision`
───────────────────────
This revision hangs off "009" deliberately. Four lanes are adding schema on this
branch at once (010, 011, 012, 013), all rooted at 009, and the lead linearises
them at merge time. Until then the repository has several heads, so
`alembic upgrade head` fails by design — use `alembic upgrade 013` to apply this
one. Nothing here depends on 010/011/012: it creates a brand-new table and
touches no existing object.

Revision ID: 013
Revises: 009
Create Date: 2026-07-31
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "013"
down_revision: Union[str, None] = "009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLE = "epg_programmes"
_UQ_NAME = "uq_epg_programmes_channel_start"
_UQ_COLS = ["channel_id", "start_at"]
_IX_END_AT = "ix_epg_programmes_end_at"


def upgrade() -> None:
    op.create_table(
        _TABLE,
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "channel_id",
            UUID(as_uuid=True),
            sa.ForeignKey("channels.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("start_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("end_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("title", sa.Text, nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("xmltv_channel_id", sa.String(128), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
    )

    # A real UNIQUE CONSTRAINT. `create_index(..., unique=True)` would enforce
    # the same uniqueness and break `ON CONFLICT ON CONSTRAINT` — see the module
    # docstring, and migrations 008 and 010, which exist to undo precisely that.
    op.create_unique_constraint(_UQ_NAME, _TABLE, _UQ_COLS)

    # Retention sweep only. The (channel_id, start_at) read path is already
    # covered by the constraint's own backing index.
    op.create_index(_IX_END_AT, _TABLE, ["end_at"])


def downgrade() -> None:
    # Dropping the table takes its indexes, its unique constraint and its foreign
    # key with it, so they are not dropped individually. This is a pure data loss
    # rollback and that is acceptable here in a way it is not for 009: the guide
    # is a CACHE of a third-party feed, fully reconstructible by re-running the
    # ingest against the same source. Nothing else references epg_programmes.
    op.drop_table(_TABLE)
