"""M2 — playback observability counters (MetricsService + /authorize wiring)."""
import httpx
import pytest
from httpx import ASGITransport

from app.services.metrics_service import MetricsService, _norm_reason
from app.services import stream_auth_service as sas_mod

pytestmark = pytest.mark.asyncio


# ── Unit: counters ────────────────────────────────────────────────────────────

async def test_counters_and_snapshot(redis_client):
    m = MetricsService(redis_client)
    await m.record_playback_success()
    await m.record_playback_success()
    await m.record_playback_failure("DEVICE_NOT_REGISTERED")
    await m.record_playback_failure({"reason_code": "CHANNEL_NOT_INCLUDED"})
    await m.record_playback_failure("DEVICE_NOT_REGISTERED")
    snap = await m.playback_snapshot()
    assert snap["authorize_total"] == 5
    assert snap["authorize_success"] == 2
    assert snap["authorize_failure"] == 3
    assert snap["failure_rate"] == round(3 / 5, 4)
    assert snap["failure_by_reason"]["DEVICE_NOT_REGISTERED"] == 2
    assert snap["failure_by_reason"]["CHANNEL_NOT_INCLUDED"] == 1


def test_norm_reason_bounded_and_stable():
    assert _norm_reason({"reason_code": "DEVICE_LIMIT_REACHED"}) == "DEVICE_LIMIT_REACHED"
    assert _norm_reason("No active subscription found") == "NO_ACTIVE_SUBSCRIPTION_FOUND"
    assert _norm_reason(None) == "UNKNOWN"
    assert len(_norm_reason("x" * 200)) <= 48


async def test_empty_snapshot(redis_client):
    snap = await MetricsService(redis_client).playback_snapshot()
    assert snap == {
        "authorize_total": 0, "authorize_success": 0, "authorize_failure": 0,
        "failure_rate": 0.0, "failure_by_reason": {},
    }


# ── Integration: /authorize records success and failure ──────────────────────

async def _client(world, redis):
    from app.main import app
    from app.database import get_db
    from app.redis_client import get_redis
    from app.core.dependencies import get_current_subscriber
    app.dependency_overrides[get_db] = lambda: world["session"]
    app.dependency_overrides[get_redis] = lambda: redis
    app.dependency_overrides[get_current_subscriber] = lambda: world["subscriber"]
    return app


async def test_authorize_records_success_and_failure(entitlement_world, redis_client, monkeypatch):
    from app.models.device import Device
    monkeypatch.setattr(sas_mod.settings, "entitlement_enforce", True)
    dev = "dev-metrics-1"  # >=6 chars (schema); fixture 'dev-1' is too short for the API
    entitlement_world["session"].add(
        Device(subscriber_id=entitlement_world["subscriber"].id, device_id=dev,
               device_type="web_player", is_blocked=False)
    )
    await entitlement_world["session"].commit()
    app = await _client(entitlement_world, redis_client)
    try:
        async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            # channel in plan → 200 → success counter
            r = await c.post("/api/client/playback/authorize",
                             json={"channel_id": "canal-1", "device_id": dev})
            assert r.status_code == 200, r.text
            # channel NOT in plan + enforce on → 403 → failure counter (reason)
            r = await c.post("/api/client/playback/authorize",
                             json={"channel_id": "canal-99", "device_id": dev})
            assert r.status_code == 403
    finally:
        app.dependency_overrides.clear()

    snap = await MetricsService(redis_client).playback_snapshot()
    assert snap["authorize_success"] == 1
    assert snap["authorize_failure"] == 1
    assert snap["failure_by_reason"].get("CHANNEL_NOT_INCLUDED") == 1
