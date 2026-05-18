"""
DeviceService — registro, heartbeat, validación y límites por suscripción.
"""
import uuid
from datetime import datetime, timezone
import redis.asyncio as aioredis
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func

from app.models.device import Device
from app.models.subscription import Subscription
from app.models.plan import Plan
from app.core.exceptions import not_found, bad_request, forbidden
from app.redis_client import key_heartbeat
from app.schemas.device import DeviceRegister, DeviceHeartbeat
from app.services.connection_service import ConnectionService
from app.services.session_service import SessionService


class DeviceService:
    def __init__(self, db: AsyncSession, redis: aioredis.Redis):
        self.db = db
        self.redis = redis

    async def _active_subscription(self, subscriber_id: uuid.UUID) -> tuple[Subscription, Plan] | None:
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
        row = result.first()
        return row if row else None

    async def _device_count(self, subscriber_id: uuid.UUID) -> int:
        result = await self.db.execute(
            select(func.count()).select_from(Device).where(Device.subscriber_id == subscriber_id)
        )
        return result.scalar_one()

    async def get_by_id(self, device_id_uuid: uuid.UUID) -> Device:
        result = await self.db.execute(select(Device).where(Device.id == device_id_uuid))
        dev = result.scalar_one_or_none()
        if not dev:
            raise not_found("Device")
        return dev

    async def get_by_device_id(self, device_id: str) -> Device | None:
        result = await self.db.execute(select(Device).where(Device.device_id == device_id))
        return result.scalar_one_or_none()

    async def list_for_subscriber(self, subscriber_id: uuid.UUID) -> list[Device]:
        result = await self.db.execute(
            select(Device).where(Device.subscriber_id == subscriber_id)
        )
        return list(result.scalars().all())

    async def register(self, subscriber_id: uuid.UUID, data: DeviceRegister, ip: str) -> Device:
        # Check existing
        existing = await self.get_by_device_id(data.device_id)
        if existing:
            if existing.subscriber_id != subscriber_id:
                raise forbidden("Device registered to a different subscriber")
            # Update info on re-register
            existing.model = data.model or existing.model
            existing.brand = data.brand or existing.brand
            existing.device_type = data.device_type or existing.device_type
            existing.app_version = data.app_version or existing.app_version
            existing.os_version = data.os_version or existing.os_version
            existing.user_agent = data.user_agent or existing.user_agent
            existing.last_ip = ip
            existing.last_seen_at = datetime.now(timezone.utc)
            await self.db.flush()
            return existing

        # Enforce device limit from plan
        sub_plan = await self._active_subscription(subscriber_id)
        if sub_plan:
            _, plan = sub_plan
            current_count = await self._device_count(subscriber_id)
            if current_count >= plan.max_devices:
                raise bad_request(
                    f"Device limit reached ({plan.max_devices}). Remove a device before adding a new one."
                )

        device = Device(
            subscriber_id=subscriber_id,
            device_id=data.device_id,
            mac_address=data.mac_address,
            model=data.model,
            brand=data.brand,
            device_type=data.device_type,
            app_version=data.app_version,
            os_version=data.os_version,
            user_agent=data.user_agent,
            last_ip=ip,
            last_seen_at=datetime.now(timezone.utc),
        )
        self.db.add(device)
        await self.db.flush()
        return device

    async def heartbeat(self, data: DeviceHeartbeat, ip: str) -> dict:
        device = await self.get_by_device_id(data.device_id)
        if not device:
            raise not_found("Device not registered")
        if device.is_blocked:
            raise forbidden(f"Device is blocked: {device.block_reason or 'no reason given'}")

        now = datetime.now(timezone.utc)
        device.last_seen_at = now
        device.last_ip = ip
        if data.app_version:
            device.app_version = data.app_version
        await self.db.flush()

        # Redis heartbeat key (60s TTL — short keepalive)
        await self.redis.setex(key_heartbeat(data.device_id), 60, "1")

        # ZSET connection tracking (180s TTL — auto-disconnect on missing heartbeat)
        conn_svc = ConnectionService(self.redis)
        await conn_svc.extend_connection(device.subscriber_id, device.id)
        active_count = await conn_svc.count_active(str(device.subscriber_id))

        # Sync IPTV session heartbeat in DB
        session_svc = SessionService(self.redis, self.db)
        active_session = await session_svc.get_active_iptv_session(
            device.subscriber_id, device.id
        )
        if active_session:
            await session_svc.touch_iptv_session(active_session.access_token_jti)

        sub_plan = await self._active_subscription(device.subscriber_id)
        return {
            "ok": bool(sub_plan),
            "device_id": data.device_id,
            "last_seen": now.isoformat(),
            "subscription_active": bool(sub_plan),
            "active_connections": active_count,
            "max_connections": sub_plan[1].max_connections if sub_plan else 0,
            "expires_at": sub_plan[0].expires_at.isoformat() if sub_plan else None,
        }

    async def block(self, device_id_uuid: uuid.UUID, reason: str | None) -> Device:
        device = await self.get_by_id(device_id_uuid)
        device.is_blocked = True
        device.block_reason = reason
        await self.db.flush()
        return device

    async def unblock(self, device_id_uuid: uuid.UUID) -> Device:
        device = await self.get_by_id(device_id_uuid)
        device.is_blocked = False
        device.block_reason = None
        await self.db.flush()
        return device

    async def delete(self, device_id_uuid: uuid.UUID) -> None:
        device = await self.get_by_id(device_id_uuid)
        await self.db.delete(device)
        await self.db.flush()
