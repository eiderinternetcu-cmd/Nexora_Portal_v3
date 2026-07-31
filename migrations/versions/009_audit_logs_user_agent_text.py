"""audit_logs.user_agent: varchar(255) -> TEXT, aligning the column with the ORM.

THE BUG (live 500 on the login path)

  app/models/audit.py declares `user_agent` as Text (unbounded).
  migrations/versions/001_initial_schema.py:138 created it as sa.String(255).
  The real column in production is `character varying(255)`.

  app/services/auth_service.py truncated the header to 512 before writing the
  audit row — a bound chosen by reading the ORM, which says Text. So a
  User-Agent between 256 and 512 characters passes the truncation and then
  overflows the column: Postgres raises StringDataRightTruncation, the INSERT
  fails and the login returns 500. TV and STB clients are exactly the ones that
  send the longest navigator.userAgent — this project already had to raise
  `os_version` from 32 to 512 in app/schemas/client.py for the same reason.

  No test could catch it: tests/conftest.py builds the schema with
  Base.metadata.create_all(), i.e. from the ORM, so the test database always had
  the Text column the code assumed. Only a migration-built database has the
  varchar(255).

  Note that 001 is internally inconsistent rather than deliberate: line 113 of
  the same file creates `devices.user_agent` as sa.Text. Text is the intent.

WHY TEXT AND NOT A WIDER VARCHAR

  The ORM is the contract the application code is written against, so the column
  follows the ORM rather than the other way round. In Postgres `text` and
  `varchar(n)` are the same storage; a length limit buys nothing here and is what
  produced the divergence in the first place. The application-side cap now lives
  in ONE place, next to the model (app/models/audit.py: AUDIT_USER_AGENT_MAX_LEN),
  and is applied centrally in AuditService.log.

COST

  varchar(255) -> text is binary-coercible: Postgres updates the catalog and does
  NOT rewrite the table, so this is cheap even with data. It still takes a brief
  ACCESS EXCLUSIVE lock on audit_logs, which is a write-only table on the login
  path — sub-second on any realistic trail size.

Revision ID: 009
Revises: 008
Create Date: 2026-07-31
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "009"
down_revision: Union[str, None] = "008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLE = "audit_logs"
_COLUMN = "user_agent"
_LEGACY_LEN = 255


def _data_type(conn) -> str | None:
    """Current SQL type of the column, or None if the table/column is absent."""
    return conn.exec_driver_sql(
        "SELECT data_type FROM information_schema.columns "
        "WHERE table_name = %(t)s AND column_name = %(c)s",
        {"t": _TABLE, "c": _COLUMN},
    ).scalar()


def upgrade() -> None:
    conn = op.get_bind()
    current = _data_type(conn)

    # Idempotent, and a no-op on a database whose schema came from the ORM
    # (create_all already produces `text`) — same discipline as 008, which has to
    # land safely on databases in either of two historical states.
    if current is None or current == "text":
        return

    op.alter_column(
        _TABLE,
        _COLUMN,
        type_=sa.Text(),
        existing_type=sa.String(_LEGACY_LEN),
        existing_nullable=True,
    )


def downgrade() -> None:
    """Narrow back to varchar(255) — and REFUSE if that would lose audit data.

    The narrowing is only safe while every stored value still fits. Postgres
    would otherwise raise StringDataRightTruncation mid-ALTER; the check below
    turns that into an explicit, readable failure before anything is touched.

    It deliberately does NOT truncate to force the rollback through:

      - migration 007 makes audit_logs append-only, with a BEFORE UPDATE OR
        DELETE trigger that raises. The obvious repair (`UPDATE audit_logs SET
        user_agent = left(user_agent, 255)`) is blocked by design.
      - the `ALTER ... TYPE varchar(255) USING left(user_agent, 255)` form would
        slip past that trigger (DDL does not fire row triggers) and silently
        rewrite the content of an audit trail whose whole purpose is to be
        tamper-evident. A schema rollback is not a licence to edit history.

    So with over-long rows present this downgrade is irreversible by design, and
    says so. Recovering the varchar(255) shape then requires a deliberate,
    audited decision about those rows — not a side effect of `alembic downgrade`.
    """
    conn = op.get_bind()

    if _data_type(conn) != "text":
        return

    too_long = conn.exec_driver_sql(
        "SELECT count(*) FROM audit_logs WHERE length(user_agent) > %(n)s",
        {"n": _LEGACY_LEN},
    ).scalar()

    if too_long:
        raise RuntimeError(
            f"Refusing to downgrade {_TABLE}.{_COLUMN} to varchar({_LEGACY_LEN}): "
            f"{too_long} row(s) are longer and would be truncated. audit_logs is "
            "append-only (migration 007); shortening stored entries is a "
            "deliberate decision, not a schema rollback. Resolve those rows "
            "explicitly first, then re-run the downgrade."
        )

    op.alter_column(
        _TABLE,
        _COLUMN,
        type_=sa.String(_LEGACY_LEN),
        existing_type=sa.Text(),
        existing_nullable=True,
    )
