"""Client profile and device management endpoints."""
from fastapi import APIRouter, Depends, Request
import redis.asyncio as aioredis
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.redis_client import get_redis
from app.core.dependencies import get_current_subscriber
from app.core.exceptions import not_found, forbidden
from app.models.subscriber import Subscriber
from app.schemas.client import ClientProfileResponse, ClientDeviceRegister, ClientHeartbeatRequest
from app.schemas.device import (
    DeviceOut,
    DeviceRegister,
    DeviceHeartbeat,
    DeviceRegisterResponse,
    DeviceActivateRequest,
)
from app.services.stb_service import STBService
from app.services.device_service import DeviceService

router = APIRouter(prefix="/profile", tags=["Client Profile"])


def _get_ip(request: Request) -> str:
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


@router.get("", response_model=ClientProfileResponse)
async def get_profile(
    subscriber: Subscriber = Depends(get_current_subscriber),
    db: AsyncSession = Depends(get_db),
    redis: aioredis.Redis = Depends(get_redis),
):
    stb = STBService(db, redis)
    status = await stb.validate_active(subscriber.id)
    return ClientProfileResponse(
        subscriber_id=str(subscriber.id),
        username=subscriber.username,
        full_name=subscriber.full_name,
        email=subscriber.email,
        status=subscriber.status.value,
        subscription_expires_at=status.subscription_expires_at,
        max_connections=status.max_connections,
        max_devices=status.max_devices,
        device_count=status.device_count,
        days_remaining=status.days_remaining,
    )


@router.get("/devices", response_model=list[DeviceOut])
async def list_devices(
    subscriber: Subscriber = Depends(get_current_subscriber),
    db: AsyncSession = Depends(get_db),
    redis: aioredis.Redis = Depends(get_redis),
):
    svc = DeviceService(db, redis)
    return await svc.list_for_subscriber(subscriber.id)


@router.post("/devices/register", response_model=DeviceRegisterResponse)
async def register_device(
    data: ClientDeviceRegister,
    request: Request,
    subscriber: Subscriber = Depends(get_current_subscriber),
    db: AsyncSession = Depends(get_db),
    redis: aioredis.Redis = Depends(get_redis),
):
    svc = DeviceService(db, redis)
    device = await svc.register(
        subscriber.id,
        DeviceRegister(
            device_id=data.device_id,
            mac_address=data.mac_address,
            model=data.model,
            brand=data.brand,
            device_type=data.device_type,
            app_version=data.app_version,
            os_version=data.os_version,
        ),
        _get_ip(request),
    )
    await db.commit()
    resp = DeviceRegisterResponse.model_validate(device)
    # Surface the plaintext secret ONCE, only when a fresh one was just issued.
    resp.device_secret = getattr(device, "plaintext_secret", None)
    return resp


@router.post("/devices/activate", response_model=DeviceOut)
async def activate_device(
    data: DeviceActivateRequest,
    subscriber: Subscriber = Depends(get_current_subscriber),
    db: AsyncSession = Depends(get_db),
    redis: aioredis.Redis = Depends(get_redis),
):
    """Activate a pending device by presenting its registration secret.
    No-op for already-active devices; wrong secret → 403."""
    svc = DeviceService(db, redis)
    device = await svc.get_by_device_id(data.device_id)
    if device is None:
        raise not_found("Device")
    if device.subscriber_id != subscriber.id:
        raise forbidden("Device does not belong to this subscriber")
    device = await svc.activate_with_secret(data.device_id, data.device_secret)
    await db.commit()
    return device


@router.post("/devices/heartbeat")
async def device_heartbeat(
    data: ClientHeartbeatRequest,
    request: Request,
    subscriber: Subscriber = Depends(get_current_subscriber),
    db: AsyncSession = Depends(get_db),
    redis: aioredis.Redis = Depends(get_redis),
):
    svc = DeviceService(db, redis)
    device = await svc.get_by_device_id(data.device_id)
    if device is None:
        raise not_found("Device not registered")
    if device.subscriber_id != subscriber.id:
        raise forbidden("Device does not belong to this subscriber")
    result = await svc.heartbeat(
        DeviceHeartbeat(device_id=data.device_id, app_version=data.app_version),
        ip=_get_ip(request),
    )
    await db.commit()
    return result
