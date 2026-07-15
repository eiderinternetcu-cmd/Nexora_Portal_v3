"""AlertService — active operational alerts (M2), backed by Redis.

An alert is opened when a resource (Flussonic node) is detected DOWN and stays
active until it recovers. Delivery is structured logs (WARNING) plus a queryable
list at /admin/alerts; an external webhook/email sink can be added later without
changing callers.
"""
import json
import logging

import redis.asyncio as aioredis

logger = logging.getLogger("app.alerts")

_PREFIX = "nexora:alert:"


class AlertService:
    def __init__(self, redis: aioredis.Redis):
        self.redis = redis

    @staticmethod
    def _key(kind: str, ident: str) -> str:
        return f"{_PREFIX}{kind}:{ident}"

    async def record_node_health(
        self, node_id: str, healthy: bool, detail: str | None = None
    ) -> str | None:
        """Open an alert on the first DOWN, resolve it on recovery. Idempotent
        while the state persists. Returns 'opened' | 'resolved' | None."""
        key = self._key("node", node_id)
        exists = bool(await self.redis.exists(key))
        if not healthy and not exists:
            await self.redis.set(key, json.dumps({
                "kind": "node", "id": node_id, "status": "down", "detail": detail,
            }))
            logger.warning("ALERT opened — Flussonic node down: %s (%s)", node_id, detail or "")
            return "opened"
        if healthy and exists:
            await self.redis.delete(key)
            logger.info("ALERT resolved — Flussonic node back up: %s", node_id)
            return "resolved"
        return None

    async def active_alerts(self) -> list[dict]:
        alerts: list[dict] = []
        async for k in self.redis.scan_iter(match=_PREFIX + "*"):
            v = await self.redis.get(k)
            if not v:
                continue
            try:
                alerts.append(json.loads(v))
            except (ValueError, TypeError):
                pass
        return sorted(alerts, key=lambda a: (a.get("kind", ""), a.get("id", "")))
