"""NX-AUTH — admin login lockout + login audit.

Covers the AC "credenciales malas → 401; login admin auditado":
  - LOGIN_LOCKOUT_ENABLED=False (default) → behavior is unchanged (legacy path).
  - LOGIN_LOCKOUT_ENABLED=True  → N-1 failures still answer 401, the Nth arms the
    block, and the blocked attempt is INDISTINGUISHABLE from a wrong password.
  - a successful login resets the counter.
  - every attempt lands in audit_log, and no row ever carries the password or a
    token.
"""
import pytest
from sqlalchemy import select

from app.core.security import hash_password
from app.models.audit import AuditLog
from app.models.user import User, UserRole
from app.redis_client import key_lockout, key_login_attempts
from app.services import auth_service as auth_mod
from app.services.auth_service import AuthService

pytestmark = pytest.mark.asyncio

PASSWORD = "Sup3rSecret!pw"
WRONG = "definitely-not-the-password"


async def _make_admin(db_session, username="admin_lk", active=True):
    user = User(
        username=username,
        email=f"{username}@test.local",
        password_hash=hash_password(PASSWORD),
        role=UserRole.admin,
        is_active=active,
    )
    db_session.add(user)
    await db_session.commit()
    return user


def _enable(monkeypatch, max_attempts=3, window=900, duration=900):
    monkeypatch.setattr(auth_mod.settings, "login_lockout_enabled", True)
    monkeypatch.setattr(auth_mod.settings, "login_lockout_max_attempts", max_attempts)
    monkeypatch.setattr(auth_mod.settings, "login_lockout_window_seconds", window)
    monkeypatch.setattr(auth_mod.settings, "login_lockout_duration_seconds", duration)


async def _fail_login(svc, username, password=WRONG, ip="10.0.0.1"):
    """Attempt a login expected to fail; return the raised HTTPException."""
    with pytest.raises(Exception) as exc:
        await svc.login(username, password, ip=ip, user_agent="pytest-UA/1.0")
    return exc.value


# ── Lockout: enabled ────────────────────────────────────────────────────────

async def test_first_failures_are_401_and_nth_arms_the_lockout(
    db_session, redis_client, monkeypatch
):
    _enable(monkeypatch, max_attempts=3)
    await _make_admin(db_session)
    svc = AuthService(db_session, redis_client)

    # N-1 failures: plain 401, account still open.
    for _ in range(2):
        err = await _fail_login(svc, "admin_lk")
        assert err.status_code == 401
    assert not await redis_client.exists(key_lockout("admin:admin_lk"))

    # Nth failure: still a 401, but the block is now armed.
    err = await _fail_login(svc, "admin_lk")
    assert err.status_code == 401
    assert await redis_client.exists(key_lockout("admin:admin_lk"))
    # Duration comes from LOGIN_LOCKOUT_DURATION_SECONDS, not the count window.
    assert 0 < await redis_client.ttl(key_lockout("admin:admin_lk")) <= 900


async def test_locked_account_rejects_the_correct_password(
    db_session, redis_client, monkeypatch
):
    """The whole point: once blocked, even valid credentials do not get in."""
    _enable(monkeypatch, max_attempts=3)
    await _make_admin(db_session)
    svc = AuthService(db_session, redis_client)

    for _ in range(3):
        await _fail_login(svc, "admin_lk")

    err = await _fail_login(svc, "admin_lk", password=PASSWORD)
    assert err.status_code == 401


async def test_blocked_response_is_indistinguishable_from_a_bad_password(
    db_session, redis_client, monkeypatch
):
    """No enumeration and no lockout signal: a blocked attempt, a wrong password
    and a username that does not exist must all answer the exact same 401."""
    _enable(monkeypatch, max_attempts=3)
    await _make_admin(db_session)
    svc = AuthService(db_session, redis_client)

    bad_password = await _fail_login(svc, "admin_lk")
    unknown_user = await _fail_login(svc, "no_such_admin_at_all")

    for _ in range(3):
        await _fail_login(svc, "admin_lk")
    blocked = await _fail_login(svc, "admin_lk")

    assert blocked.status_code == bad_password.status_code == unknown_user.status_code == 401
    assert blocked.detail == bad_password.detail == unknown_user.detail
    assert blocked.headers == bad_password.headers
    # Nothing that hints at a lockout, a countdown, or the account's existence.
    assert "lock" not in str(blocked.detail).lower()
    assert "attempt" not in str(blocked.detail).lower()


