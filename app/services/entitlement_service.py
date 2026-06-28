"""
EntitlementService — decides whether a subscriber+device may watch a channel.

This is the single source of truth for "can_watch_channel", consumed by
PlaybackAuthorizationService BEFORE any session/token/URL is created.

Designed against the CURRENT schema:
  - subscriptions: is_active (bool) + starts_at + expires_at  (status enum is P1)
  - devices: is_blocked (bool)                                (status enum is P1)
It is forward-compatible: when subscriptions.status / devices.status land,
the corresponding checks can be tightened without changing the contract.

Returns an EntitlementResult(allow, reason_code, detail) — never a secret.
"""
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.reason_codes import ReasonCode
from app.models.subscriber import Subscriber, SubscriberStatus
from app.models.subscription import Subscription
from app.models.plan import Plan
from app.models.device import Device
from app.models.channel import Channel
from app.models.plan_channel import PlanChannel


@dataclass(frozen=True)
class EntitlementResult:
    allow: bool
    reason_code: ReasonCode
    detail: str | None = None

    @property
    def code(self) -> str:
        return self.reason_code.value


_ALLOW = EntitlementResult(True, ReasonCode.ALLOW)


def _deny(code: ReasonCode, detail: str | None = None) -> EntitlementResult:
    return EntitlementResult(False, code, detail)


class EntitlementService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def can_watch_channel(
        self,
        subscriber_id,
        device_id: str,
        channel_id: str,
    ) -> EntitlementResult:
        """Evaluate entitlement in a deterministic order (first failure wins).

        Args:
          subscriber_id: UUID of the subscriber (from validated JWT).
          device_id:     external device identifier (Device.device_id string).
          channel_id:    channel_key (public) of the requested channel.
        """
        now = datetime.now(timezone.utc)

        # 1. subscriber exists
        subscriber = (
            await self.db.execute(
                select(Subscriber).where(Subscriber.id == subscriber_id)
            )
        ).scalar_one_or_none()
        if subscriber is None:
            return _deny(ReasonCode.SUBSCRIBER_NOT_FOUND)

        # 2. subscriber status allows service
        if subscriber.status == SubscriberStatus.suspended:
            return _deny(ReasonCode.SUBSCRIBER_SUSPENDED)
        if subscriber.status in (SubscriberStatus.banned, SubscriberStatus.expired):
            return _deny(ReasonCode.SUBSCRIBER_DISABLED, subscriber.status.value)

        # 3-7. active subscription + plan active + date window
        row = (
            await self.db.execute(
                select(Subscription, Plan)
                .join(Plan, Subscription.plan_id == Plan.id)
                .where(
                    Subscription.subscriber_id == subscriber.id,
                    Subscription.is_active.is_(True),
                )
                .order_by(Subscription.expires_at.desc())
                .limit(1)
            )
        ).first()
        if row is None:
            return _deny(ReasonCode.SUBSCRIPTION_NOT_FOUND)
        subscription, plan = row

        starts_at = _aware(subscription.starts_at)
        expires_at = _aware(subscription.expires_at)
        if starts_at > now:
            return _deny(ReasonCode.SUBSCRIPTION_NOT_FOUND, "not_started")
        if expires_at <= now:
            return _deny(ReasonCode.SUBSCRIPTION_EXPIRED)
        if not plan.is_active:
            return _deny(ReasonCode.PLAN_INACTIVE)

        # 8-9. device exists, belongs to subscriber, not blocked
        device = (
            await self.db.execute(
                select(Device).where(Device.device_id == device_id)
            )
        ).scalar_one_or_none()
        if device is None or device.subscriber_id != subscriber.id:
            return _deny(ReasonCode.DEVICE_NOT_REGISTERED)
        if device.is_blocked:
            return _deny(ReasonCode.DEVICE_BLOCKED)

        # 10-11. channel exists and is active
        channel = (
            await self.db.execute(
                select(Channel).where(Channel.channel_key == channel_id)
            )
        ).scalar_one_or_none()
        if channel is None:
            return _deny(ReasonCode.CHANNEL_NOT_FOUND)
        if not channel.is_active:
            return _deny(ReasonCode.CHANNEL_INACTIVE)

        # 12. channel included in the subscriber's plan
        included = (
            await self.db.execute(
                select(PlanChannel.id).where(
                    PlanChannel.plan_id == plan.id,
                    PlanChannel.channel_id == channel.id,
                    PlanChannel.is_enabled.is_(True),
                )
            )
        ).scalar_one_or_none()
        if included is None:
            return _deny(ReasonCode.CHANNEL_NOT_INCLUDED)

        return _ALLOW


def _aware(dt: datetime) -> datetime:
    """Treat naive datetimes (from DB) as UTC for safe comparison."""
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)
