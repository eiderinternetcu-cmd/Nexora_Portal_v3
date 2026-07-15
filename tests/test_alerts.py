"""M2 — node/stream alerting + multi-node health."""
import pytest

from app.config import get_settings
from app.services.alert_service import AlertService
from app.services import node_health as nh

pytestmark = pytest.mark.asyncio


async def test_alert_open_idempotent_resolve_list(redis_client):
    a = AlertService(redis_client)
    assert await a.record_node_health("co-main", False, "timeout") == "opened"
    assert await a.record_node_health("co-main", False, "timeout") is None  # already open
    active = await a.active_alerts()
    assert len(active) == 1 and active[0]["id"] == "co-main" and active[0]["status"] == "down"
    assert await a.record_node_health("co-main", True) == "resolved"
    assert await a.active_alerts() == []
    assert await a.record_node_health("co-main", True) is None  # nothing to resolve


class _FakeClient:
    is_configured = True
    async def check_connectivity(self):
        return True
    async def list_streams(self):
        return [1, 2, 3]


async def test_check_node_reports_health(monkeypatch):
    monkeypatch.setattr(nh, "get_flussonic_node_client", lambda nid: _FakeClient())
    monkeypatch.setattr(get_settings(), "flussonic_base_url", "http://ec.example:8002")
    out = await nh.check_node("ec-main")
    assert out["configured"] and out["reachable"]
    assert out["stream_count"] == 3 and out["region"] == "EC"
    assert out["host"] == "ec.example:8002"


async def test_configured_node_ids(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "flussonic_base_url", "http://ec:8002")
    monkeypatch.setattr(s, "flussonic_co_main_base_url", "")
    monkeypatch.setattr(s, "flussonic_ec_quito_base_url", "", raising=False)
    ids = nh.configured_node_ids()
    assert "ec-main" in ids and "co-main" not in ids and "ec-quito" not in ids