async def test_lockout_arms_for_unknown_usernames_too(
    db_session, redis_client, monkeypatch
):
    """Why the response above cannot leak: the counter does not care whether the
    account exists, so a blocked answer proves nothing about it."""
    _enable(monkeypatch, max_attempts=3)
    svc = AuthService(db_session, redis_client)

    for _ in range(3):
        await _fail_login(svc, "ghost_admin")
    assert await redis_client.exists(key_lockout("admin:ghost_admin"))


async def test_blocked_attempts_do_not_extend_the_block(
    db_session, redis_client, monkeypatch
):
    """An attacker hammering a blocked account must not be able to hold it locked
    forever: the counter is dropped when the block arms."""
    _enable(monkeypatch, max_attempts=3, duration=900)
    await _make_admin(db_session)
    svc = AuthService(db_session, redis_client)

    for _ in range(3):
        await _fail_login(svc, "admin_lk")
    ttl_at_arm = await redis_client.ttl(key_lockout("admin:admin_lk"))

    for _ in range(5):
        await _fail_login(svc, "admin_lk")

    assert await redis_client.ttl(key_lockout("admin:admin_lk")) <= ttl_at_arm
    assert not await redis_client.exists(key_login_attempts("admin:admin_lk"))


async def test_successful_login_resets_the_counter(
    db_session, redis_client, monkeypatch
):
    _enable(monkeypatch, max_attempts=3)
    await _make_admin(db_session)
    svc = AuthService(db_session, redis_client)

    for _ in range(2):
        await _fail_login(svc, "admin_lk")
    assert await redis_client.exists(key_login_attempts("admin:admin_lk"))

    tokens = await svc.login("admin_lk", PASSWORD, ip="10.0.0.1", user_agent="pytest-UA/1.0")
    assert tokens.access_token
    assert not await redis_client.exists(key_login_attempts("admin:admin_lk"))

    # Counter really restarted: two more failures still do not block.
    for _ in range(2):
        await _fail_login(svc, "admin_lk")
    assert not await redis_client.exists(key_lockout("admin:admin_lk"))


async def test_lockout_is_scoped_to_one_username(
    db_session, redis_client, monkeypatch
):
    """Per-username, not per-IP: hammering one account from an IP must not lock
    a different admin logging in from that same address."""
    _enable(monkeypatch, max_attempts=3)
    await _make_admin(db_session, "admin_lk")
    await _make_admin(db_session, "admin_other")
    svc = AuthService(db_session, redis_client)

    for _ in range(4):
        await _fail_login(svc, "admin_lk", ip="10.0.0.1")

    tokens = await svc.login("admin_other", PASSWORD, ip="10.0.0.1", user_agent="pytest-UA/1.0")
    assert tokens.access_token


# ── Lockout: disabled (default) ─────────────────────────────────────────────

async def test_flag_off_leaves_the_hardened_path_inert(
    db_session, redis_client, monkeypatch
):
    """Default deploy state: the new keys are never written and a good password
    keeps working after failures well past the new threshold."""
    monkeypatch.setattr(auth_mod.settings, "login_lockout_enabled", False)
    monkeypatch.setattr(auth_mod.settings, "login_lockout_max_attempts", 3)
    # Keep the legacy threshold out of the way so this test isolates the flag.
    monkeypatch.setattr(auth_mod.settings, "max_login_attempts", 50)
    await _make_admin(db_session)
    svc = AuthService(db_session, redis_client)

    for _ in range(5):
        err = await _fail_login(svc, "admin_lk")
        assert err.status_code == 401

    assert not await redis_client.exists(key_lockout("admin:admin_lk"))
    assert not await redis_client.exists(key_login_attempts("admin:admin_lk"))

    tokens = await svc.login("admin_lk", PASSWORD, ip="10.0.0.1", user_agent="pytest-UA/1.0")
    assert tokens.access_token


