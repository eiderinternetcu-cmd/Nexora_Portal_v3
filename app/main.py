import asyncio
import contextlib
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import update

from app.config import Settings, get_settings
from app.database import engine, AsyncSessionLocal
from app.models.session import Session
from app.redis_client import get_redis, close_redis
from app.api.v1.router import router as v1_router
from app.api.admin.router import router as admin_router
from app.api.stb.router import router as stb_router
from app.api.subscriber.router import router as subscriber_router
from app.api.client.router import router as client_router
from app.api.internal.stream_auth import router as internal_stream_auth_router
from app.middleware.rate_limit import RateLimitMiddleware
from app.middleware.correlation import CorrelationIdMiddleware, configure_correlation_logging
from app.core.exceptions import NexoraException

settings = get_settings()
configure_correlation_logging()

_CLEANUP_INTERVAL_SECONDS = 900  # 15 minutes
_STREAM_MONITOR_INTERVAL_SECONDS = 120  # 2 minutes


async def _cleanup_expired_sessions() -> None:
    """Background task: mark expired IPTV sessions as revoked for DB hygiene.

    Sessions expire naturally (expires_at < NOW) and are already excluded from all
    active-session queries. This cleanup marks them as revoked so they don't appear
    as phantom records in admin views that don't filter by expires_at.
    Runs every 15 minutes; first run is delayed to avoid startup load.
    """
    await asyncio.sleep(60)  # let the app warm up before first run
    while True:
        try:
            now = datetime.now(timezone.utc)
            async with AsyncSessionLocal() as db:
                result = await db.execute(
                    update(Session)
                    .where(Session.revoked_at.is_(None), Session.expires_at < now)
                    .values(revoked_at=now)
                    .returning(Session.id)
                )
                count = len(result.fetchall())
                if count:
                    print(f"[nexora-api] Cleaned up {count} expired session(s)")
                await db.commit()
        except Exception as exc:
            print(f"[nexora-api] Session cleanup error: {exc}")
        await asyncio.sleep(_CLEANUP_INTERVAL_SECONDS)


async def _stream_health_monitor() -> None:
    """Background task (M2): poll every configured Flussonic node and open/resolve
    alerts when a node goes down/recovers. Runs every 2 min; first run delayed."""
    from app.services.node_health import check_all_nodes
    from app.services.alert_service import AlertService

    await asyncio.sleep(90)  # warmup
    while True:
        try:
            redis = await get_redis()
            alerts = AlertService(redis)
            for n in await check_all_nodes():
                detail = None if n["reachable"] else f"host={n['host']} configured={n['configured']}"
                await alerts.record_node_health(n["node_id"], n["reachable"], detail)
        except Exception as exc:
            print(f"[nexora-api] Stream health monitor error: {exc}")
        await asyncio.sleep(_STREAM_MONITOR_INTERVAL_SECONDS)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    redis = await get_redis()
    await redis.ping()
    print("[nexora-api] Redis connected")
    cleanup_task = asyncio.create_task(_cleanup_expired_sessions())
    monitor_task = asyncio.create_task(_stream_health_monitor())
    yield
    # Shutdown
    for t in (cleanup_task, monitor_task):
        t.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await t
    await close_redis()
    await engine.dispose()
    print("[nexora-api] Shutdown complete")


# ── Exception handlers ────────────────────────────────────────────────────────

async def nexora_exception_handler(request: Request, exc: NexoraException):
    # Consistent contract: `error` is ALWAYS a string. A structured detail
    # ({"reason_code","message"}) is flattened to error=message + reason_code.
    detail = exc.detail
    content: dict = {"success": False}
    if isinstance(detail, dict):
        content["error"] = detail.get("message", "error")
        if detail.get("reason_code"):
            content["reason_code"] = detail["reason_code"]
    else:
        content["error"] = detail
    return JSONResponse(
        status_code=exc.status_code,
        content=content,
        headers=getattr(exc, "headers", None),
    )


async def generic_exception_handler(request: Request, exc: Exception):
    if settings.debug:
        raise exc
    return JSONResponse(
        status_code=500,
        content={"success": False, "error": "Internal server error"},
    )

# ── Routes ────────────────────────────────────────────────────────────────────

async def health():
    redis = await get_redis()
    redis_ok = await redis.ping()
    return {
        "status": "ok",
        "service": "nexora-api",
        "version": "1.0.0",
        "redis": "ok" if redis_ok else "error",
        "version": "2.0.0",
    }


# ── Application factory ───────────────────────────────────────────────────────

def create_app(config: Settings | None = None) -> FastAPI:
    """Build the ASGI app for `config` (defaults to the process settings).

    A factory (instead of a bare module-level FastAPI()) so the two
    environment-dependent decisions below — whether the API schema is published
    and which browser origins are allowed — can be exercised in tests without
    reimporting this module.
    """
    cfg = config or settings

    # /docs, /redoc and /openapi.json publish the complete API map, including
    # /internal/stream-auth/validate and its parameter contract — the exact map
    # someone attacking playback authorization needs. Closed in production;
    # unchanged (open) in development and staging.
    docs_enabled = not cfg.is_production

    application = FastAPI(
        title="Nexora API",
        description="Nexora Middleware — Users, Subscribers, Devices & STB Core",
        version="2.0.0",
        docs_url="/docs" if docs_enabled else None,
        redoc_url="/redoc" if docs_enabled else None,
        openapi_url="/openapi.json" if docs_enabled else None,
        lifespan=lifespan,
    )

    # ── Middleware ────────────────────────────────────────────────────────────
    # Allowed origins come from CORS_ALLOW_ORIGINS (CSV); the default keeps the
    # historical development origins so an unset variable changes nothing.
    # In debug, also open wildcard (credentials MUST be False with wildcard per
    # the CORS spec — browsers reject wildcard + credentials).
    application.add_middleware(
        CORSMiddleware,
        allow_origins=(["*"] if cfg.debug else cfg.cors_allowed_origins),
        allow_credentials=not cfg.debug,  # False with wildcard (debug), True with explicit (prod)
        allow_methods=["*"],
        allow_headers=["*"],
    )
    application.add_middleware(RateLimitMiddleware)
    application.add_middleware(CorrelationIdMiddleware)

    # ── Exception handlers ────────────────────────────────────────────────────
    application.add_exception_handler(NexoraException, nexora_exception_handler)
    application.add_exception_handler(Exception, generic_exception_handler)

    # ── Routes ────────────────────────────────────────────────────────────────
    application.include_router(v1_router)         # /api/v1/  — legacy compat
    application.include_router(admin_router)      # /api/admin/
    application.include_router(stb_router)        # /api/stb/
    application.include_router(subscriber_router) # /api/subscriber/
    application.include_router(client_router)     # /api/client/
    application.include_router(internal_stream_auth_router)  # /internal/stream-auth/ (edge auth_request)

    application.add_api_route("/health", health, methods=["GET"], tags=["Health"])

    return application


app = create_app()
