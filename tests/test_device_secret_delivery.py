"""NX-DEV — device secret delivery + os_version contract.

BUG 1: the device secret was generated during POST /api/client/auth/login,
hashed into devices.device_secret_hash, and the plaintext dropped on the floor
(ClientTokenResponse had no field for it, and a later /profile/devices/register
with the same device_id takes the "existing device" branch and returns
device_secret: null). Every device born from a login was therefore impossible to
activate — fatal once DEVICE_SECRET_ENFORCE is on, since such devices start
'pending'.

Contract now under test:
  - device created BY the login      → plaintext secret in the login response
  - any later login, same device_id  → no secret (the plaintext no longer exists)
  - device cap hit (no device row)   → no secret
  - refresh                          → never a secret
  - the secret never reaches a log record

BUG 2: ClientLoginRequest/ClientDeviceRegister accept os_version up to 512
chars (navigator.userAgent easily exceeds 32) while devices.os_version is
String(32) and DeviceRegister capped it at 32 → ValidationError → HTTP 500.
Fixed by truncating at the DTO boundary.
"""
import logging
import uuid
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from httpx import ASGITransport
from sqlalchemy import func, select

from app.core.security import hash_password, verify_device_secret
from app.models.device import Device
from app.models.plan import Plan
from app.models.subscriber import Subscriber, SubscriberStatus
from app.models.subscription import Subscription
from app.schemas.client import ClientLoginRequest
from app.schemas.device import DeviceRegister
from app.services import device_service as dev_mod
from app.services.client_auth_service import ClientAuthService, ClientLoginResult

pytestmark = pytest.mark.asyncio

PASSWORD = "secret123"
_UA_PREFIX = "Mozilla/5.0 (Linux; Android 14; SM-S918B) AppleWebKit/537.36 "
# Exactly the public max_length (512) — a realistic navigator.userAgent shape.
LONG_OS_VERSION = _UA_PREFIX + "x" * (512 - len(_UA_PREFIX))


def _fresh_ip() -> str:
    """Unique client IP per call.

    The rate-limit middleware talks to the REAL Redis db (settings.redis_url),
    which the test fixtures do not flush, and
    /api/client/profile/devices/register is capped at 5 req/min per IP. A unique
    IP per request keeps repeated local runs from tripping a spurious 429.
    """
    n = uuid.uuid4().int
    return f"10.{n % 251}.{(n >> 8) % 251}.{(n >> 16) % 251}"


async def _make_world(db, *, max_devices: int = 5, n_devices: int = 0, username: str = "nxdev_user"):
    now = datetime.now(timezone.utc)
    sub = Subscriber(
        username=username, status=SubscriberStatus.active,
        password_hash=hash_password(PASSWORD),
    )
    plan = Plan(name=f"plan_{username}", max_connections=2, max_devices=max_devices,
                duration_days=365, is_active=True)
    db.add_all([sub, plan])
    await db.flush()
    db.add(Subscription(
        subscriber_id=sub.id, plan_id=plan.id,
        starts_at=now - timedelta(days=1), expires_at=now + timedelta(days=30),
        is_active=True,
    ))
    for i in range(n_devices):
        db.add(Device(subscriber_id=sub.id, device_id=f"seeded-dev-{i}",
                      device_type="web_player", is_blocked=False))
    await db.commit()
    return sub, plan


def _login_data(device_id: str, *, os_version: str | None = "Android 14") -> ClientLoginRequest:
    return ClientLoginRequest(
        username="nxdev_user", password=PASSWORD, device_id=device_id,
        device_type="web_player", model="m", brand="Nexora", os_version=os_version,
    )


async def _stored_device(db, device_id: str) -> Device | None:
    return (await db.execute(
        select(Device).where(Device.device_id == device_id)
    )).scalar_one_or_none()


# ── BUG 1 — service level ────────────────────────────────────────────────────