async def test_flag_off_keeps_the_legacy_423_lockout(
    db_session, redis_client, monkeypatch
):
    """Deploying with the default must NOT remove the brake production runs
    today — the legacy username+IP counters are still in charge."""
    monkeypatch.setattr(auth_mod.settings, "login_lockout_enabled", False)
    monkeypatch.setattr(auth_mod.settings, "max_login_attempts", 3)
    await _make_admin(db_session)
    svc = AuthService(db_session, redis_client)

    for _ in range(3):
        await _fail_login(svc, "admin_lk")

    assert await redis_client.exists(key_lockout("admin_lk"))
    err = await _fail_login(svc, "admin_lk", password=PASSWORD)
    assert err.status_code == 423


# ── Audit ───────────────────────────────────────────────────────────────────

async def _audit_rows(db_session, action=None):
    q = select(AuditLog)
    if action:
        q = q.where(AuditLog.action == action)
    return list((await db_session.execute(q)).scalars().all())


async def test_failed_login_is_audited(db_session, redis_client, monkeypatch):
    _enable(monkeypatch, max_attempts=3)
    user = await _make_admin(db_session)
    svc = AuthService(db_session, redis_client)

    await _fail_login(svc, "admin_lk", ip="203.0.113.7")

    rows = await _audit_rows(db_session, "auth.login_failed")
    assert len(rows) == 1
    row = rows[0]
    assert row.ip_address == "203.0.113.7"
    assert row.user_agent == "pytest-UA/1.0"
    assert row.details["outcome"] == "failure"
    assert row.details["reason"] == "invalid_credentials"
    assert row.details["username"] == "admin_lk"
    # Resolved account → the FK is populated so the trail is queryable per user.
    assert row.actor_id == user.id


async def test_failed_login_for_unknown_user_is_audited_without_an_actor(
    db_session, redis_client, monkeypatch
):
    _enable(monkeypatch, max_attempts=3)
    svc = AuthService(db_session, redis_client)

    await _fail_login(svc, "ghost_admin")

    rows = await _audit_rows(db_session, "auth.login_failed")
    assert len(rows) == 1
    assert rows[0].actor_id is None
    assert rows[0].details["reason"] == "unknown_user"
    # The attempted name is still recorded — that is what makes a spray visible.
    assert rows[0].details["username"] == "ghost_admin"


async def test_blocked_attempt_is_audited_under_its_own_action(
    db_session, redis_client, monkeypatch
):
    """The response says nothing, so the audit trail is the only place an
    operator can tell a lockout from an ordinary bad password."""
    _enable(monkeypatch, max_attempts=3)
    await _make_admin(db_session)
    svc = AuthService(db_session, redis_client)

    for _ in range(3):
        await _fail_login(svc, "admin_lk")
    await _fail_login(svc, "admin_lk")

    blocked = await _audit_rows(db_session, "auth.login_blocked")
    assert len(blocked) == 1
    assert blocked[0].details["reason"] == "locked_out"
    assert blocked[0].details["username"] == "admin_lk"
    assert len(await _audit_rows(db_session, "auth.login_failed")) == 3


async def test_disabled_account_is_audited_and_not_counted(
    db_session, redis_client, monkeypatch
):
    _enable(monkeypatch, max_attempts=3)
    await _make_admin(db_session, "admin_off", active=False)
    svc = AuthService(db_session, redis_client)

    for _ in range(4):
        err = await _fail_login(svc, "admin_off", password=PASSWORD)
        assert err.status_code == 401

    rows = await _audit_rows(db_session, "auth.login_failed")
    assert len(rows) == 4
    assert all(r.details["reason"] == "account_disabled" for r in rows)
    # Correct credentials against a disabled account are not a brute force.
    assert not await redis_client.exists(key_lockout("admin:admin_off"))


