"""
STB — endpoints para dispositivos MAG/Android TV.
POST /api/stb/heartbeat — heartbeat desde dispositivo
POST /api/stb/register  — registro de dispositivo (STB-facing, sin auth admin)
GET  /api/stb/connections/{sub_id} — conexiones activas del suscriptor
"""
import uuid
from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession
import redis.asyncio as aioredis

from app.database import get_db
from app.redis_client import get_redis
from app.services.device_service import DeviceService
from app.services.connection_service import ConnectionService
from app.schemas.device import DeviceHeartbeat, DeviceRegister, DeviceOut
from app.schemas.common import ApiResponse
from app.core.dependencies import require_admin_or_reseller, get_client_ip
from app.models.user import User

router = APIRouter(prefix="", tags=["STB — Devices"])


@router.post("/heartbeat", response_model=ApiResponse[dict])
async def stb_heartbeat(
    body: DeviceHeartbeat,
    request: Request,
    db: AsyncSession = Depends(get_db),
    redis: aioredis.Redis = Depends(get_redis),
):
    """
    Heartbeat STB. Llamado cada 60s por el dispositivo.
    Renueva el TTL en Redis ZSET (180s). Sin TTL: desconexión automática.
    No requiere token de admin.
    """
    svc = DeviceService(db, redis)
    ip = get_client_ip(request)
    result = await svc.heartbeat(body, ip)
    await db.commit()
    return ApiResponse(data=result)


@router.post("/register/{sub_id}", response_model=ApiResponse[DeviceOut], status_code=201)
async def stb_register_device(
    sub_id: uuid.UUID,
    body: DeviceRegister,
    request: Request,
    db: AsyncSession = Depends(get_db),
    redis: aioredis.Redis = Depends(get_redis),
    actor: User = Depends(require_admin_or_reseller),
):
    """Registra un dispositivo STB para un suscriptor."""
    svc = DeviceService(db, redis)
    ip = get_client_ip(request)
    device = await svc.register(sub_id, body, ip)
    await db.commit()
    return ApiResponse(data=DeviceOut.model_validate(device), message="Device registered")


@router.get("/connections/{sub_id}", response_model=ApiResponse[dict])
async def stb_active_connections(
    sub_id: uuid.UUID,
    redis: aioredis.Redis = Depends(get_redis),
    _: User = Depends(require_admin_or_reseller),
):
    """Devuelve las conexiones IPTV activas del suscriptor (desde Redis ZSET)."""
    conn_svc = ConnectionService(redis)
    devices = await conn_svc.get_active_devices(str(sub_id))
    count = len(devices)
    return ApiResponse(data={
        "subscriber_id": str(sub_id),
        "active_connections": count,
        "device_ids": devices,
    })
