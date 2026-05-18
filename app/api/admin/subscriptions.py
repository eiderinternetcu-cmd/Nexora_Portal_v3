"""
Admin — CRUD de suscripciones de suscriptores.

POST   /api/admin/subscribers/{sub_id}/subscriptions
GET    /api/admin/subscribers/{sub_id}/subscriptions
POST   /api/admin/subscribers/{sub_id}/subscriptions/{subscription_id}/renew
POST   /api/admin/subscribers/{sub_id}/subscriptions/{subscription_id}/cancel

Al cancelar, se revocan inmediatamente las sesiones IPTV activas del suscriptor
para cortar el playback en curso.
"""
import uuid
from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession
import redis.asyncio as aioredis

from app.database import get_db
from app.redis_client import get_redis
from app.core.dependencies import require_admin_or_reseller, get_client_ip
from app.services.subscription_service import SubscriptionService
from app.services.session_service import SessionService
from app.services.connection_service import ConnectionService
from app.services.audit_service import AuditService
from app.schemas.subscription import (
    SubscriptionAdminCreate,
    SubscriptionRenew,
    SubscriptionOut,
)
from app.schemas.common import ApiResponse, MessageResponse
from app.models.user import User

router = APIRouter(prefix="/subscribers", tags=["Admin — Subscriptions"])


@router.post(
    "/{sub_id}/subscriptions",
    response_model=ApiResponse[SubscriptionOut],
    status_code=201,
)
async def create_subscription(
    sub_id: uuid.UUID,
    body: SubscriptionAdminCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_admin_or_reseller),
):
    """
    Crea una nueva suscripción para un suscriptor.

    - Valida que el suscriptor exista.
    - Valida que el plan exista y esté activo.
    - Desactiva la suscripción activa previa si existe (conserva historial).
    - expires_at = now + plan.duration_days.
    """
    svc = SubscriptionService(db)
    audit = AuditService(db)

    subscription = await svc.create(
        subscriber_id=sub_id,
        plan_id=body.plan_id,
        actor_id=actor.id,
        renewal_note=body.renewal_note,
    )
    await audit.log(
        "subscription.create",
        actor,
        "subscription",
        str(subscription.id),
        {
            "subscriber_id": str(sub_id),
            "plan_id": str(body.plan_id),
            "expires_at": subscription.expires_at.isoformat(),
        },
        get_client_ip(request),
    )
    return ApiResponse(
        data=SubscriptionOut.model_validate(subscription),
        message="Subscription created",
    )


@router.get(
    "/{sub_id}/subscriptions",
    response_model=ApiResponse[list[SubscriptionOut]],
)
async def list_subscriptions(
    sub_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin_or_reseller),
):
    """Lista el historial completo de suscripciones del suscriptor, más reciente primero."""
    svc = SubscriptionService(db)
    subscriptions = await svc.list_for_subscriber(sub_id)
    return ApiResponse(data=[SubscriptionOut.model_validate(s) for s in subscriptions])


@router.post(
    "/{sub_id}/subscriptions/{subscription_id}/renew",
    response_model=ApiResponse[SubscriptionOut],
)
async def renew_subscription(
    sub_id: uuid.UUID,
    subscription_id: uuid.UUID,
    body: SubscriptionRenew,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_admin_or_reseller),
):
    """
    Renueva una suscripción:

    - Si plan_id se provee, cambia el plan (debe estar activo).
    - Si la suscripción está vigente, extiende desde expires_at.
    - Si ya venció, extiende desde now.
    - Siempre marca is_active=True.
    """
    svc = SubscriptionService(db)
    audit = AuditService(db)

    subscription = await svc.renew(
        subscriber_id=sub_id,
        subscription_id=subscription_id,
        plan_id=body.plan_id,
        renewal_note=body.renewal_note,
        actor_id=actor.id,
    )
    await audit.log(
        "subscription.renew",
        actor,
        "subscription",
        str(subscription_id),
        {
            "subscriber_id": str(sub_id),
            "plan_id": str(subscription.plan_id),
            "new_expires_at": subscription.expires_at.isoformat(),
            "renewal_note": body.renewal_note,
        },
        get_client_ip(request),
    )
    return ApiResponse(
        data=SubscriptionOut.model_validate(subscription),
        message="Subscription renewed",
    )


@router.post(
    "/{sub_id}/subscriptions/{subscription_id}/cancel",
    response_model=MessageResponse,
)
async def cancel_subscription(
    sub_id: uuid.UUID,
    subscription_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    redis: aioredis.Redis = Depends(get_redis),
    actor: User = Depends(require_admin_or_reseller),
):
    """
    Cancela una suscripción (is_active=False, no borra el registro).

    Además revoca inmediatamente todas las sesiones IPTV activas del suscriptor
    para cortar el playback en curso: elimina Redis keys + playback tokens + ZSET connections.
    """
    svc = SubscriptionService(db)
    session_svc = SessionService(redis, db)
    conn_svc = ConnectionService(redis)
    audit = AuditService(db)

    await svc.cancel(sub_id, subscription_id)

    # Revoke active IPTV sessions → immediate playback cutoff
    active_sessions = await session_svc.list_subscriber_sessions(sub_id, only_active=True)
    revoked_count = await session_svc.revoke_subscriber_sessions(sub_id)
    for session in active_sessions:
        if session.device_id is not None:
            await conn_svc.close_connection(sub_id, session.device_id)

    await audit.log(
        "subscription.cancel",
        actor,
        "subscription",
        str(subscription_id),
        {
            "subscriber_id": str(sub_id),
            "iptv_sessions_revoked": revoked_count,
        },
        get_client_ip(request),
    )
    msg = f"Subscription cancelled. {revoked_count} active IPTV session(s) revoked."
    return MessageResponse(message=msg)