async def test_successful_login_audit_records_ip_and_user_agent(
    db_session, redis_client, monkeypatch
):
    _enable(monkeypatch, max_attempts=3)
    await _make_admin(db_session)
    svc = AuthService(db_session, redis_client)

    await svc.login("admin_lk", PASSWORD, ip="198.51.100.4", user_agent="pytest-UA/1.0")

    rows = await _audit_rows(db_session, "auth.login")
    assert len(rows) == 1
    assert rows[0].actor_username == "admin_lk"
    assert rows[0].ip_address == "198.51.100.4"
    assert rows[0].user_agent == "pytest-UA/1.0"
    assert rows[0].details["outcome"] == "success"
    assert rows[0].details["role"] == "admin"


async def test_no_audit_row_ever_contains_a_secret(
    db_session, redis_client, monkeypatch
):
    """Password and issued tokens must not reach the trail through any field —
    details, user_agent or ip_address."""
    _enable(monkeypatch, max_attempts=3)
    await _make_admin(db_session, "admin_lk")
    await _make_admin(db_session, "admin_two")
    await _make_admin(db_session, "admin_off", active=False)
    svc = AuthService(db_session, redis_client)

    # One row of every kind the login path can write.
    await _fail_login(svc, "admin_lk")                          # invalid_credentials
    await _fail_login(svc, "ghost_admin")                       # unknown_user
    await _fail_login(svc, "admin_off", password=PASSWORD)      # account_disabled
    for _ in range(3):
        await _fail_login(svc, "admin_two")                     # arms the block
    await _fail_login(svc, "admin_two")                         # locked_out
    tokens = await svc.login("admin_lk", PASSWORD, ip="10.0.0.1", user_agent="pytest-UA/1.0")

    actions = {r.action for r in await _audit_rows(db_session)}
    assert actions == {"auth.login", "auth.login_failed", "auth.login_blocked"}

    rows = await _audit_rows(db_session)
    assert rows, "expected audit rows to inspect"
    for row in rows:
        blob = f"{row.details}|{row.user_agent}|{row.ip_address}|{row.target_id}"
        assert PASSWORD not in blob
        assert WRONG not in blob
        assert tokens.access_token not in blob
        assert tokens.refresh_token not in blob
        assert "password" not in str(row.details).lower()
        assert "token" not in str(row.details).lower()


async def test_audit_survives_the_append_only_trigger(
    db_session, redis_client, monkeypatch
):
    """Login auditing only ever INSERTs, so it works under migration 007's
    append-only trigger (conftest builds the schema without it)."""
    from sqlalchemy import text

    await db_session.execute(text(
        "CREATE OR REPLACE FUNCTION nexora_audit_logs_immutable() RETURNS trigger AS $$ "
        "BEGIN RAISE EXCEPTION 'audit_logs is append-only: % is not allowed', TG_OP; END; "
        "$$ LANGUAGE plpgsql;"))
    await db_session.execute(text(
        "DROP TRIGGER IF EXISTS audit_logs_no_update_delete ON audit_logs;"
        "CREATE TRIGGER audit_logs_no_update_delete BEFORE UPDATE OR DELETE ON audit_logs "
        "FOR EACH ROW EXECUTE FUNCTION nexora_audit_logs_immutable();"))
    await db_session.commit()

    _enable(monkeypatch, max_attempts=3)
    await _make_admin(db_session)
    svc = AuthService(db_session, redis_client)

    await _fail_login(svc, "admin_lk")
    await svc.login("admin_lk", PASSWORD, ip="10.0.0.1", user_agent="pytest-UA/1.0")

    assert len(await _audit_rows(db_session, "auth.login_failed")) == 1
    assert len(await _audit_rows(db_session, "auth.login")) == 1
