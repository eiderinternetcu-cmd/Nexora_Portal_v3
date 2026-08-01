"""channels.censored + subscribers.parental_pin_hash (NX-PARENTAL)

Two additive columns, one per side of the parental gate:

  channels.censored (bool NOT NULL, default false)
      Marks an adult / age-restricted channel. `server_default` is kept, not
      dropped after the backfill, for the same reason 006 keeps its own: the
      column is NOT NULL, the ORM sets its default in Python only, and any
      direct SQL insert (imports, fixtures, an operator adding a channel by
      hand) would otherwise fail. Every existing row backfills to false, i.e.
      "nothing is censored yet", which is the state the catalog is in today.

  subscribers.parental_pin_hash (varchar(255) NULL)
      Argon2id hash of the household PIN. NULL = not configured, which the gate
      reads as "blocked", not "open" (see app/services/parental_service.py).
      Same width as subscribers.password_hash — argon2 output is ~97 chars.
      Nullable, so this migration needs no backfill and cannot fail on a
      populated table.

Both are pure ADD COLUMN: no rewrite of existing data, no constraint that could
reject a row already in the table, nothing that blocks on a large table beyond
the catalog lock ADD COLUMN takes (instant in PG11+ for a column with a
non-volatile default). Applying it changes no behaviour on its own — the gate
only engages under PARENTAL_CONTROL_ENFORCE, which defaults to False.

No index on channels.censored: nothing queries the catalog by it. It is read
off a row already fetched by channel_key, so an index would only cost writes.
The ORM must therefore not declare one either, or tests/test_migration_schema_
parity.py reports a missing index.

Revision ID: 012
Revises: 009
Create Date: 2026-07-31
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "012"
# Deliberately 009 and not the latest revision: several lanes are branching off
# 009 in parallel this session and the lead linearises them at merge time. Until
# then `alembic upgrade head` has multiple heads — use `alembic upgrade 012`.
down_revision: Union[str, None] = "009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "channels",
        sa.Column(
            "censored",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.add_column(
        "subscribers",
        sa.Column("parental_pin_hash", sa.String(length=255), nullable=True),
    )


def downgrade() -> None:
    """Drops both columns — and with parental_pin_hash, every configured PIN.

    Stated rather than glossed over: a down-then-up cycle does not restore the
    PINs, so every household would have to set one again. That is recoverable
    (the PIN is not a key to anything encrypted, and the gate fails closed
    meanwhile), but it is a real user-visible cost, not a free rollback.
    """
    op.drop_column("subscribers", "parental_pin_hash")
    op.drop_column("channels", "censored")
