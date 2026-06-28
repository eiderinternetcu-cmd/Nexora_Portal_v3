import redis.asyncio as aioredis
from app.config import get_settings

settings = get_settings()

_redis: aioredis.Redis | None = None


async def get_redis() -> aioredis.Redis:
    global _redis
    if _redis is None:
        _redis = aioredis.from_url(
            settings.redis_url,
            encoding="utf-8",
            decode_responses=True,
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


def key_stream_grant(node: str, stream_key: str, ip_hash: str) -> str:
    """Short-lived segment grant seeded by a token-validated manifest request.
    Lets tokenless HLS segments (same node+stream+client IP) pass auth_request.
    TTL = stream_auth_cache_ttl_seconds."""
    return f"nexora:stream_grant:{node}:{stream_key}:{ip_hash}"
