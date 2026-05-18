"""channels: add flussonic_node and hls_path columns

flussonic_node — identifies which Flussonic node serves this channel
                 ('ec-main', 'co-main', etc.). Required for multi-node routing.
hls_path       — HLS playlist filename on the Flussonic node
                 ('index.m3u8' or 'video.m3u8'). Default: index.m3u8.

Revision ID: 004
Revises: 003
Create Date: 2026-05-18
"""
from typing import Sequence, Union
import sqlalchemy as sa
from alembic import op

revision: str = "004"
down_revision: Union[str, None] = "003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "channels",
        sa.Column(
            "flussonic_node",
            sa.String(64),
            nullable=False,
            server_default="ec-main",
        ),
    )
    op.add_column(
        "channels",
        sa.Column(
            "hls_path",
            sa.String(32),
            nullable=False,
            server_default="index.m3u8",
        ),
    )
    op.create_index("ix_channels_flussonic_node", "channels", ["flussonic_node"])


def downgrade() -> None:
    op.drop_index("ix_channels_flussonic_node", "channels")
    op.drop_column("channels", "hls_path")
    op.drop_column("channels", "flussonic_node")
