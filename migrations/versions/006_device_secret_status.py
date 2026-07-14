"""devices: strong identity — device_secret_hash + status (M1)

Adds per-device secret storage (keyed hash, never the plaintext) and a status
column so device registration stops being an unconditional silent auto-add when
DEVICE_SECRET_ENFORCE is on. Backfills existing devices to 'active' so the
current (flag-off) behavior is unchanged.

Revision ID: 006
Revises: 005
Create Date: 2026-07-14
"""
from typing import Sequence, Union
import sqlalchemy as sa
from alembic import op

revision: str = "006"
down_revision: Union[str, None] = "005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "devices",
        sa.Column("device_secret_hash", sa.String(length=255), nullable=True),
    )
    # server_default 'active' backfills existing rows; keep the default so legacy
    # inserts (flag off) remain 'active' without app changes.
    op.add_column(
        "devices",
        sa.Column(
            "status",
            sa.String(length=16),
            nullable=False,
            server_default="active",
        ),
    )
    op.create_index("ix_devices_status", "devices", ["status"])


def downgrade() -> None:
    op.drop_index("ix_devices_status", "devices")
    op.drop_column("devices", "status")
    op.drop_column("devices", "device_secret_hash")
