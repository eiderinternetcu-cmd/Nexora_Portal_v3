"""Admin — observabilidad y métricas del sistema Nexora.

GET /api/admin/metrics             — snapshot de salud: sesiones activas, Redis, Postgres, Flussonic
GET /api/admin/metrics/prometheus  — el mismo snapshot en formato de exposición Prometheus (P2.1)
GET /api/admin/nodes/health        — estado de cada nodo Flussonic configurado
"""
import hmac
import time
from datetime import datetime, timezone
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
import redis.asyncio as aioredis

from app.config import get_settings
from app.database import get_db
from app.redis_client import get_redis
from app.core.dependencies import require_admin_or_reseller
from app.core.exceptions import unauthorized
from app.models.user import User
from app.models.session import Session
from app.integrations.flussonic_client import get_flussonic_client
from app.services.channel_service import ChannelService
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
    # P0.5: which probe strategy produced this reading, and — when it failed —
    # why, so "node down" in the admin panel names its cause instead of hiding it.
    probe_mode: str = "origin"
    detail: str | None = None


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


# ── Prometheus exposition (P2.1) ────────────────────────────────────────────
#
# Auth: a static bearer token (Settings.metrics_prometheus_token), NOT the
# admin JWT that guards every other /api/admin route. Prometheus scrapes on a
# timer with no login flow to renew a short-lived session token against, so
# reusing the admin JWT would mean either a token that never expires (bad) or
# one an operator has to manually refresh into the scrape_configs file forever
# (worse). A separate long-lived credential scoped to exactly one caller is the
# standard shape for this (Prometheus' scrape_configs has first-class
# `bearer_token` support). It is compared with hmac.compare_digest to avoid a
# timing side-channel, and the endpoint is DISABLED (401) whenever the token is
# left unset — an operator who forgets to configure it gets no scrape endpoint,
# never an open one. Network-level restriction (only the Prometheus host can
# reach this path) is expected as defense in depth on top of this, not instead
# of it.
#
# Cardinality: every label here is a small, fixed-size, non-user-identifying
# set — failure `reason` (bounded gate vocabulary, see MetricsService._norm_reason),
# `node_id`/`region` (currently 3 configured nodes), and `channel_key` (~41
# catalog rows). None of it is `subscriber_id`, a device id or an IP: any of
# those would turn a handful of series into one per user and let the time
# series database grow with the subscriber base instead of the catalog.
# `nexora_channel_active` is DB-only (is_active) and deliberately does NOT
# live-probe Flussonic per channel on every scrape — that would mean up to ~41
# signed-HLS round trips every scrape interval, hammering the edge on a timer
# for a fact a scrape doesn't need instantly. Live per-channel `alive` is
# on-demand only, at GET /api/admin/streams.
def _check_prometheus_token(request: Request) -> None:
    configured = get_settings().metrics_prometheus_token
    if not configured:
        raise unauthorized("Prometheus metrics endpoint is not configured")
    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not token or not hmac.compare_digest(token, configured):
        raise unauthorized("Invalid or missing metrics token")


def _prom_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def _prom_line(name: str, value, labels: dict[str, str] | None = None) -> str:
    if labels:
        rendered = ",".join(f'{k}="{_prom_escape(str(v))}"' for k, v in labels.items())
        return f"{name}{{{rendered}}} {value}"
    return f"{name} {value}"


