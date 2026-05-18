"""
STBService — valida suscriptores para el protocolo STB/Stalker.
Fase 1: validación de suscriptor activo y dispositivo.
Fase 3: adaptador completo compatible con endpoints Stalker.
"""
import uuid
from datetime import datetime, timezone
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
import redis.asyncio as aioredis

from app.models.subscriber import Subscriber, SubscriberStatus
from app.models.subscription import Subscription
from app.models.plan import Plan
from app.models.device import Device
from app.core.security import verify_password
from app.core.exceptions import unauthorized, forbidden
from app.schemas.subscription import SubscriberActiveStatus


class STBService:
    def __init__(self, db: AsyncSession, redis: aioredis.Redis):
        self.db = db
        self.redis = redis

    async def _get_active_subscription(
        self, subscriber_id: uuid.UUID
    ) -> tuple[Subscription, Plan] | None:
        now = datetime.now(timezone.utc)
        result = await self.db.execute(
            select(Subscription, Plan)
            .join(Plan, Subscription.plan_id == Plan.id)
            .where(
                Subscription.subscriber_id == subscriber_id,
                Subscription.is_active == True,
                Subscription.expires_at > now,
            )
            .order_by(Subscription.expires_at.desc())
            .limit(1)
        )
        return result.first()

    async def authenticate_subscriber(
        self, username: str, password: str | None = None, activation_code: str | None = None
    ) -> Subscriber:
        result = await self.db.execute(
            select(Subscriber).where(Subscriber.username == username)
        )
        sub = result.scalar_one_or_none()
        if not sub:
            raise unauthorized("Subscriber not found")
        if sub.status == SubscriberStatus.banned:
            raise forbidden("Subscriber is banned")
        if sub.status == SubscriberStatus.suspended:
            raise forbidden("Subscriber is suspended")

        if password and sub.password_hash:
            if not verify_password(password, sub.password_hash):
                raise unauthorized("Invalid credentials")
        elif activation_code:
            if sub.activation_code != activation_code:
                raise unauthorized("Invalid activation code")
        elif not password and not activation_code:
            raise unauthorized("Credentials required")

        return sub

    async def validate_active(self, subscriber_id: uuid.UUID) -> SubscriberActiveStatus:
        result = await self.db.execute(
            select(Subscriber).where(Subscriber.id == subscriber_id)
        )
        sub = result.scalar_one_or_none()
        if not sub:
            raise unauthorized("Subscriber not found")

        sub_plan = await self._get_active_subscription(subscriber_id)

        # Count registered devices
        dev_result = await self.db.execute(
            select(Device).where(Device.subscriber_id == subscriber_id)
        )
        devices = dev_result.scalars().all()
        device_count = len(devices)

        if sub_plan:
            subscription, plan = sub_plan
            now = datetime.now(timezone.utc)
            days_remaining = (subscription.expires_at - now).days
            return SubscriberActiveStatus(
                subscriber_id=subscriber_id,
                username=sub.username,
                is_active=True,
                subscription_expires_at=subscription.expires_at,
                max_connections=plan.max_connections,
                max_devices=plan.max_devices,
                device_count=device_count,
                days_remaining=days_remaining,
            )

        return SubscriberActiveStatus(
            subscriber_id=subscriber_id,
            username=sub.username,
            is_active=False,
            subscription_expires_at=None,
            max_connections=0,
            max_devices=0,
            device_count=device_count,
            days_remaining=None,
        )

    async def validate_device(self, device_id: str, subscriber_id: uuid.UUID) -> Device:
        result = await self.db.execute(
            select(Device).where(
                Device.device_id == device_id,
                Device.subscriber_id == subscriber_id,
            )
        )
        device = result.scalar_one_or_none()
        if not device:
            raise unauthorized("Device not registered for this subscriber")
        if device.is_blocked:
            raise forbidden(f"Device blocked: {device.block_reason or 'no reason'}")
        return device
