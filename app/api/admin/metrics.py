"""Admin — observabilidad y métricas del sistema Nexora.

GET /api/admin/metrics       — snapshot de salud: sesiones activas, Redis, Postgres, Flussonic
GET /api/admin/nodes/health  — estado de cada nodo Flussonic configurado
"""
import time
from datetime import datetime, timezone
from urllib.parse import urlparse

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
import redis.asyncio as aioredis

from app.database import get_db
from app.redis_client import get_redis
from app.core.dependencies import require_admin_or_reseller
from app.models.user import User
from app.models.session import Session
from app.integrations.flussonic_client import get_flussonic_client
from app.services.metrics_service import MetricsService
from app.services.alert_service import AlertService
from app.services.node_health import check_all_nodes
from app.services.audit_service import AuditService

router = APIRouter(tags=["Admin Metrics"])
_flussonic = get_flussonic_client()


class SystemMetrics(BaseModel):
    timestamp: str
    active_iptv_sessions: int
    redis_healthy: bool
    redis_latency_ms: float
    postgres_healthy: bool
    flussonic_configured: bool
    flussonic_reachable: bool | None  # None = not checked (not configured)
    playback: dict  # cumulative authorize counters (total/success/failure/rate/by_reason)


class NodeHealth(BaseModel):
    node_id: str
    host: str
    region: str | None
    configured: bool
    reachable: bool
    latency_ms: float | None
    stream_count: int | None


@router.get("/metrics", response_model=SystemMetrics)
async def get_system_metrics(
    db: AsyncSession = Depends(get_db),
    redis: aioredis.Redis = Depends(get_redis),
    _: User = Depends(require_admin_or_reseller),
):
    """System health snapshot: active sessions, Redis, Postgres, Flussonic."""
    now = datetime.now(timezone.utc)

    # Active IPTV sessions from DB
    result = await db.execute(
        select(func.count(Session.id)).where(
            Session.revoked_at.is_(None),
            Session.expires_at > now,
        )
    )
    active_sessions: int = result.scalar_one()

    # Redis health + round-trip latency
    redis_ok = False
    redis_latency = 0.0
    try:
        t0 = time.monotonic()
        await redis.ping()
        redis_latency = round((time.monotonic() - t0) * 1000, 2)
        redis_ok = True
    except Exception:
        pass

    # Flussonic reachability (only if configured — avoid blocking on unconfigured nodes)
    flussonic_reachable: bool | None = None
    if _flussonic.is_configured:
        flussonic_reachable = await _flussonic.check_connectivity()

    playback = await MetricsService(redis).playback_snapshot()

    return SystemMetrics(
        timestamp=now.isoformat(),
        active_iptv_sessions=active_sessions,
        redis_healthy=redis_ok,
        redis_latency_ms=redis_latency,
        postgres_healthy=True,  # DB query above succeeded; 500 fires on failure
        flussonic_configured=_flussonic.is_configured,
        flussonic_reachable=flussonic_reachable,
        playback=playback,
    )


@router.get("/nodes/health", response_model=list[NodeHealth])
async def get_nodes_health(
    _: User = Depends(require_admin_or_reseller),
):
    """Per-node Flussonic health for EVERY configured node (ec-main, co-main,
    ec-quito), so a down secondary node is visible — not just the primary."""
    return [NodeHealth(**n) for n in await check_all_nodes()]


@router.get("/alerts")
async def get_active_alerts(
    redis: aioredis.Redis = Depends(get_redis),
    _: User = Depends(require_admin_or_reseller),
):
    """Active operational alerts (e.g. Flussonic node down). Opened by the
    background stream-health monitor; cleared automatically on recovery."""
    return {"active": await AlertService(redis).active_alerts()}


@router.get("/audit")
async def get_audit_log(
    action: str | None = None,
    actor: str | None = None,
    limit: int = 50,
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin_or_reseller),
):
    """Append-only admin audit trail (most recent first), filterable by action
    and actor username."""
    rows = await AuditService(db).list(action=action, actor_username=actor, limit=limit, offset=offset)
    return [
        {
            "id": str(r.id),
            "actor_username": r.actor_username,
            "action": r.action,
            "target_type": r.target_type,
            "target_id": r.target_id,
            "details": r.details,
            "ip_address": r.ip_address,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in rows
    ]
