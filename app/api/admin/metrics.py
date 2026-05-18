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

    return SystemMetrics(
        timestamp=now.isoformat(),
        active_iptv_sessions=active_sessions,
        redis_healthy=redis_ok,
        redis_latency_ms=redis_latency,
        postgres_healthy=True,  # DB query above succeeded; 500 fires on failure
        flussonic_configured=_flussonic.is_configured,
        flussonic_reachable=flussonic_reachable,
    )


@router.get("/nodes/health", response_model=list[NodeHealth])
async def get_nodes_health(
    _: User = Depends(require_admin_or_reseller),
):
    """Per-node Flussonic health check.

    Returns one entry per configured node. Phase 4.4 will extend this to
    the full multi-node registry. For now: single primary node (ec-main).
    """
    from app.config import get_settings
    s = get_settings()

    nodes: list[NodeHealth] = []

    if s.flussonic_base_url:
        parsed = urlparse(s.flussonic_base_url)
        reachable = False
        latency_ms: float | None = None
        stream_count: int | None = None

        if _flussonic.is_configured:
            t0 = time.monotonic()
            reachable = await _flussonic.check_connectivity()
            latency_ms = round((time.monotonic() - t0) * 1000, 2)

            if reachable:
                try:
                    streams = await _flussonic.list_streams()
                    stream_count = len(streams)
                except Exception:
                    pass

        nodes.append(NodeHealth(
            node_id="ec-main",
            host=parsed.netloc,
            region="EC",
            configured=_flussonic.is_configured,
            reachable=reachable,
            latency_ms=latency_ms,
            stream_count=stream_count,
        ))

    return nodes
