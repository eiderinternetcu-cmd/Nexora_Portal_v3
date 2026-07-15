import asyncio
import contextlib
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import update

from app.config import get_settings
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
from app.core.exceptions import NexoraException

settings = get_settings()

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


app = FastAPI(
    title="Nexora API",
    description="Nexora Middleware — Users, Subscribers, Devices & STB Core",
    version="2.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

# ── Middleware ────────────────────────────────────────────────────────────────

# Explicit origins are always allowed regardless of debug mode.
# In debug, also open wildcard (credentials must be False with wildcard per CORS spec).
_WEB_ORIGINS = [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "http://172.27.99.151:5173",
    "http://localhost:4173",
    "http://127.0.0.1:4173",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=(["*"] if settings.debug else _WEB_ORIGINS),
    allow_credentials=not settings.debug,  # False with wildcard (debug), True with explicit (prod)
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(RateLimitMiddleware)

# ── Exception handlers ────────────────────────────────────────────────────────

@app.exception_handler(NexoraException)
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


@app.exception_handler(Exception)
async def generic_exception_handler(request: Request, exc: Exception):
    if settings.debug:
        raise exc
    return JSONResponse(
        status_code=500,
        content={"success": False, "error": "Internal server error"},
    )

# ── Routes ────────────────────────────────────────────────────────────────────

app.include_router(v1_router)         # /api/v1/  — legacy compat
app.include_router(admin_router)      # /api/admin/
app.include_router(stb_router)        # /api/stb/
app.include_router(subscriber_router) # /api/subscriber/
app.include_router(client_router)     # /api/client/
app.include_router(internal_stream_auth_router)  # /internal/stream-auth/ (edge auth_request)


@app.get("/health", tags=["Health"])
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