async def test_login_that_creates_the_device_returns_the_plaintext_secret(db_session, redis_client):
    await _make_world(db_session)
    svc = ClientAuthService(db_session, redis_client)
    result = await svc.login(_login_data("brand-new-dev-1"), ip="1.2.3.4", user_agent="ua")
    await db_session.commit()

    assert result.device_registration == "registered"
    assert result.device_secret, "the login that creates the device must deliver its secret"
    assert len(result.device_secret) >= 16

    dev = await _stored_device(db_session, "brand-new-dev-1")
    assert dev is not None
    # Only the hash is persisted, and the delivered plaintext verifies against it.
    assert dev.device_secret_hash and dev.device_secret_hash != result.device_secret
    assert verify_device_secret(result.device_secret, dev.device_secret_hash) is True


async def test_second_login_same_device_does_not_reissue_the_secret(db_session, redis_client):
    await _make_world(db_session)
    svc = ClientAuthService(db_session, redis_client)

    first = await svc.login(_login_data("repeat-dev-1"), ip="1.2.3.4", user_agent="ua")
    await db_session.commit()
    hash_after_first = (await _stored_device(db_session, "repeat-dev-1")).device_secret_hash

    second = await svc.login(_login_data("repeat-dev-1"), ip="1.2.3.5", user_agent="ua")
    await db_session.commit()

    assert first.device_secret is not None
    assert second.device_secret is None, "the plaintext is unrecoverable — never re-emit it"
    assert second.device_registration == "registered"
    # And the stored credential was not rotated behind the client's back.
    assert (await _stored_device(db_session, "repeat-dev-1")).device_secret_hash == hash_after_first


async def test_login_at_device_limit_returns_no_secret(db_session, redis_client):
    sub, _ = await _make_world(db_session, max_devices=2, n_devices=2)  # 2/2 full
    svc = ClientAuthService(db_session, redis_client)
    result = await svc.login(_login_data("over-the-cap-dev"), ip="1.2.3.4", user_agent="ua")
    await db_session.commit()

    assert result.device_registration == "limit_reached"
    assert result.device_secret is None
    assert result.access_token and result.refresh_token  # login still succeeds
    count = (await db_session.execute(
        select(func.count()).select_from(Device).where(Device.subscriber_id == sub.id)
    )).scalar_one()
    assert count == 2  # no device row → nothing to issue a secret for
    assert await _stored_device(db_session, "over-the-cap-dev") is None


async def test_login_result_still_unpacks_as_the_legacy_five_tuple(db_session, redis_client):
    """Additive change: pre-existing callers unpacking 5 values must keep working,
    and the secret must NOT sneak into that tuple."""
    await _make_world(db_session)
    svc = ClientAuthService(db_session, redis_client)
    result = await svc.login(_login_data("legacy-unpack-dev"), ip="1.2.3.4", user_agent="ua")
    await db_session.commit()

    access, refresh, sub_id, expires_in, dev_reg = result  # 5-value unpack
    assert access == result.access_token
    assert refresh == result.refresh_token
    assert sub_id == result.subscriber_id
    assert expires_in == result.expires_in
    assert dev_reg == "registered"
    assert len(tuple(result)) == 5
    assert result.device_secret not in tuple(result)


async def test_refresh_never_carries_a_device_secret(db_session, redis_client):
    await _make_world(db_session)
    svc = ClientAuthService(db_session, redis_client)
    login = await svc.login(_login_data("refresh-dev-1"), ip="1.2.3.4", user_agent="ua")
    await db_session.commit()
    assert login.device_secret is not None

    access, new_refresh, sub_id, expires_in = await svc.refresh(login.refresh_token)
    assert access and new_refresh and sub_id and expires_in
    # refresh() returns a plain 4-tuple — structurally incapable of carrying one.
    assert login.device_secret not in (access, new_refresh, sub_id)


async def test_login_result_repr_redacts_secret_and_tokens():
    r = ClientLoginResult(
        access_token="ACCESS-TOKEN-VALUE",
        refresh_token="REFRESH-TOKEN-VALUE",
        subscriber_id="sub-1",
        expires_in=3600,
        device_registration="registered",
        device_secret="SUPER-SECRET-VALUE",
    )
    text = repr(r)
    assert "SUPER-SECRET-VALUE" not in text
    assert "ACCESS-TOKEN-VALUE" not in text
    assert "REFRESH-TOKEN-VALUE" not in text
    assert "<redacted>" in text
    assert ClientLoginResult(
        access_token="a", refresh_token="b", subscriber_id="s",
        expires_in=1, device_registration="limit_reached",
    ).device_secret is None


