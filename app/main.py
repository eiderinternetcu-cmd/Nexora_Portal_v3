from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.database import engine
from app.redis_client import get_redis, close_redis
from app.api.v1.router import router as v1_router
from app.api.admin.router import router as admin_router
from app.api.stb.router import router as stb_router
from app.api.subscriber.router import router as subscriber_router
from app.api.client.router import router as client_router
from app.middleware.rate_limit import RateLimitMiddleware
from app.core.exceptions import NexoraException

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    redis = await get_redis()
    await redis.ping()
    print(f"[nexora-api] Redis connected")
    yield
    # Shutdown
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

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"] if settings.debug else [],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(RateLimitMiddleware)

# ── Exception handlers ────────────────────────────────────────────────────────

@app.exception_handler(NexoraException)
async def nexora_exception_handler(request: Request, exc: NexoraException):
    return JSONResponse(
        status_code=exc.status_code,
        content={"success": False, "error": exc.detail},
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
