"""FASE 3 — device cap decoupled from login; /devices/register → 409.

Covers:
  - login does NOT fail when the device limit is reached (flag limit_reached)
  - DeviceService.register raises 409 DEVICE_LIMIT_REACHED (explicit register)
  - register creates nothing when over the limit
  - register works under the limit
  - playback denies unregistered / blocked device (403 reason codes)
  - login + existing device still works
"""
from datetime import datetime, timedelta, timezone

import pytest

from app.core.exceptions import NexoraException
from app.core.security import hash_password
from app.models.subscriber import Subscriber, SubscriberStatus
from app.models.plan import Plan
from app.models.subscription import Subscription
from app.models.device import Device
from app.schemas.client import ClientLoginRequest
from app.schemas.device import DeviceRegister
from app.services.client_auth_service import ClientAuthService
from app.services.device_service import DeviceService
from app.services.stream_auth_service import StreamAuthService

pytestmark = pytest.mark.asyncio


async def _make_world(db, *, max_devices: int, n_devices: int, password: str = "secret123"):
    now = datetime.now(timezone.utc)
    sub = Subscriber(
        username="dev_user", status=SubscriberStatus.active,
        password_hash=hash_password(password),
    )
    plan = Plan(name="dev_plan", max_connections=2, max_devices=max_devices,
                duration_days=365, is_active=True)
    db.add_all([sub, plan])
    await db.flush()
    db.add(Subscription(
        subscriber_id=sub.id, plan_id=plan.id,
        starts_at=now - timedelta(days=1), expires_at=now + timedelta(days=30),
        is_active=True,
    ))
    for i in range(n_devices):
        db.add(Device(subscriber_id=sub.id, device_id=f"existing-dev-{i}",
                      device_type="web_player", is_blocked=False))
    await db.commit()
    return sub, plan


async def _device_count(db, sub_id) -> int:
    from sqlalchemy import func, select
    return (await db.execute(
        select(func.count()).select_from(Device).where(Device.subscriber_id == sub_id)
    )).scalar()


def _login_data(device_id: str) -> ClientLoginRequest:
    return ClientLoginRequest(
        username="dev_user", password="secret123", device_id=device_id,
        device_type="web_player", model="m", brand="Nexora", os_version="x",
    )


async def test_login_not_blocked_when_device_limit_reached(db_session, redis_client):
    sub, _ = await _make_world(db_session, max_devices=2, n_devices=2)  # 2/2 full
    svc = ClientAuthService(db_session, redis_client)
    access, refresh, sub_id, expires_in, dev_reg = await svc.login(
        _login_data("brand-new-device-1"), ip="1.2.3.4", user_agent="ua"
    )
    assert access and refresh
    assert dev_reg == "limit_reached"
    assert await _device_count(db_session, sub.id) == 2  # new device NOT created


async def test_login_catalog_still_work_with_existing_device(db_session, redis_client):
    sub, _ = await _make_world(db_session, max_devices=2, n_devices=2)
    svc = ClientAuthService(db_session, redis_client)
    access, refresh, sub_id, expires_in, dev_reg = await svc.login(
        _login_data("existing-dev-0"), ip="1.2.3.4", user_agent="ua"
    )
    assert access and refresh
    assert dev_reg == "registered"
    assert str(sub.id) == sub_id


async def test_device_register_returns_409_when_limit_reached(db_session, redis_client):
    sub, _ = await _make_world(db_session, max_devices=2, n_devices=2)
    svc = DeviceService(db_session, redis_client)
    with pytest.raises(NexoraException) as ei:
        await svc.register(sub.id, DeviceRegister(device_id="over-limit-dev"), ip="1.2.3.4")
    assert ei.value.status_code == 409
    assert ei.value.detail["reason_code"] == "DEVICE_LIMIT_REACHED"


async def test_device_register_does_not_create_device_when_limit_reached(db_session, redis_client):
    sub, _ = await _make_world(db_session, max_devices=2, n_devices=2)
    svc = DeviceService(db_session, redis_client)
    with pytest.raises(NexoraException):
        await svc.register(sub.id, DeviceRegister(device_id="over-limit-dev"), ip="1.2.3.4")
    assert await _device_count(db_session, sub.id) == 2


async def test_device_register_allows_when_under_limit(db_session, redis_client):
    sub, _ = await _make_world(db_session, max_devices=5, n_devices=2)
    svc = DeviceService(db_session, redis_client)
    dev = await svc.register(sub.id, DeviceRegister(device_id="fresh-dev-001"), ip="1.2.3.4")
    await db_session.commit()
    assert dev is not None
    assert await _device_count(db_session, sub.id) == 3


async def test_playback_denies_unregistered_device(entitlement_world, redis_client):
    svc = StreamAuthService(entitlement_world["session"], redis_client)
    with pytest.raises(NexoraException) as ei:
        await svc.authorize(
            subscriber_id=entitlement_world["subscriber"].id,
            device_id_str="ghost-device",
            channel_id="K1",
            channel_key="canal-1",
        )
    assert ei.value.status_code == 403
    assert ei.value.detail == "DEVICE_NOT_REGISTERED"


async def test_playback_denies_blocked_device(entitlement_world, redis_client):
    entitlement_world["device"].is_blocked = True
    await entitlement_world["session"].commit()
    svc = StreamAuthService(entitlement_world["session"], redis_client)
    with pytest.raises(NexoraException) as ei:
        await svc.authorize(
            subscriber_id=entitlement_world["subscriber"].id,
            device_id_str=entitlement_world["device"].device_id,
            channel_id="K1",
            channel_key="canal-1",
        )
    assert ei.value.status_code == 403
    assert ei.value.detail == "DEVICE_BLOCKED"
