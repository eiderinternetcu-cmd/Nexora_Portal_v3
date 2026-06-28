"""Tests for EntitlementService.can_watch_channel (P0-010)."""
import pytest

from app.core.reason_codes import ReasonCode
from app.services.entitlement_service import EntitlementService

pytestmark = pytest.mark.asyncio


async def _check(world, channel_key):
    svc = EntitlementService(world["session"])
    return await svc.can_watch_channel(
        world["subscriber"].id, world["device"].device_id, channel_key
    )


async def test_entitlement_allows_channel_in_plan(entitlement_world):
    r = await _check(entitlement_world, "canal-1")
    assert r.allow is True
    assert r.reason_code == ReasonCode.ALLOW


async def test_entitlement_denies_channel_not_in_plan(entitlement_world):
    r = await _check(entitlement_world, "canal-99")
    assert r.allow is False
    assert r.reason_code == ReasonCode.CHANNEL_NOT_INCLUDED


async def test_entitlement_denies_unknown_channel(entitlement_world):
    r = await _check(entitlement_world, "canal-nope")
    assert r.allow is False
    assert r.reason_code == ReasonCode.CHANNEL_NOT_FOUND


async def test_entitlement_denies_suspended_subscriber(entitlement_world):
    from app.models.subscriber import SubscriberStatus
    entitlement_world["subscriber"].status = SubscriberStatus.suspended
    await entitlement_world["session"].commit()
    r = await _check(entitlement_world, "canal-1")
    assert r.allow is False
    assert r.reason_code == ReasonCode.SUBSCRIBER_SUSPENDED


async def test_entitlement_denies_expired_subscription(entitlement_world):
    from datetime import datetime, timedelta, timezone
    entitlement_world["subscription"].expires_at = datetime.now(timezone.utc) - timedelta(days=1)
    await entitlement_world["session"].commit()
    r = await _check(entitlement_world, "canal-1")
    assert r.allow is False
    assert r.reason_code == ReasonCode.SUBSCRIPTION_EXPIRED


async def test_entitlement_denies_inactive_plan(entitlement_world):
    entitlement_world["plan"].is_active = False
    await entitlement_world["session"].commit()
    r = await _check(entitlement_world, "canal-1")
    assert r.allow is False
    assert r.reason_code == ReasonCode.PLAN_INACTIVE


async def test_entitlement_denies_blocked_device(entitlement_world):
    entitlement_world["device"].is_blocked = True
    await entitlement_world["session"].commit()
    r = await _check(entitlement_world, "canal-1")
    assert r.allow is False
    assert r.reason_code == ReasonCode.DEVICE_BLOCKED


async def test_entitlement_denies_unregistered_device(entitlement_world):
    svc = EntitlementService(entitlement_world["session"])
    r = await svc.can_watch_channel(
        entitlement_world["subscriber"].id, "ghost-device", "canal-1"
    )
    assert r.allow is False
    assert r.reason_code == ReasonCode.DEVICE_NOT_REGISTERED


async def test_entitlement_denies_inactive_channel(entitlement_world):
    entitlement_world["ch_in"].is_active = False
    await entitlement_world["session"].commit()
    r = await _check(entitlement_world, "canal-1")
    assert r.allow is False
    assert r.reason_code == ReasonCode.CHANNEL_INACTIVE
