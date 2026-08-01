"""linealizar las cuatro cabezas paralelas

Revision ID: 014
Revises: 010, 011, 012, 013
Create Date: 2026-08-01 01:12:01.453492

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '014'
down_revision: Union[str, None] = ('010', '011', '012', '013')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
