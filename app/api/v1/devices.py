import uuid
from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.redis_client import get_redis
from app.services.device_service import DeviceService
from app.services.audit_service import AuditService
from app.schemas.device import DeviceRegister, DeviceHeartbeat, DeviceBlockRequest, DeviceOut
from app.schemas.common import ApiResponse, MessageResponse
from app.core.dependencies import require_admin_or_reseller, get_client_ip
from app.models.user import User

router = APIRouter(prefix="/devices", tags=["Devices"])


@router.get("/subscriber/{sub_id}", response_model=ApiResponse[list[DeviceOut]])
async def list_devices(
    sub_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    redis=Depends(get_redis),
    _: User = Depends(require_admin_or_reseller),
):
    svc = DeviceService(db, redis)
    devices = await svc.list_for_subscriber(sub_id)
    return ApiResponse(data=devices)


@router.post("/register/{sub_id}", response_model=ApiResponse[DeviceOut], status_code=201)
async def register_device(
    sub_id: uuid.UUID,
    body: DeviceRegister,
    request: Request,
    db: AsyncSession = Depends(get_db),
    redis=Depends(get_redis),
    actor: User = Depends(require_admin_or_reseller),
):
    """Registra o actualiza un dispositivo para un suscriptor."""
    svc = DeviceService(db, redis)
    audit = AuditService(db)
    ip = get_client_ip(request)
    device = await svc.register(sub_id, body, ip)
    await audit.log("device.register", actor, "device", str(device.id),
                    {"device_id": body.device_id, "subscriber_id": str(sub_id)}, ip)
    return ApiResponse(data=DeviceOut.model_validate(device), message="Device registered")


@router.post("/heartbeat", response_model=ApiResponse[dict])
async def heartbeat(
    body: DeviceHeartbeat,
    request: Request,
    db: AsyncSession = Depends(get_db),
    redis=Depends(get_redis),
):
    """
    Heartbeat desde el dispositivo. No requiere autenticación de usuario admin —
    el device_id identifica el dispositivo. Valida estado del suscriptor.
    """
    svc = DeviceService(db, redis)
    ip = get_client_ip(request)
    result = await svc.heartbeat(body, ip)
    return ApiResponse(data=result)


@router.post("/{device_id}/block", response_model=ApiResponse[DeviceOut])
async def block_device(
    device_id: uuid.UUID,
    body: DeviceBlockRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    redis=Depends(get_redis),
    actor: User = Depends(require_admin_or_reseller),
):
    svc = DeviceService(db, redis)
    audit = AuditService(db)
    device = await svc.block(device_id, body.reason)
    await audit.log("device.block", actor, "device", str(device_id),
                    {"reason": body.reason}, get_client_ip(request))
    return ApiResponse(data=DeviceOut.model_validate(device), message="Device blocked")


@router.post("/{device_id}/unblock", response_model=ApiResponse[DeviceOut])
async def unblock_device(
    device_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    redis=Depends(get_redis),
    actor: User = Depends(require_admin_or_reseller),
):
    svc = DeviceService(db, redis)
    audit = AuditService(db)
    device = await svc.unblock(device_id)
    await audit.log("device.unblock", actor, "device", str(device_id),
                    None, get_client_ip(request))
    return ApiResponse(data=DeviceOut.model_validate(device), message="Device unblocked")


@router.delete("/{device_id}", response_model=MessageResponse)
async def delete_device(
    device_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    redis=Depends(get_redis),
    actor: User = Depends(require_admin_or_reseller),
):
    svc = DeviceService(db, redis)
    audit = AuditService(db)
    await svc.delete(device_id)
    await audit.log("device.delete", actor, "device", str(device_id),
                    None, get_client_ip(request))
    return MessageResponse(message="Device removed")