async def _render_prometheus_text(db: AsyncSession, redis: aioredis.Redis) -> str:
    """Hand-rolled exposition text — see module docstring for why no
    prometheus-client dependency: the format is a handful of `# HELP`/`# TYPE`
    header pairs and `name{labels} value` lines (fewer than 40 here), all
    values already live in Redis/Postgres and are recomputed fresh per scrape,
    and prometheus-client's model is in-process counters/gauges you increment
    as things happen — a poor fit when the source of truth is Redis counters
    fed by a separate request path (MetricsService) rather than this
    process's memory. Adopting it would mean bridging its registry to values
    it never actually tracked, for no real reduction in code over this.
    """
    now = datetime.now(timezone.utc)
    lines: list[str] = []

    result = await db.execute(
        select(func.count(Session.id)).where(
            Session.revoked_at.is_(None), Session.expires_at > now,
        )
    )
    active_sessions: int = result.scalar_one()
    lines += [
        "# HELP nexora_iptv_sessions_active Currently active IPTV sessions (not revoked, not expired).",
        "# TYPE nexora_iptv_sessions_active gauge",
        _prom_line("nexora_iptv_sessions_active", active_sessions),
    ]

    redis_ok = False
    redis_latency = 0.0
    try:
        t0 = time.monotonic()
        await redis.ping()
        redis_latency = round((time.monotonic() - t0) * 1000, 2)
        redis_ok = True
    except Exception:
        pass
    lines += [
        "# HELP nexora_redis_up Whether Redis answered a PING for this scrape (1) or not (0).",
        "# TYPE nexora_redis_up gauge",
        _prom_line("nexora_redis_up", 1 if redis_ok else 0),
    ]
    if redis_ok:
        lines += [
            "# HELP nexora_redis_latency_ms Redis PING round-trip latency in milliseconds.",
            "# TYPE nexora_redis_latency_ms gauge",
            _prom_line("nexora_redis_latency_ms", redis_latency),
        ]

    lines += [
        "# HELP nexora_postgres_up Whether the Postgres query in this scrape succeeded (a failure raises 500 before this line is ever written).",
        "# TYPE nexora_postgres_up gauge",
        _prom_line("nexora_postgres_up", 1),
    ]

    lines += [
        "# HELP nexora_flussonic_configured Whether the primary Flussonic origin is configured.",
        "# TYPE nexora_flussonic_configured gauge",
        _prom_line("nexora_flussonic_configured", 1 if _flussonic.is_configured else 0),
    ]
    if _flussonic.is_configured:
        reachable = await _flussonic.check_connectivity()
        lines += [
            "# HELP nexora_flussonic_reachable Whether the primary Flussonic origin answered the management API.",
            "# TYPE nexora_flussonic_reachable gauge",
            _prom_line("nexora_flussonic_reachable", 1 if reachable else 0),
        ]

    playback = await MetricsService(redis).playback_snapshot()
    lines += [
        "# HELP nexora_playback_authorize_total Total playback /authorize outcomes recorded since process start.",
        "# TYPE nexora_playback_authorize_total counter",
        _prom_line("nexora_playback_authorize_total", playback["authorize_total"]),
        "# HELP nexora_playback_authorize_success_total Successful playback /authorize outcomes.",
        "# TYPE nexora_playback_authorize_success_total counter",
        _prom_line("nexora_playback_authorize_success_total", playback["authorize_success"]),
        "# HELP nexora_playback_authorize_failure_total Failed playback /authorize outcomes.",
        "# TYPE nexora_playback_authorize_failure_total counter",
        _prom_line("nexora_playback_authorize_failure_total", playback["authorize_failure"]),
        "# HELP nexora_playback_authorize_failure_reason_total Failed /authorize outcomes by normalized gate reason (bounded, fixed vocabulary — never a subscriber or IP).",
        "# TYPE nexora_playback_authorize_failure_reason_total counter",
    ]
    for reason, count in playback["failure_by_reason"].items():
        lines.append(_prom_line("nexora_playback_authorize_failure_reason_total", count, {"reason": reason}))

    nodes = await check_all_nodes()
    lines += [
        "# HELP nexora_node_reachable Whether a configured Flussonic node answered its health probe.",
        "# TYPE nexora_node_reachable gauge",
    ]
    for n in nodes:
        lines.append(_prom_line(
            "nexora_node_reachable", 1 if n["reachable"] else 0,
            {"node_id": n["node_id"], "region": n["region"] or "", "probe_mode": n["probe_mode"]},
        ))
    lines += [
        "# HELP nexora_node_latency_ms Node health probe round-trip latency in milliseconds.",
        "# TYPE nexora_node_latency_ms gauge",
    ]
    for n in nodes:
        if n["latency_ms"] is not None:
            lines.append(_prom_line("nexora_node_latency_ms", n["latency_ms"], {"node_id": n["node_id"]}))

    channels = await ChannelService(db).list_all()
    lines += [
        "# HELP nexora_channel_active Whether a catalog channel is active in the database. This is NOT liveness — it does not mean the source is up. See GET /api/admin/streams for live per-channel status.",
        "# TYPE nexora_channel_active gauge",
    ]
    for c in channels:
        lines.append(_prom_line("nexora_channel_active", 1 if c.is_active else 0, {"channel_key": c.channel_key}))

    return "\n".join(lines) + "\n"


@router.get("/metrics/prometheus", include_in_schema=False)
async def get_prometheus_metrics(
    request: Request,
    db: AsyncSession = Depends(get_db),
    redis: aioredis.Redis = Depends(get_redis),
):
    """Prometheus/OTel exposition-format snapshot. See the block comment above
    for the auth and cardinality decisions. `include_in_schema=False` because
    this route does not use the standard admin-JWT dependency the rest of
    /docs assumes, and it is not meant to be exercised from the admin panel.
    """
    _check_prometheus_token(request)
    body = await _render_prometheus_text(db, redis)
    return Response(content=body, media_type="text/plain; version=0.0.4; charset=utf-8")


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
