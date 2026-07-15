"""M2 — immutable admin audit + correlation id."""
import httpx
import pytest
from httpx import ASGITransport
from sqlalchemy import select, text

from app.core.security import hash_password
from app.models.audit import AuditLog
from app.models.user import User, UserRole
from app.services.audit_service import AuditService
from app.services.auth_service import AuthService

pytestmark = pytest.mark.asyncio


async def test_login_is_audited(db_session, redis_client):
    db_session.add(User(username="admin_x", email="admin_x@test.local",
                        password_hash=hash_password("Secret123!"),
                        role=UserRole.admin, is_active=True))
    await db_session.commit()
    await AuthService(db_session, redis_client).login("admin_x", "Secret123!", ip="9.9.9.9")
    rows = (await db_session.execute(
        select(AuditLog).where(AuditLog.action == "auth.login"))).scalars().all()
    assert len(rows) == 1
    assert rows[0].actor_username == "admin_x" and rows[0].ip_address == "9.9.9.9"
    assert rows[0].details.get("role") == "admin"


async def test_audit_list_filter(db_session):
    svc = AuditService(db_session)
    await svc.log(action="auth.login", target_type="user", details={"role": "admin"})
    await svc.log(action="channel.update", target_type="channel")
    await db_session.commit()
    only_login = await svc.list(action="auth.login")
    assert len(only_login) == 1 and only_login[0].action == "auth.login"
    assert len(await svc.list()) == 2


async def test_audit_logs_are_append_only(db_session):
    # conftest builds the schema from metadata (no trigger), so install the
    # migration-007 trigger here to verify UPDATE/DELETE are blocked.
    await db_session.execute(text(
        "CREATE OR REPLACE FUNCTION nexora_audit_logs_immutable() RETURNS trigger AS $$ "
        "BEGIN RAISE EXCEPTION 'audit_logs is append-only: % is not allowed', TG_OP; END; "
        "$$ LANGUAGE plpgsql;"))
    await db_session.execute(text(
        "DROP TRIGGER IF EXISTS audit_logs_no_update_delete ON audit_logs;"
        "CREATE TRIGGER audit_logs_no_update_delete BEFORE UPDATE OR DELETE ON audit_logs "
        "FOR EACH ROW EXECUTE FUNCTION nexora_audit_logs_immutable();"))
    await db_session.commit()

    await AuditService(db_session).log(action="auth.login")
    await db_session.commit()

    with pytest.raises(Exception):
        await db_session.execute(text("UPDATE audit_logs SET action='tampered'"))
        await db_session.commit()
    await db_session.rollback()

    with pytest.raises(Exception):
        await db_session.execute(text("DELETE FROM audit_logs"))
        await db_session.commit()
    await db_session.rollback()

    # INSERT still works; row survives
    await AuditService(db_session).log(action="auth.logout")
    await db_session.commit()
    n = (await db_session.execute(select(AuditLog))).scalars().all()
    assert len(n) == 2


async def test_correlation_id_header(redis_client):
    from app.main import app
    from app.redis_client import get_redis
    app.dependency_overrides[get_redis] = lambda: redis_client
    try:
        async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get("/health")
            assert r.headers.get("X-Request-ID")  # generated
            r2 = await c.get("/health", headers={"X-Request-ID": "trace-abc-123"})
            assert r2.headers.get("X-Request-ID") == "trace-abc-123"  # echoed
    finally:
        app.dependency_overrides.clear()
