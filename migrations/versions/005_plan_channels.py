"""plan_channels: per-channel entitlement (which channels a plan includes)

Closes the entitlement gap: until now an active subscription granted ALL
channels. plan_channels maps plan -> channel so PlaybackAuthorization can
enforce "channel is included in the subscriber's plan".

Revision ID: 005
Revises: 004
Create Date: 2026-06-27
"""
from typing import Sequence, Union
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID
from alembic import op

revision: str = "005"
down_revision: Union[str, None] = "004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "plan_channels",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "plan_id",
            UUID(as_uuid=True),
            sa.ForeignKey("plans.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "channel_id",
            UUID(as_uuid=True),
            sa.ForeignKey("channels.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("is_enabled", sa.Boolean, nullable=False, server_default="true"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        # admin users live in the `users` table; nullable for system/seed inserts
        sa.Column(
            "created_by_admin_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index(
        "uq_plan_channels_plan_channel",
        "plan_channels",
        ["plan_id", "channel_id"],
        unique=True,
    )
    op.create_index("ix_plan_channels_plan_id", "plan_channels", ["plan_id"])
    op.create_index("ix_plan_channels_channel_id", "plan_channels", ["channel_id"])


def downgrade() -> None:
    op.drop_index("ix_plan_channels_channel_id", "plan_channels")
    op.drop_index("ix_plan_channels_plan_id", "plan_channels")
    op.drop_index("uq_plan_channels_plan_channel", "plan_channels")
    op.drop_table("plan_channels")
