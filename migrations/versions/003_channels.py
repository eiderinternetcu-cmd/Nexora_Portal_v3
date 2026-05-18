"""Channels table

Revision ID: 003
Revises: 002
Create Date: 2026-05-17
"""
from typing import Sequence, Union
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID
from alembic import op

revision: str = "003"
down_revision: Union[str, None] = "002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "channels",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("channel_key", sa.String(64), nullable=False),
        sa.Column("number", sa.Integer, nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("category", sa.String(64), nullable=True),
        sa.Column("logo_url", sa.String(512), nullable=True),
        sa.Column("stream_key", sa.String(128), nullable=False),
        sa.Column("source_type", sa.String(16), nullable=False, server_default="manual"),
        sa.Column("source_url", sa.String(512), nullable=True),
        sa.Column("epg_id", sa.String(128), nullable=True),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("requires_subscription", sa.Boolean, nullable=False, server_default="true"),
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
    op.create_index("ix_channels_channel_key", "channels", ["channel_key"], unique=True)
    op.create_index("ix_channels_number", "channels", ["number"])
    op.create_index("ix_channels_is_active", "channels", ["is_active"])


def downgrade() -> None:
    op.drop_index("ix_channels_is_active", "channels")
    op.drop_index("ix_channels_number", "channels")
    op.drop_index("ix_channels_channel_key", "channels")
    op.drop_table("channels")
