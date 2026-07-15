"""MetricsService — lightweight cumulative counters in Redis for observability (M2).

Tracks playback-authorize outcomes (success / failure by reason) so /admin/metrics
can report gate health without an external metrics backend. Counters are cumulative
(no TTL); a scrape/dashboard computes rates over time.
"""
import redis.asyncio as aioredis

_K_SUCCESS = "nexora:metrics:playback:success"
_K_FAILURE = "nexora:metrics:playback:failure"
_K_REASON = "nexora:metrics:playback:reason"   # hash: reason -> count


def _norm_reason(detail) -> str:
    """Normalize a NexoraException detail (str or {reason_code|error|message})
    into a bounded, stable reason label (fixed set of gate reasons)."""
    if isinstance(detail, dict):
        r = detail.get("reason_code") or detail.get("error") or detail.get("message") or "unknown"
    else:
        r = str(detail) if detail else "unknown"
    r = "".join(c if (c.isalnum() or c == "_") else "_" for c in str(r).strip().upper())
    return (r[:48] or "UNKNOWN")


class MetricsService:
    def __init__(self, redis: aioredis.Redis):
        self.redis = redis

    async def record_playback_success(self) -> None:
        await self.redis.incr(_K_SUCCESS)

    async def record_playback_failure(self, detail=None) -> None:
        await self.redis.incr(_K_FAILURE)
        await self.redis.hincrby(_K_REASON, _norm_reason(detail), 1)

    async def playback_snapshot(self) -> dict:
        success = int(await self.redis.get(_K_SUCCESS) or 0)
        failure = int(await self.redis.get(_K_FAILURE) or 0)
        raw = await self.redis.hgetall(_K_REASON) or {}
        by_reason = {
            (k.decode() if isinstance(k, (bytes, bytearray)) else k): int(v)
            for k, v in raw.items()
        }
        total = success + failure
        return {
            "authorize_total": total,
            "authorize_success": success,
            "authorize_failure": failure,
            "failure_rate": round(failure / total, 4) if total else 0.0,
            "failure_by_reason": by_reason,
        }
