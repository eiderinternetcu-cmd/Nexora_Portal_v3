import csv
import io
import uuid
from fastapi import APIRouter, Depends, Query, Request, Response
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
from app.models.user import User, UserRole
from app.models.subscriber import SubscriberStatus

router = APIRouter(prefix="/subscribers", tags=["Subscribers"])


def _scope(actor: User) -> uuid.UUID | None:
    """Ambito de visibilidad. El reseller queda limitado a los suscriptores que
    creo (created_by == su id); el admin no tiene restriccion (None)."""
    return actor.id if actor.role == UserRole.reseller else None


@router.get("", response_model=PaginatedResponse[SubscriberOut])
async def list_subscribers(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    status: SubscriberStatus | None = Query(None),
    q: str | None = Query(
        None, max_length=128, description="Busca en username, full_name, email e id_cedula"
    ),
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_admin_or_reseller),
):
    svc = SubscriberService(db)
    subs, total = await svc.list_subscribers(page, page_size, status, q=q, owner_id=_scope(actor))
    pages = (total + page_size - 1) // page_size
    return PaginatedResponse(data=subs, total=total, page=page, page_size=page_size, pages=pages)


@router.get("/export")
async def export_subscribers(
    status: SubscriberStatus | None = Query(None),
    q: str | None = Query(
        None, max_length=128, description="Busca en username, full_name, email e id_cedula"
    ),
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_admin_or_reseller),
):
    """Exporta el listado (con los mismos filtros y el mismo scoping de reseller)
    como text/csv. Un reseller solo exporta lo suyo."""
    svc = SubscriberService(db)
    subs = await svc.list_for_export(status, q=q, owner_id=_scope(actor))

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([
        "id", "username", "email", "phone", "full_name", "id_cedula", "status",
        "notes", "created_at", "updated_at", "subscription_expires_at",
        "plan_name", "days_remaining", "owner_username",
    ])
    for s in subs:
        writer.writerow([
            str(s.id), s.username, s.email or "", s.phone or "", s.full_name or "",
            s.id_cedula or "", s.status.value, s.notes or "",
            s.created_at.isoformat() if s.created_at else "",
            s.updated_at.isoformat() if s.updated_at else "",
            s.subscription_expires_at.isoformat() if s.subscription_expires_at else "",
            s.plan_name or "",
            "" if s.days_remaining is None else s.days_remaining,
            s.owner_username or "",
        ])

    return Response(
        content=buf.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="subscribers.csv"'},
    )


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
    actor: User = Depends(require_admin_or_reseller),
):
    svc = SubscriberService(db)
    sub = await svc.get_by_id(sub_id, owner_id=_scope(actor))
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
    sub = await svc.update(sub_id, body, owner_id=_scope(actor))
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
    await svc.set_password(sub_id, body.new_password, owner_id=_scope(actor))
    await audit.log("subscriber.password_change", actor, "subscriber", str(sub_id),
                    None, get_client_ip(request))
    return MessageResponse(message="Password updated")


@router.get("/{sub_id}/status", response_model=ApiResponse[SubscriberActiveStatus])
async def subscriber_status(
    sub_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    redis=Depends(get_redis),
    actor: User = Depends(require_admin_or_reseller),
):
    """Valida si el suscriptor tiene suscripción activa y cuántos dispositivos tiene."""
    svc = SubscriberService(db)
    # Un reseller no puede sondear suscriptores ajenos: si no es suyo, 404 (como
    # si no existiera). Para el admin (scope None) el comportamiento es el de hoy.
    scope = _scope(actor)
    if scope is not None:
        await svc.get_by_id(sub_id, owner_id=scope)
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
    await svc.set_status(sub_id, SubscriberStatus.suspended, owner_id=_scope(actor))
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
    await svc.set_status(sub_id, SubscriberStatus.active, owner_id=_scope(actor))
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
    await svc.delete(sub_id, owner_id=_scope(actor))
    await audit.log("subscriber.delete", actor, "subscriber", str(sub_id),
                    None, get_client_ip(request))
    return MessageResponse(message="Subscriber deleted")
