import uuid
from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.redis_client import get_redis
from app.services.subscriber_service import SubscriberService
from app.services.stb_service import STBService
from app.services.audit_service import AuditService
from app.schemas.subscriber import SubscriberCreate, SubscriberUpdate, SubscriberOut, SubscriberOutFull, SubscriberPasswordChange
from app.schemas.subscription import SubscriberActiveStatus
from app.schemas.common import PaginatedResponse, MessageResponse, ApiResponse
from app.core.dependencies import require_admin_or_reseller, get_client_ip
from app.models.user import User
from app.models.subscriber import SubscriberStatus

router = APIRouter(prefix="/subscribers", tags=["Subscribers"])


@router.get("", response_model=PaginatedResponse[SubscriberOut])
async def list_subscribers(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    status: SubscriberStatus | None = Query(None),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin_or_reseller),
):
    svc = SubscriberService(db)
    subs, total = await svc.list_subscribers(page, page_size, status)
    pages = (total + page_size - 1) // page_size
    return PaginatedResponse(data=subs, total=total, page=page, page_size=page_size, pages=pages)


@router.post("", response_model=ApiResponse[SubscriberOutFull], status_code=201)
async def create_subscriber(
    body: SubscriberCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_admin_or_reseller),
):
    svc = SubscriberService(db)
    audit = AuditService(db)
    sub = await svc.create(body, created_by=actor.id)
    await audit.log("subscriber.create", actor, "subscriber", str(sub.id),
                    {"username": sub.username}, get_client_ip(request))
    return ApiResponse(data=SubscriberOutFull.model_validate(sub), message="Subscriber created")


@router.get("/{sub_id}", response_model=ApiResponse[SubscriberOutFull])
async def get_subscriber(
    sub_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin_or_reseller),
):
    svc = SubscriberService(db)
    sub = await svc.get_by_id(sub_id)
    return ApiResponse(data=SubscriberOutFull.model_validate(sub))


@router.patch("/{sub_id}", response_model=ApiResponse[SubscriberOut])
async def update_subscriber(
    sub_id: uuid.UUID,
    body: SubscriberUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_admin_or_reseller),
):
    svc = SubscriberService(db)
    audit = AuditService(db)
    sub = await svc.update(sub_id, body)
    await audit.log("subscriber.update", actor, "subscriber", str(sub_id),
                    body.model_dump(exclude_none=True), get_client_ip(request))
    return ApiResponse(data=SubscriberOut.model_validate(sub))


@router.post("/{sub_id}/set-password", response_model=MessageResponse)
async def set_subscriber_password(
    sub_id: uuid.UUID,
    body: SubscriberPasswordChange,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_admin_or_reseller),
):
    svc = SubscriberService(db)
    audit = AuditService(db)
    await svc.set_password(sub_id, body.new_password)
    await audit.log("subscriber.password_change", actor, "subscriber", str(sub_id),
                    None, get_client_ip(request))
    return MessageResponse(message="Password updated")


@router.get("/{sub_id}/status", response_model=ApiResponse[SubscriberActiveStatus])
async def subscriber_status(
    sub_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    redis=Depends(get_redis),
    _: User = Depends(require_admin_or_reseller),
):
    """Valida si el suscriptor tiene suscripción activa y cuántos dispositivos tiene."""
    stb = STBService(db, redis)
    status = await stb.validate_active(sub_id)
    return ApiResponse(data=status)


@router.post("/{sub_id}/suspend", response_model=MessageResponse)
async def suspend_subscriber(
    sub_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_admin_or_reseller),
):
    svc = SubscriberService(db)
    audit = AuditService(db)
    await svc.set_status(sub_id, SubscriberStatus.suspended)
    await audit.log("subscriber.suspend", actor, "subscriber", str(sub_id),
                    None, get_client_ip(request))
    return MessageResponse(message="Subscriber suspended")


@router.post("/{sub_id}/activate", response_model=MessageResponse)
async def activate_subscriber(
    sub_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_admin_or_reseller),
):
    svc = SubscriberService(db)
    audit = AuditService(db)
    await svc.set_status(sub_id, SubscriberStatus.active)
    await audit.log("subscriber.activate", actor, "subscriber", str(sub_id),
                    None, get_client_ip(request))
    return MessageResponse(message="Subscriber activated")


@router.delete("/{sub_id}", response_model=MessageResponse)
async def delete_subscriber(
    sub_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_admin_or_reseller),
):
    svc = SubscriberService(db)
    audit = AuditService(db)
    await svc.delete(sub_id)
    await audit.log("subscriber.delete", actor, "subscriber", str(sub_id),
                    None, get_client_ip(request))
    return MessageResponse(message="Subscriber deleted")
