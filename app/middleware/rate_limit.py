"""
Rate limiting por IP usando Redis sliding window.
"""
from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

from app.core.dependencies import get_client_ip
from app.redis_client import get_redis, key_rate_limit
from app.config import get_settings

settings = get_settings()

# path → max requests per 60s window
RATE_LIMITS: dict[str, int] = {
    # Auth endpoints — strict
    "/api/v1/auth/login": 10,
    "/api/v1/auth/refresh": 20,
    "/api/admin/auth/login": 10,
    "/api/admin/auth/refresh": 20,
    # Device registration — strict (prevent enumeration)
    "/api/v1/devices/register": 5,
    "/api/stb/register": 5,
    # Heartbeat — moderate (every 60s per device, allow slight burst)
    "/api/v1/devices/heartbeat": 30,
    "/api/stb/heartbeat": 30,
    # Playback auth — /play is heavier (full DB), /token is lighter
    "/api/stb/auth/play": 20,
    "/api/stb/auth/token": 30,
    # /api/stb/auth/validate intentionally omitted: uses global limit.
    # Flussonic calls it per-stream; keep the global 60/min as floor.

    # Client (subscriber) endpoints
    "/api/client/auth/login": 10,
    "/api/client/auth/refresh": 20,
    "/api/client/profile/devices/register": 5,
    "/api/client/profile/devices/heartbeat": 30,
    "/api/client/playback/authorize": 20,
}


class RateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: ASGIApp):
        super().__init__(app)

    async def dispatch(self, request: Request, call_next) -> Response:
        redis = await get_redis()
        ip = self._get_ip(request)
        path = request.url.path

        limit = RATE_LIMITS.get(path, settings.rate_limit_per_minute)
        window = 60

        rkey = key_rate_limit(ip, path)
        current = await redis.incr(rkey)
        if current == 1:
            await redis.expire(rkey, window)

        if current > limit:
            return Response(
                content='{"success":false,"error":"Too many requests"}',
                status_code=429,
                media_type="application/json",
                headers={"Retry-After": str(window)},
            )

        response = await call_next(request)
        response.headers["X-RateLimit-Limit"] = str(limit)
        response.headers["X-RateLimit-Remaining"] = str(max(0, limit - current))
        return response

    @staticmethod
    def _get_ip(request: Request) -> str:
        """Delegates to the single client-IP resolver (NX-AUTH).

        This used to be a private copy that read the FIRST X-Forwarded-For value,
        so the per-IP bucket was keyed by a header the caller writes: a fresh fake
        address per request meant the login limit (10/min) never fired. Keeping
        the resolver in one place is the point — fixing get_client_ip while this
        copy survived would have left the rate limiter open.
        """
        return get_client_ip(request)
