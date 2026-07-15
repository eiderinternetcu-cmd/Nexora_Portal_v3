"""audit_logs: append-only (immutable) — block UPDATE/DELETE via trigger (M2)

Makes the admin audit trail tamper-evident at the DB level: INSERT stays allowed,
UPDATE and DELETE raise. App code never updates/deletes audit rows; this enforces
it even against direct SQL.

Revision ID: 007
Revises: 006
Create Date: 2026-07-14
"""
from typing import Sequence, Union
from alembic import op

revision: str = "007"
down_revision: Union[str, None] = "006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        CREATE OR REPLACE FUNCTION nexora_audit_logs_immutable()
        RETURNS trigger AS $$
        BEGIN
          RAISE EXCEPTION 'audit_logs is append-only: % is not allowed', TG_OP;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER audit_logs_no_update_delete
          BEFORE UPDATE OR DELETE ON audit_logs
          FOR EACH ROW EXECUTE FUNCTION nexora_audit_logs_immutable();
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS audit_logs_no_update_delete ON audit_logs;")
    op.execute("DROP FUNCTION IF EXISTS nexora_audit_logs_immutable();")