async def test_device_secret_never_reaches_a_log_record(db_session, redis_client, monkeypatch):
    """Capture at Logger.handle so propagate=False loggers (the correlation
    middleware sets it on 'app') cannot hide a leak from us."""
    records: list[logging.LogRecord] = []
    original_handle = logging.Logger.handle

    def _spy(self, record):
        records.append(record)
        return original_handle(self, record)

    monkeypatch.setattr(logging.Logger, "handle", _spy)
    logging.disable(logging.NOTSET)
    root_level = logging.getLogger().level
    logging.getLogger().setLevel(logging.DEBUG)
    try:
        await _make_world(db_session)
        svc = ClientAuthService(db_session, redis_client)
        result = await svc.login(_login_data("logged-dev-1"), ip="1.2.3.4", user_agent="ua")
        await db_session.commit()
        # Re-register the same device through the service layer too.
        result2 = await svc.login(_login_data("logged-dev-1"), ip="1.2.3.4", user_agent="ua")
        await db_session.commit()
    finally:
        logging.getLogger().setLevel(root_level)

    secret = result.device_secret
    assert secret and result2.device_secret is None
    haystack = "\n".join(
        f"{r.getMessage()} {r.args!r} {r.__dict__!r}" for r in records
    )
    assert secret not in haystack, "device secret leaked into a log record"


# ── BUG 1 — HTTP level, end to end ──────────────────────────────────────────

def _app(db, redis, subscriber=None):
    from app.main import app
    from app.database import get_db
    from app.redis_client import get_redis
    from app.core.dependencies import get_current_subscriber

    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_redis] = lambda: redis
    if subscriber is not None:
        app.dependency_overrides[get_current_subscriber] = lambda: subscriber
    return app


async def test_http_login_delivers_secret_once_and_it_activates_the_device(
    db_session, redis_client, monkeypatch
):
    # Enforcement ON — this is exactly the configuration the bug made unusable:
    # new devices are born 'pending' and could never be activated.
    monkeypatch.setattr(dev_mod.settings, "device_secret_enforce", True)
    sub, _ = await _make_world(db_session)
    app = _app(db_session, redis_client, subscriber=sub)
    body = {
        "username": "nxdev_user", "password": PASSWORD,
        "device_id": "http-dev-0001", "device_type": "web_player",
    }
    try:
        async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r1 = await c.post("/api/client/auth/login", json=body,
                              headers={"X-Forwarded-For": _fresh_ip()})
            assert r1.status_code == 200, r1.text
            secret = r1.json()["device_secret"]
            assert secret, "first login must hand the plaintext secret to the client"

            dev = await _stored_device(db_session, "http-dev-0001")
            assert dev is not None and dev.status == "pending"

            # The delivered secret is the credential that unblocks the device.
            r_act = await c.post(
                "/api/client/profile/devices/activate",
                json={"device_id": "http-dev-0001", "device_secret": secret},
                headers={"X-Forwarded-For": _fresh_ip()},
            )
            assert r_act.status_code == 200, r_act.text
            assert r_act.json()["status"] == "active"

            # Second login for the same device → field present but null.
            r2 = await c.post("/api/client/auth/login", json=body,
                              headers={"X-Forwarded-For": _fresh_ip()})
            assert r2.status_code == 200, r2.text
            assert "device_secret" in r2.json()
            assert r2.json()["device_secret"] is None
    finally:
        app.dependency_overrides.clear()


async def test_http_refresh_response_has_no_device_secret(db_session, redis_client):
    await _make_world(db_session)
    app = _app(db_session, redis_client)
    try:
        async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r1 = await c.post(
                "/api/client/auth/login",
                json={"username": "nxdev_user", "password": PASSWORD,
                      "device_id": "http-dev-0002", "device_type": "web_player"},
                headers={"X-Forwarded-For": _fresh_ip()},
            )
            assert r1.status_code == 200, r1.text
            assert r1.json()["device_secret"]

            r2 = await c.post("/api/client/auth/refresh",
                              json={"refresh_token": r1.json()["refresh_token"]},
                              headers={"X-Forwarded-For": _fresh_ip()})
            assert r2.status_code == 200, r2.text
            assert r2.json()["device_secret"] is None
    finally:
        app.dependency_overrides.clear()


