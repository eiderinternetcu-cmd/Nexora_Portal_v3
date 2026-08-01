"""plans.name: repair the unique-index-instead-of-constraint bug (same class as
005/008), and create the missing ix_subscriptions_starts_at index.

THIS IS TWO INDEPENDENT FIXES, KEPT IN ONE MIGRATION because
tests/test_migration_schema_parity.py surfaced both as KNOWN_DIVERGENCES at
once and both are trivial, idempotent, non-destructive DDL repairs owned by
migrations/.

BUG 1 — plans.name (SAME BUG CLASS AS 005, THIS TIME PLANS INSTEAD OF
PLAN_CHANNELS)

  app/models/plan.py:15 declares `name: Mapped[str] = mapped_column(String(128),
  nullable=False, unique=True)` — an (unnamed) UNIQUE CONSTRAINT.
  migrations/versions/001_initial_schema.py:77 created it with
  `op.create_index("ix_plans_name", "plans", ["name"], unique=True)` — a unique
  INDEX. Uniqueness is enforced identically either way, so this has not (yet)
  corrupted data — but a unique INDEX is not a unique CONSTRAINT in Postgres:
  `INSERT ... ON CONFLICT ON CONSTRAINT <name>` raises `UndefinedObject` against
  a bare index. plan_channel_service.py already hit exactly this shape against
  plan_channels (fixed in 008); nothing today upserts against plans.name, but
  the moment something does, it 500s the same way. Fixed proactively rather than
  waiting for that call site to exist.

  The repair follows 008's pattern exactly, including its reasoning: query
  pg_constraint/pg_indexes directly rather than assume history (untrusted after
  005 proved a "fixed" migration can still leave old databases broken), cover
  all three possible starting states, and drop the index before adding the
  constraint since Postgres will not let both share a name and
  create_unique_constraint creates its own backing index.

  ORM side note: `unique=True` produces an UNNAMED UniqueConstraint (no
  naming_convention is configured on app.database.Base.metadata), so
  tests/test_migration_schema_parity.py compares this one by columns only, not
  by name — unlike plan_channels, whose ORM constraint IS explicitly named.
  The constraint below is still given an explicit name (uq_plans_name) so the
  migration itself is deterministic and repeatable; the test does not require
  any specific string.

BUG 2 — ix_subscriptions_starts_at (performance-only, no correctness impact)

  app/models/subscription.py:23 declares `starts_at ... index=True`. No
  migration has ever created the backing index (001_initial_schema.py:87 omits
  it — expires_at, is_active and the two FK columns all get one, starts_at does
  not). SQLAlchemy's default index-naming convention for `index=True` produces
  exactly `ix_subscriptions_starts_at`, so that is the name created here. Purely
  additive: a plain (non-unique) CREATE INDEX IF NOT EXISTS, nothing to migrate
  away from.

Revision ID: 010
Revises: 009
Create Date: 2026-07-31
"""
from typing import Sequence, Union

from alembic import op

revision: str = "010"
down_revision: Union[str, None] = "009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_PLANS_TABLE = "plans"
_PLANS_UQ_NAME = "uq_plans_name"
_PLANS_OLD_IX_NAME = "ix_plans_name"
_PLANS_COLS = ["name"]

_SUBS_TABLE = "subscriptions"
_SUBS_IX_NAME = "ix_subscriptions_starts_at"
_SUBS_COL = "starts_at"


def _has_constraint(conn, name: str, table: str) -> bool:
    return conn.exec_driver_sql(
        "SELECT 1 FROM pg_constraint "
        "WHERE conname = %(name)s AND conrelid = %(table)s::regclass",
        {"name": name, "table": table},
    ).first() is not None


def _has_index(conn, name: str, table: str) -> bool:
    return conn.exec_driver_sql(
        "SELECT 1 FROM pg_indexes WHERE indexname = %(name)s AND tablename = %(table)s",
        {"name": name, "table": table},
    ).first() is not None


def upgrade() -> None:
    conn = op.get_bind()

    # ── Bug 1: plans.name unique INDEX -> unique CONSTRAINT ──────────────────
    if _has_constraint(conn, _PLANS_UQ_NAME, _PLANS_TABLE):
        # Already correct -- either this migration already ran, or the table
        # was created directly from a fixed schema. Nothing to do.
        pass
    else:
        if _has_index(conn, _PLANS_OLD_IX_NAME, _PLANS_TABLE):
            # The 001 bug: a bare unique index squatting on ix_plans_name. Must
            # go first, or create_unique_constraint below collides on the name
            # the index leaves behind (Postgres backs a unique constraint with
            # an index of its own).
            op.drop_index(_PLANS_OLD_IX_NAME, table_name=_PLANS_TABLE)
        op.create_unique_constraint(_PLANS_UQ_NAME, _PLANS_TABLE, _PLANS_COLS)

    # ── Bug 2: create the missing ix_subscriptions_starts_at ─────────────────
    if not _has_index(conn, _SUBS_IX_NAME, _SUBS_TABLE):
        op.create_index(_SUBS_IX_NAME, _SUBS_TABLE, [_SUBS_COL])


def downgrade() -> None:
    """Reverts bug 1 to its pre-fix shape (a bare unique index instead of a
    constraint), for symmetry with 008's downgrade -- not because that shape is
    desirable. Drops the ix_subscriptions_starts_at index outright, since
    nothing depended on its absence either way.
    """
    conn = op.get_bind()

    if not _has_index(conn, _SUBS_IX_NAME, _SUBS_TABLE):
        pass
    else:
        op.drop_index(_SUBS_IX_NAME, table_name=_SUBS_TABLE)

    if _has_constraint(conn, _PLANS_UQ_NAME, _PLANS_TABLE):
        op.drop_constraint(_PLANS_UQ_NAME, _PLANS_TABLE, type_="unique")

    if not _has_index(conn, _PLANS_OLD_IX_NAME, _PLANS_TABLE):
        op.create_index(_PLANS_OLD_IX_NAME, _PLANS_TABLE, _PLANS_COLS, unique=True)
