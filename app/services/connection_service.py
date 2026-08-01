"""
ConnectionService — control de conexiones IPTV concurrentes usando Redis ZSET.

ZSET: nexora:active_conns:{subscriber_id}
  score  = unix timestamp de expiración
  member = device_id (UUID string)

Flujo:
  heartbeat → extend_connection (renueva el score de un hueco YA existente; nunca lo crea)
  stream open → open_connection (verifica límite antes de abrir — único camino que crea)
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

# Atomic renew: cleanup expired → renew ONLY IF PRESENT, in one server-side step.
#
# WHY 'XX' AND WHY A SCRIPT (NX-CONC / stress F1)
# ──────────────────────────────────────────────
# This used to be a bare ZADD, which CREATES the member. That silently disabled
# the whole concurrency limit: _OPEN_CONNECTION_LUA deliberately skips the
# ZCARD check when the member already exists (renewing your own slot must not
# consume a second one), so a heartbeat that inserted the device made the next
# authorize skip the check entirely. Measured: 10 active connections on a plan
# of 3, i.e. the real ceiling became max_devices, not max_connections.
#
# A heartbeat RENEWS a slot; only open_connection() may CREATE one, because
# only that path evaluates max_connections. 'XX' is exactly that: update an
# existing member, never add.
#
# The ZREMRANGEBYSCORE must run in the SAME script and BEFORE the ZADD, or an
# EXPIRED member would be resurrected by 'XX' without any limit check — the same
# bypass through a slower door (let the slot lapse, let other devices take the
# freed slots, then heartbeat it back). Cleaning first makes the member truly
# absent, so 'XX' is a no-op and the device has to go through authorize again.
#
#   KEYS[1]=zset  ARGV: 1=now 2=score 3=member 4=expire_ttl
#   returns 1 = renewed, 0 = no live slot for this member (nothing created)
_EXTEND_CONNECTION_LUA = """
redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', ARGV[1])
if not redis.call('ZSCORE', KEYS[1], ARGV[3]) then
  return 0
end
redis.call('ZADD', KEYS[1], 'XX', ARGV[2], ARGV[3])
redis.call('EXPIRE', KEYS[1], ARGV[4])
return 1
"""


class ConnectionService:
    def __init__(self, redis: aioredis.Redis):
        self.redis = redis
        self.ttl = settings.heartbeat_ttl_seconds  # 180s
        self._open_script = redis.register_script(_OPEN_CONNECTION_LUA)
        self._extend_script = redis.register_script(_EXTEND_CONNECTION_LUA)

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
    ) -> bool:
        """Heartbeat renewal — refreshes an EXISTING slot's TTL. Never creates one.

        Returns True if a live slot was renewed, False if this device holds no
        slot (it never had one, or it expired). See _EXTEND_CONNECTION_LUA for
        why creating is forbidden here.

        A False is deliberately NOT an error and deliberately does not stop the
        heartbeat: the heartbeat is also the device liveness signal (last_seen,
        the `nexora:heartbeat:{device_id}` key, the IPTV session touch), and
        those must keep working for a device that is simply not playing anything
        — which is the ordinary case for an app sitting on the channel list.
        The caller stays silent and the client learns its state from the fields
        the heartbeat response already carries (`active_connections` /
        `max_connections`). The remedy is the same in every False case, and it
        is the only correct one: go through POST /playback/authorize, which is
        the single path that evaluates max_connections and may create a slot.
        """
        key = key_active_connections(str(subscriber_id))
        now = time.time()
        result = await self._extend_script(
            keys=[key],
            args=[now, now + self.ttl, str(device_id), self.ttl + 60],
        )
        return bool(int(result))

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
