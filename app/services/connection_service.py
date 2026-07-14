"""
ConnectionService — control de conexiones IPTV concurrentes usando Redis ZSET.

ZSET: nexora:active_conns:{subscriber_id}
  score  = unix timestamp de expiración
  member = device_id (UUID string)

Flujo:
  heartbeat → extend_connection (renueva score)
  stream open → open_connection (verifica límite antes de abrir)
  stream close → close_connection (ZREM)
  Miembros expirados se limpian automáticamente en cada operación.
"""
import time
import uuid
import redis.asyncio as aioredis

from app.config import get_settings
from app.redis_client import key_active_connections

settings = get_settings()

# Atomic open: cleanup expired → renew-if-present → check-limit → add, in one
# server-side step so concurrent opens of DISTINCT new devices can never both
# observe count < max and both add (which would exceed max_connections).
#   KEYS[1]=zset  ARGV: 1=now 2=score 3=member 4=max 5=expire_ttl
#   returns 1 = opened/renewed, 0 = limit reached
_OPEN_CONNECTION_LUA = """
redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', ARGV[1])
if not redis.call('ZSCORE', KEYS[1], ARGV[3]) then
  if redis.call('ZCARD', KEYS[1]) >= tonumber(ARGV[4]) then
    return 0
  end
end
redis.call('ZADD', KEYS[1], ARGV[2], ARGV[3])
redis.call('EXPIRE', KEYS[1], ARGV[5])
return 1
"""


class ConnectionService:
    def __init__(self, redis: aioredis.Redis):
        self.redis = redis
        self.ttl = settings.heartbeat_ttl_seconds  # 180s
        self._open_script = redis.register_script(_OPEN_CONNECTION_LUA)

    async def _cleanup_expired(self, key: str) -> None:
        await self.redis.zremrangebyscore(key, "-inf", time.time())

    async def count_active(self, subscriber_id: str | uuid.UUID) -> int:
        key = key_active_connections(str(subscriber_id))
        await self._cleanup_expired(key)
        return await self.redis.zcard(key)

    async def get_active_devices(self, subscriber_id: str | uuid.UUID) -> list[str]:
        key = key_active_connections(str(subscriber_id))
        await self._cleanup_expired(key)
        return await self.redis.zrange(key, 0, -1)

    async def extend_connection(
        self, subscriber_id: str | uuid.UUID, device_id: str | uuid.UUID
    ) -> None:
        """Heartbeat renewal — adds or updates the entry with a fresh TTL."""
        key = key_active_connections(str(subscriber_id))
        score = time.time() + self.ttl
        await self.redis.zadd(key, {str(device_id): score})
        await self.redis.expire(key, self.ttl + 60)

    async def open_connection(
        self,
        subscriber_id: str | uuid.UUID,
        device_id: str | uuid.UUID,
        max_connections: int,
    ) -> bool:
        """
        Abre una nueva conexión IPTV (atómico, vía Lua).
        Retorna True si se abrió, False si el límite está alcanzado.
        Si el dispositivo ya está en el ZSET, renueva su TTL (no cuenta como nueva).

        El check-de-límite y el ZADD ocurren en un único script server-side, así que
        bajo concurrencia nunca se excede `max_connections` (no hay ventana entre
        contar y añadir).
        """
        key = key_active_connections(str(subscriber_id))
        now = time.time()
        result = await self._open_script(
            keys=[key],
            args=[now, now + self.ttl, str(device_id), int(max_connections), self.ttl + 60],
        )
        return bool(int(result))

    async def close_connection(
        self, subscriber_id: str | uuid.UUID, device_id: str | uuid.UUID
    ) -> None:
        key = key_active_connections(str(subscriber_id))
        await self.redis.zrem(key, str(device_id))

    async def is_connected(
        self, subscriber_id: str | uuid.UUID, device_id: str | uuid.UUID
    ) -> bool:
        key = key_active_connections(str(subscriber_id))
        score = await self.redis.zscore(key, str(device_id))
        if score is None:
            return False
        return score > time.time()
