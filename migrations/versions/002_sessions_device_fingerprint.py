"""Sessions table + device fingerprint columns

Revision ID: 002
Revises: 001
Create Date: 2026-05-17
"""
from typing import Sequence, Union
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID
from alembic import op

revision: str = "002"
down_revision: Union[str, None] = "001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── devices: extended fingerprint columns ────────────────────────────────
    op.add_column("devices", sa.Column("android_id", sa.String(64), nullable=True))
    op.add_column("devices", sa.Column("device_fingerprint", sa.String(128), nullable=True))
    op.add_column("devices", sa.Column("serial_hash", sa.String(64), nullable=True))

    op.create_index("ix_devices_android_id", "devices", ["android_id"])
    op.create_index("ix_devices_device_fingerprint", "devices", ["device_fingerprint"])

    # ── sessions ─────────────────────────────────────────────────────────────
    op.create_table(
        "sessions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "subscriber_id",
            UUID(as_uuid=True),
            sa.ForeignKey("subscribers.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "device_id",
            UUID(as_uuid=True),
            sa.ForeignKey("devices.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("device_fingerprint", sa.String(128), nullable=True),
        sa.Column("access_token_jti", sa.String(64), nullable=False),
        sa.Column("refresh_token_jti", sa.String(64), nullable=True),
        sa.Column("ip_address", sa.String(45), nullable=True),
        sa.Column("user_agent", sa.String(512), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_index("ix_sessions_subscriber_id", "sessions", ["subscriber_id"])
    op.create_index("ix_sessions_device_id", "sessions", ["device_id"])
    op.create_index("ix_sessions_device_fingerprint", "sessions", ["device_fingerprint"])
    op.create_index("ix_sessions_access_token_jti", "sessions", ["access_token_jti"], unique=True)
    op.create_index("ix_sessions_refresh_token_jti", "sessions", ["refresh_token_jti"])
    op.create_index("ix_sessions_expires_at", "sessions", ["expires_at"])
    op.create_index("ix_sessions_revoked_at", "sessions", ["revoked_at"])


def downgrade() -> None:
    op.drop_table("sessions")

    op.drop_index("ix_devices_device_fingerprint", "devices")
    op.drop_index("ix_devices_android_id", "devices")
    op.drop_column("devices", "serial_hash")
    op.drop_column("devices", "device_fingerprint")
    op.drop_column("devices", "android_id")
