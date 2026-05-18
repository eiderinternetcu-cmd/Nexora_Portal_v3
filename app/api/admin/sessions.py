"""
Admin — gestión de sesiones IPTV de suscriptores.

GET    /api/admin/sessions/live                 — sesiones IPTV activas en tiempo real
GET    /api/admin/sessions/subscriber/{sub_id}  — sesiones de un suscriptor
DELETE /api/admin/sessions/subscriber/{sub_id}  — revocar todas
DELETE /api/admin/sessions/{jti}                — revocar sesión por JTI

Al revocar:
  - Marca revoked_at en DB
  - Elimina Redis session key (nexora:session:{jti})
  - Elimina todos los playback tokens asociados (nexora:playback:{*})
  - Cierra la conexión IPTV en el ZSET de Redis
"""
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
import redis.asyncio as aioredis

from app.database import get_db
from app.redis_client import get_redis
from app.services.session_service import SessionService
from app.services.connection_service import ConnectionService
from app.schemas.session import SessionOut
from app.schemas.common import ApiResponse, MessageResponse
from app.core.dependencies import require_admin_or_reseller
from app.models.user import User
from app.models.session import Session
from app.models.subscriber import Subscriber

router = APIRouter(prefix="/sessions", tags=["Admin — Sessions"])


class LiveSessionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    session_id: str
    subscriber_id: str
    subscriber_username: str
    device_id: str | None
    ip_address: str | None
    created_at: datetime
    expires_at: datetime
    last_heartbeat_at: datetime | None


@router.get("/live", response_model=list[LiveSessionOut])
async def list_live_sessions(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin_or_reseller),
):
    """All active IPTV sessions in real time (not revoked, not expired), newest first."""
    now = datetime.now(timezone.utc)
    result = await db.execute(
        select(Session, Subscriber.username)
        .join(Subscriber, Session.subscriber_id == Subscriber.id)
        .where(
            Session.revoked_at.is_(None),
            Session.expires_at > now,
        )
        .order_by(Session.created_at.desc())
        .limit(200)
    )
    return [
        LiveSessionOut(
            session_id=str(row.Session.id),
            subscriber_id=str(row.Session.subscriber_id),
            subscriber_username=row.username,
            device_id=str(row.Session.device_id) if row.Session.device_id else None,
            ip_address=row.Session.ip_address,
            created_at=row.Session.created_at,
            expires_at=row.Session.expires_at,
            last_heartbeat_at=row.Session.last_heartbeat_at,
        )
        for row in result.all()
    ]


@router.get("/subscriber/{sub_id}", response_model=ApiResponse[list[SessionOut]])
async def list_sessions(
    sub_id: uuid.UUID,
    only_active: bool = True,
    db: AsyncSession = Depends(get_db),
    redis: aioredis.Redis = Depends(get_redis),
    _: User = Depends(require_admin_or_reseller),
):
    """Lista sesiones IPTV de un suscriptor (DB, no Redis)."""
    svc = SessionService(redis, db)
    sessions = await svc.list_subscriber_sessions(sub_id, only_active=only_active)
    return ApiResponse(data=[SessionOut.model_validate(s) for s in sessions])


@router.delete("/subscriber/{sub_id}", response_model=MessageResponse)
async def revoke_all_sessions(
    sub_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    redis: aioredis.Redis = Depends(get_redis),
    _: User = Depends(require_admin_or_reseller),
):
    """
    Revoca todas las sesiones IPTV activas de un suscriptor:
      - revoked_at en DB
      - Limpia Redis session keys + playback tokens
      - Cierra conexiones en ZSET
    """
    svc = SessionService(redis, db)
    conn_svc = ConnectionService(redis)

    # Get active sessions first to close ZSET connections after revoke
    active_sessions = await svc.list_subscriber_sessions(sub_id, only_active=True)

    count = await svc.revoke_subscriber_sessions(sub_id)

    # Close each device's IPTV connection slot in ZSET
    for session in active_sessions:
        if session.device_id is not None:
            await conn_svc.close_connection(sub_id, session.device_id)

    await db.commit()
    return MessageResponse(message=f"{count} session(s) revoked")


@router.delete("/{jti}", response_model=MessageResponse)
async def revoke_session(
    jti: str,
    db: AsyncSession = Depends(get_db),
    redis: aioredis.Redis = Depends(get_redis),
    _: User = Depends(require_admin_or_reseller),
):
    """
    Revoca una sesión específica por access_token_jti:
      - revoked_at en DB
      - Limpia Redis session key + playback tokens asociados
      - Cierra la conexión IPTV en ZSET si el device_id está disponible
    """
    svc = SessionService(redis, db)
    conn_svc = ConnectionService(redis)

    # Load session before revoking to get subscriber_id + device_id for ZSET
    session = await svc.get_session_by_jti(jti)

    ok = await svc.revoke_iptv_session(jti)

    if ok and session is not None and session.device_id is not None:
        await conn_svc.close_connection(session.subscriber_id, session.device_id)

    await db.commit()

    if not ok:
        return MessageResponse(message="Session not found or already revoked")
    return MessageResponse(message="Session revoked")