async def test_http_register_endpoint_still_returns_secret_for_a_fresh_device(
    db_session, redis_client
):
    """The explicit registration path must keep working unchanged (regression
    guard for the endpoint that already surfaced the secret)."""
    sub, _ = await _make_world(db_session)
    app = _app(db_session, redis_client, subscriber=sub)
    try:
        async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.post("/api/client/profile/devices/register",
                             json={"device_id": "http-dev-0003", "device_type": "web_player"},
                             headers={"X-Forwarded-For": _fresh_ip()})
            assert r.status_code == 200, r.text
            secret = r.json()["device_secret"]
            assert secret
            dev = await _stored_device(db_session, "http-dev-0003")
            assert verify_device_secret(secret, dev.device_secret_hash) is True

            # Re-register → existing branch → no secret.
            r2 = await c.post("/api/client/profile/devices/register",
                              json={"device_id": "http-dev-0003", "device_type": "web_player"},
                              headers={"X-Forwarded-For": _fresh_ip()})
            assert r2.status_code == 200, r2.text
            assert r2.json()["device_secret"] is None
    finally:
        app.dependency_overrides.clear()


# ── BUG 2 — os_version longer than the column ───────────────────────────────

async def test_device_register_dto_truncates_os_version_instead_of_raising():
    assert len(LONG_OS_VERSION) == 512
    dto = DeviceRegister(device_id="dto-dev-0001", os_version=LONG_OS_VERSION)
    assert len(dto.os_version) == 32
    assert dto.os_version == LONG_OS_VERSION[:32]
    # Identity fields are NOT truncated — an over-long device_id must still 422.
    with pytest.raises(Exception):
        DeviceRegister(device_id="d" * 129)


async def test_http_register_with_512_char_os_version_does_not_500(db_session, redis_client):
    sub, _ = await _make_world(db_session)
    app = _app(db_session, redis_client, subscriber=sub)
    try:
        async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.post(
                "/api/client/profile/devices/register",
                json={"device_id": "long-os-dev-01", "device_type": "web",
                      "os_version": LONG_OS_VERSION},
                headers={"X-Forwarded-For": _fresh_ip()},
            )
            assert r.status_code == 200, r.text  # was 500 (ValidationError)
            assert len(r.json()["os_version"]) == 32
    finally:
        app.dependency_overrides.clear()

    dev = await _stored_device(db_session, "long-os-dev-01")
    assert dev is not None and dev.os_version == LONG_OS_VERSION[:32]


async def test_http_login_with_512_char_os_version_does_not_500(db_session, redis_client):
    await _make_world(db_session)
    app = _app(db_session, redis_client)
    try:
        async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.post(
                "/api/client/auth/login",
                json={"username": "nxdev_user", "password": PASSWORD,
                      "device_id": "long-os-dev-02", "device_type": "web",
                      "os_version": LONG_OS_VERSION},
                headers={"X-Forwarded-For": _fresh_ip()},
            )
            assert r.status_code == 200, r.text
            assert r.json()["device_registration"] == "registered"
            assert r.json()["device_secret"]
    finally:
        app.dependency_overrides.clear()

    dev = await _stored_device(db_session, "long-os-dev-02")
    assert dev is not None
    assert dev.os_version == LONG_OS_VERSION[:32]  # truncated, persisted, no 500


async def test_register_over_512_chars_is_rejected_at_the_edge_not_crashed(
    db_session, redis_client
):
    """Beyond the public max_length the API must answer 422, never 500."""
    sub, _ = await _make_world(db_session)
    app = _app(db_session, redis_client, subscriber=sub)
    try:
        async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.post(
                "/api/client/profile/devices/register",
                json={"device_id": "long-os-dev-03", "os_version": "y" * 513},
                headers={"X-Forwarded-For": _fresh_ip()},
            )
            assert r.status_code == 422, r.text
    finally:
        app.dependency_overrides.clear()
