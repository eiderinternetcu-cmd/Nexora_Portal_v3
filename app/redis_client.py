import redis.asyncio as aioredis
from app.config import get_settings

settings = get_settings()

_redis: aioredis.Redis | None = None


async def get_redis() -> aioredis.Redis:
    """Process-wide Redis client with an EXPLICITLY bounded pool.

    The bound and the wait-vs-fail choice are configuration, not library
    defaults — see REDIS_MAX_CONNECTIONS / REDIS_POOL_TIMEOUT_SECONDS in
    app/config.py for the numbers and why they are what they are. Short version:
    the previous unbounded `from_url` inherited whatever ceiling the installed
    redis-py shipped (100 in the deployed 8.1.0), and that pool raises
    MaxConnectionsError instead of queueing, so every op past the ceiling became
    a 500 during a reconnect stampede.
    """
    global _redis
    if _redis is None:
        if settings.redis_pool_timeout_seconds > 0:
            # Waits up to `timeout` for a free connection, then raises. Bursts
            # become a few ms of latency instead of a wall of 500s.
            pool = aioredis.BlockingConnectionPool.from_url(
                settings.redis_url,
                max_connections=settings.redis_max_connections,
                timeout=settings.redis_pool_timeout_seconds,
                encoding="utf-8",
                decode_responses=True,
            )
            _redis = aioredis.Redis(connection_pool=pool)
        else:
            # Escape hatch: fail fast, but still bounded (never the library default).
            _redis = aioredis.from_url(
                settings.redis_url,
                encoding="utf-8",
                decode_responses=True,
                max_connections=settings.redis_max_connections,
            )
    return _redis


async def close_redis() -> None:
    global _redis
    if _redis:
        await _redis.aclose()
        _redis = None


# ── Key helpers ──────────────────────────────────────────────────────────────

def key_session(token_jti: str) -> str:
    return f"nexora:session:{token_jti}"


def key_refresh(token_jti: str) -> str:
    return f"nexora:refresh:{token_jti}"


def key_blacklist(token_jti: str) -> str:
    return f"nexora:blacklist:{token_jti}"


def key_login_attempts(identifier: str) -> str:
    return f"nexora:login_attempts:{identifier}"


def key_lockout(identifier: str) -> str:
    return f"nexora:lockout:{identifier}"


def key_heartbeat(device_id: str) -> str:
    return f"nexora:heartbeat:{device_id}"


def key_rate_limit(ip: str, endpoint: str) -> str:
    return f"nexora:rate:{ip}:{endpoint}"


def key_active_connections(subscriber_id: str) -> str:
    """ZSET: member=device_id, score=expire_unix_timestamp"""
    return f"nexora:active_conns:{subscriber_id}"


def key_playback(token_jti: str) -> str:
    """Short-lived playback token store. TTL = playback_token_expire_seconds."""
    return f"nexora:playback:{token_jti}"


def key_session_playbacks(session_jti: str) -> str:
    """SET of playback JTIs issued under a session. Used for bulk revocation."""
    return f"nexora:session_playbacks:{session_jti}"


def key_client(token_jti: str) -> str:
    """Client (subscriber) access token. TTL = client_access_token_expire_hours."""
    return f"nexora:client:{token_jti}"


def key_client_refresh(token_jti: str) -> str:
    """Client (subscriber) refresh token. TTL = client_refresh_token_expire_days."""
    return f"nexora:client_refresh:{token_jti}"


def key_parental_unlock(subscriber_id: str, device_id: str) -> str:
    """Short-lived parental-PIN unlock grant (NX-PARENTAL).

    Keyed by subscriber AND device on purpose: unlocking on the parent's phone
    must not unlock the child's television. TTL = parental_pin_unlock_ttl_seconds,
    renewed on each use (sliding window).

    The failed-attempt counter and the lockout deliberately do NOT live here:
    they reuse key_login_attempts/key_lockout with a `parental:{subscriber_id}`
    identifier, which keeps one lockout implementation in the project and keeps
    the namespace disjoint from the admin (`admin:{username}`) and legacy
    (bare username) counters.
    """
    return f"nexora:parental_unlock:{subscriber_id}:{device_id}"


def key_stream_grant(node: str, stream_key: str, ip_hash: str) -> str:
    """Short-lived segment grant seeded by a token-validated manifest request.
    Lets tokenless HLS segments (same node+stream+client IP) pass auth_request.
    TTL = stream_auth_cache_ttl_seconds."""
    return f"nexora:stream_grant:{node}:{stream_key}:{ip_hash}"
