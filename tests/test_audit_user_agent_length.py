"""audit_logs.user_agent — the migration↔ORM divergence that 500s the login.

app/models/audit.py declares Text; 001_initial_schema.py created varchar(255);
auth_service.py truncated to 512 (a bound read off the ORM). A User-Agent of
256-512 characters therefore passed the truncation and overflowed the column:
StringDataRightTruncation → the audit INSERT fails → 500 on login.

The ordinary suite is structurally blind to this: tests/conftest.py builds the
schema with Base.metadata.create_all(), i.e. from the ORM, so the test database
always had the unbounded column the code assumed. The middle test below closes
that blind spot by reshaping the column to the production type first.
"""
import pytest
import sqlalchemy as sa
from sqlalchemy.exc import DBAPIError

from app.models.audit import (
    AuditLog,
    AUDIT_IP_ADDRESS_MAX_LEN,
    AUDIT_USER_AGENT_MAX_LEN,
)
from app.services.audit_service import AuditService

pytestmark = pytest.mark.asyncio

# Between 256 and 512: the exact window that passed the old truncation and then
# failed the INSERT. A real Android TV / STB navigator.userAgent lands here.
UA_400 = (
    "Mozilla/5.0 (Linux; Android 14; BRAVIA 4K VH2 Build/PTT1.220621.001; wv) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/120.0.6099.230 "
    "Safari/537.36 CrKey/1.56.500000 " + "x" * 200
)


def _count(db, **where):
    return sa.select(sa.func.count()).select_from(AuditLog.__table__).filter_by(**where)


async def test_long_user_agent_is_audited_without_blowing_up(db_session):
    """The regression test asked for: ~400 chars is written, not truncated, not
    an exception."""
    assert 256 < len(UA_400) <= AUDIT_USER_AGENT_MAX_LEN

    entry = await AuditService(db_session).log(
        action="auth.login",
        details={"outcome": "success"},
        ip_address="203.0.113.10",
        user_agent=UA_400,
    )
    await db_session.commit()

    assert entry.user_agent == UA_400
    assert len(entry.user_agent) == len(UA_400)


async def test_production_column_shape_reproduces_the_500_and_009_fixes_it(db_session):
    """Reproduce the real production column, show the failure, apply 009's DDL.

    Step 1 narrows the ORM-built column to varchar(255) — what
    001_initial_schema.py:138 actually created and what `\\d audit_logs` shows in
    production. Step 2 proves a 400-char User-Agent then fails the INSERT. Step 3
    applies exactly the change migration 009 makes and proves the same insert
    succeeds.

    Without migration 009 this test is the only thing in the suite that can see
    the bug at all.
    """
    conn = await db_session.connection()

    # 1. become production.
    await conn.exec_driver_sql(
        "ALTER TABLE audit_logs ALTER COLUMN user_agent TYPE varchar(255)"
    )

    # 2. the live 500: the value passes the application cap, the column refuses it.
    with pytest.raises(DBAPIError) as ei:
        await AuditService(db_session).log(action="auth.login", user_agent=UA_400)
        await db_session.flush()
    assert "too long" in str(ei.value).lower() or "truncation" in str(ei.value).lower()

    await db_session.rollback()

    # 3. exactly what 009.upgrade() does.
    conn = await db_session.connection()
    await conn.exec_driver_sql(
        "ALTER TABLE audit_logs ALTER COLUMN user_agent TYPE text"
    )

    entry = await AuditService(db_session).log(action="auth.login", user_agent=UA_400)
    await db_session.flush()
    assert entry.user_agent == UA_400


async def test_audit_service_bounds_both_client_supplied_fields(db_session):
    """The cap is enforced centrally, so no call site can forget it — the two
    `[:512]` literals in auth_service.py were the only ones that ever applied it,
    and ~20 other AuditService.log callers had none."""
    entry = await AuditService(db_session).log(
        action="auth.login_failed",
        ip_address="x" * 200,
        user_agent="u" * (AUDIT_USER_AGENT_MAX_LEN * 3),
    )
    await db_session.flush()

    assert len(entry.user_agent) == AUDIT_USER_AGENT_MAX_LEN
    assert len(entry.ip_address) == AUDIT_IP_ADDRESS_MAX_LEN


async def test_empty_strings_are_stored_as_null(db_session):
    """Preserved from the `(x or "")[:n] or None` idiom the call sites used."""
    entry = await AuditService(db_session).log(
        action="auth.login", ip_address="", user_agent=None
    )
    await db_session.flush()
    assert entry.ip_address is None
    assert entry.user_agent is None


# ── the divergence guard ─────────────────────────────────────────────────────

def test_caps_fit_their_columns():
    """The invariant whose violation caused all of this: an application cap that
    exceeds its column silently produces values that pass truncation and then
    fail the INSERT.

    _assert_fits enforces it at import time; this pins it so the guard itself
    cannot be dropped unnoticed.
    """
    from app.models import audit as audit_mod

    for column, cap in (
        ("user_agent", AUDIT_USER_AGENT_MAX_LEN),
        ("ip_address", AUDIT_IP_ADDRESS_MAX_LEN),
    ):
        length = getattr(AuditLog.__table__.c[column].type, "length", None)
        assert length is None or cap <= length, (
            f"audit_logs.{column} holds {length} but the cap is {cap}"
        )

    # user_agent is unbounded (Text), so no cap can overflow it — that is the
    # whole point of migration 009, and why _assert_fits stays quiet here.
    audit_mod._assert_fits("user_agent", 10_000_000)

    # ip_address IS bounded, so raising its cap past the column must fail loudly.
    # This is the shape the login 500 had: cap 512 over a varchar(255) column.
    with pytest.raises(RuntimeError, match="would pass truncation"):
        audit_mod._assert_fits("ip_address", AUDIT_IP_ADDRESS_MAX_LEN + 1)


def test_orm_declares_user_agent_unbounded():
    """What migration 009 aligns the database to. If someone narrows this back to
    String(n), 009 and this test are the record of why that is wrong."""
    assert isinstance(AuditLog.__table__.c.user_agent.type, sa.Text)
    assert AuditLog.__table__.c.user_agent.type.length is None
