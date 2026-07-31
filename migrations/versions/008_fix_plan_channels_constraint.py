"""plan_channels: repair uq_plan_channels_plan_channel on databases migrated
before the 005 fix (turn the bare unique INDEX into a real unique CONSTRAINT).

Context: revision 005 originally created uq_plan_channels_plan_channel with
`op.create_index(..., unique=True)`. That produces a unique INDEX, which is
NOT the same thing as a unique CONSTRAINT in Postgres.
app/services/plan_channel_service.py upserts with
`INSERT ... ON CONFLICT ON CONSTRAINT uq_plan_channels_plan_channel`, and
Postgres raises `UndefinedObject: constraint "..." does not exist` against a
bare index sharing that name — every write to a plan's channel whitelist
(the entitlement gate) 500s.

005 has since been corrected in place to use `op.create_unique_constraint`,
but Alembic never replays an already-applied revision. Any database that ran
the old 005 before that fix landed (in particular: production, which is at
head 007 with no 008 at time of writing) is stuck with the broken index and
needs this migration to repair it in place, without a destructive
downgrade/recreate of the table.

Idempotent and safe from BOTH starting states, so this is harmless whether
it lands on a database still carrying the old broken 005 (has index, no
constraint) or one created after the 005 fix (already has the constraint,
correctly, from 005 itself):
  - has index only  -> drop the index, add the constraint in its place
  - already has the constraint -> no-op
  - has neither (shouldn't happen; plan_channels is created in 005) -> add
    the constraint (Postgres creates its backing index automatically)

Order matters: Postgres will not let two objects share a name, and adding a
unique constraint creates its own backing index — so the old index must be
dropped BEFORE the constraint is added, never after.

Revision ID: 008
Revises: 007
Create Date: 2026-07-31
"""
from typing import Sequence, Union

from alembic import op

revision: str = "008"
down_revision: Union[str, None] = "007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLE = "plan_channels"
_NAME = "uq_plan_channels_plan_channel"
_COLS = ["plan_id", "channel_id"]


def _has_constraint(conn) -> bool:
    return conn.exec_driver_sql(
        "SELECT 1 FROM pg_constraint "
        "WHERE conname = %(name)s AND conrelid = %(table)s::regclass",
        {"name": _NAME, "table": _TABLE},
    ).first() is not None


def _has_index(conn) -> bool:
    return conn.exec_driver_sql(
        "SELECT 1 FROM pg_indexes WHERE indexname = %(name)s AND tablename = %(table)s",
        {"name": _NAME, "table": _TABLE},
    ).first() is not None


def upgrade() -> None:
    conn = op.get_bind()

    if _has_constraint(conn):
        # Already correct -- either this migration already ran, or the table
        # was created by the fixed 005 in the first place. Nothing to do.
        return

    if _has_index(conn):
        # Old broken 005: a bare unique index squatting on the name the
        # constraint needs. Must go first, or create_unique_constraint below
        # collides on the name.
        op.drop_index(_NAME, table_name=_TABLE)

    op.create_unique_constraint(_NAME, _TABLE, _COLS)


def downgrade() -> None:
    """Reverts to the pre-fix shape: a bare unique index instead of a
    constraint. This intentionally reproduces the historical (broken) 005
    output for symmetry with upgrade() -- it exists so `alembic downgrade`
    doesn't error out, not because that shape is desirable. Nothing in
    production is expected to ever run this downgrade.
    """
    conn = op.get_bind()

    if _has_constraint(conn):
        op.drop_constraint(_NAME, _TABLE, type_="unique")

    if not _has_index(conn):
        op.create_index(_NAME, _TABLE, _COLS, unique=True)
