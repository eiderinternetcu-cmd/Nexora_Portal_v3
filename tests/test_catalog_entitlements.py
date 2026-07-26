"""Catalog filtering by subscriber entitlements (CATALOG_ENTITLEMENT_FILTER).

The client catalog used to return every active channel while plan_channels was
enforced only at playback → the user pressed Play and got 403
CHANNEL_NOT_INCLUDED. These tests pin the flagged behaviour:

  - flag OFF (default)          → full active catalog, byte-for-byte as before
  - flag ON                     → only the channels of the plan in force
  - flag ON, no plan in force   → full active catalog (deliberate, not empty)
  - flag ON                     → bounded query count (no N+1)
"""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from httpx import ASGITransport
from sqlalchemy import event
from sqlalchemy.engine import Engine

from app.services.channel_service import ChannelService
from app.services import channel_service as cs_mod

pytestmark = pytest.mark.asyncio


def _keys(channels) -> list[str]:
    return [c.channel_key for c in channels]


async def _visible(world) -> list[str]:
    svc = ChannelService(world["session"])
    return _keys(await svc.list_visible_for_subscriber(world["subscriber"].id))


@contextmanager
def _captured_sql():
    """Collect every statement sent to the DB inside the block.

    Hooks the Core-level event on the Engine class: the async engine wraps a
    sync one, so this sees exactly what hits the cursor — including anything a
    lazy relationship load would trigger.
    """
    statements: list[str] = []

    def _hook(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(Engine, "before_cursor_execute", _hook)
    try:
        yield statements
    finally:
        event.remove(Engine, "before_cursor_execute", _hook)


# ── Flag OFF → current behaviour untouched ───────────────────────────────────

async def test_catalog_unfiltered_when_flag_off(entitlement_world, monkeypatch):
    monkeypatch.setattr(cs_mod.settings, "catalog_entitlement_filter", False)
    # canal-99 is NOT in the plan and must still be listed (legacy behaviour)
    assert await _visible(entitlement_world) == ["canal-1", "canal-99"]


async def test_catalog_flag_off_matches_list_active_exactly(entitlement_world, monkeypatch):
    monkeypatch.setattr(cs_mod.settings, "catalog_entitlement_filter", False)
    svc = ChannelService(entitlement_world["session"])
    legacy = await svc.list_active()
    current = await svc.list_visible_for_subscriber(entitlement_world["subscriber"].id)
    assert [c.id for c in current] == [c.id for c in legacy]


async def test_flag_defaults_to_off(monkeypatch):
    """Deploying must not change production behaviour."""
    monkeypatch.delenv("CATALOG_ENTITLEMENT_FILTER", raising=False)
    from app.config import Settings
    assert Settings(_env_file=None).catalog_entitlement_filter is False


# ── Flag ON → only the plan's channels ───────────────────────────────────────

async def test_catalog_filtered_when_flag_on(entitlement_world, monkeypatch):
    monkeypatch.setattr(cs_mod.settings, "catalog_entitlement_filter", True)
    assert await _visible(entitlement_world) == ["canal-1"]


async def test_disabled_plan_channel_row_is_excluded(entitlement_world, monkeypatch):
    """is_enabled=False is a soft removal — same rule as EntitlementService."""
    from sqlalchemy import select
    from app.models.plan_channel import PlanChannel

    monkeypatch.setattr(cs_mod.settings, "catalog_entitlement_filter", True)
    session = entitlement_world["session"]
    pc = (await session.execute(select(PlanChannel))).scalar_one()
    pc.is_enabled = False
    await session.commit()
    assert await _visible(entitlement_world) == []


async def test_inactive_channel_never_listed_even_if_in_plan(entitlement_world, monkeypatch):
    monkeypatch.setattr(cs_mod.settings, "catalog_entitlement_filter", True)
    entitlement_world["ch_in"].is_active = False
    await entitlement_world["session"].commit()
    assert await _visible(entitlement_world) == []


# ── Flag ON + no plan in force → full catalog (documented decision) ──────────

async def test_no_active_subscription_shows_full_catalog(entitlement_world, monkeypatch):
    """An expired/lapsed user must still see what renewing gives back."""
    monkeypatch.setattr(cs_mod.settings, "catalog_entitlement_filter", True)
    entitlement_world["subscription"].is_active = False
    await entitlement_world["session"].commit()
    assert await _visible(entitlement_world) == ["canal-1", "canal-99"]


async def test_expired_subscription_shows_full_catalog(entitlement_world, monkeypatch):
    monkeypatch.setattr(cs_mod.settings, "catalog_entitlement_filter", True)
    entitlement_world["subscription"].expires_at = datetime.now(timezone.utc) - timedelta(days=1)
    await entitlement_world["session"].commit()
    assert await _visible(entitlement_world) == ["canal-1", "canal-99"]


async def test_not_started_subscription_shows_full_catalog(entitlement_world, monkeypatch):
    monkeypatch.setattr(cs_mod.settings, "catalog_entitlement_filter", True)
    entitlement_world["subscription"].starts_at = datetime.now(timezone.utc) + timedelta(days=1)
    await entitlement_world["session"].commit()
    assert await _visible(entitlement_world) == ["canal-1", "canal-99"]


async def test_inactive_plan_shows_full_catalog(entitlement_world, monkeypatch):
    monkeypatch.setattr(cs_mod.settings, "catalog_entitlement_filter", True)
    entitlement_world["plan"].is_active = False
    await entitlement_world["session"].commit()
    assert await _visible(entitlement_world) == ["canal-1", "canal-99"]


# ── Consistency with playback: no channel is shown that Play would refuse ────

async def test_filtered_catalog_agrees_with_entitlement_service(entitlement_world, monkeypatch):
    from app.services.entitlement_service import EntitlementService

    monkeypatch.setattr(cs_mod.settings, "catalog_entitlement_filter", True)
    world = entitlement_world
    ent = EntitlementService(world["session"])
    visible = await _visible(world)
    for key in ("canal-1", "canal-99"):
        allowed = (
            await ent.can_watch_channel(
                world["subscriber"].id, world["device"].device_id, key
            )
        ).allow
        assert (key in visible) is allowed, key


# ── Performance: bounded query count, independent of catalog size ────────────

async def _seed_many(world, n: int) -> None:
    from app.models.channel import Channel
    from app.models.plan_channel import PlanChannel

    session = world["session"]
    channels = [
        Channel(channel_key=f"bulk-{i}", number=100 + i, name=f"Bulk {i}",
                stream_key=f"BK{i}", is_active=True)
        for i in range(n)
    ]
    session.add_all(channels)
    await session.flush()
    session.add_all([
        PlanChannel(plan_id=world["plan"].id, channel_id=c.id, is_enabled=True)
        for c in channels
    ])
    await session.commit()


async def test_filtered_catalog_is_at_most_two_queries(entitlement_world, monkeypatch):
    monkeypatch.setattr(cs_mod.settings, "catalog_entitlement_filter", True)
    await _seed_many(entitlement_world, 40)  # ~prod scale (43 channels)

    svc = ChannelService(entitlement_world["session"])
    with _captured_sql() as statements:
        channels = await svc.list_visible_for_subscriber(
            entitlement_world["subscriber"].id
        )

    selects = [s for s in statements if s.lstrip().upper().startswith("SELECT")]
    assert len(selects) <= 2, selects           # plan resolution + JOIN, no N+1
    assert len(channels) == 41                  # canal-1 + 40 bulk (canal-99 excluded)
    assert "canal-99" not in _keys(channels)


async def test_query_count_does_not_grow_with_catalog_size(entitlement_world, monkeypatch):
    monkeypatch.setattr(cs_mod.settings, "catalog_entitlement_filter", True)
    svc = ChannelService(entitlement_world["session"])
    sid = entitlement_world["subscriber"].id

    with _captured_sql() as small:
        await svc.list_visible_for_subscriber(sid)
    await _seed_many(entitlement_world, 60)
    with _captured_sql() as large:
        await svc.list_visible_for_subscriber(sid)

    n_small = len([s for s in small if s.lstrip().upper().startswith("SELECT")])
    n_large = len([s for s in large if s.lstrip().upper().startswith("SELECT")])
    assert n_large == n_small


async def test_unfiltered_catalog_is_one_query(entitlement_world, monkeypatch):
    """Flag OFF must not add any DB round-trip vs. today."""
    monkeypatch.setattr(cs_mod.settings, "catalog_entitlement_filter", False)
    svc = ChannelService(entitlement_world["session"])
    with _captured_sql() as statements:
        await svc.list_visible_for_subscriber(entitlement_world["subscriber"].id)
    selects = [s for s in statements if s.lstrip().upper().startswith("SELECT")]
    assert len(selects) == 1, selects


# ── End-to-end through the HTTP endpoint ─────────────────────────────────────

def _app(world, redis):
    from app.main import app
    from app.database import get_db
    from app.redis_client import get_redis
    from app.core.dependencies import get_current_subscriber

    app.dependency_overrides[get_db] = lambda: world["session"]
    app.dependency_overrides[get_redis] = lambda: redis
    app.dependency_overrides[get_current_subscriber] = lambda: world["subscriber"]
    return app


async def _get_channels(world, redis) -> list[str]:
    app = _app(world, redis)
    try:
        async with httpx.AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as c:
            r = await c.get("/api/client/catalog/channels")
            assert r.status_code == 200, r.text
            return [item["channel_key"] for item in r.json()]
    finally:
        app.dependency_overrides.clear()


async def test_endpoint_returns_full_catalog_when_flag_off(entitlement_world, redis_client, monkeypatch):
    monkeypatch.setattr(cs_mod.settings, "catalog_entitlement_filter", False)
    assert await _get_channels(entitlement_world, redis_client) == ["canal-1", "canal-99"]


async def test_endpoint_returns_only_plan_channels_when_flag_on(entitlement_world, redis_client, monkeypatch):
    monkeypatch.setattr(cs_mod.settings, "catalog_entitlement_filter", True)
    assert await _get_channels(entitlement_world, redis_client) == ["canal-1"]


async def test_endpoint_response_shape_is_unchanged(entitlement_world, redis_client, monkeypatch):
    """The filter is a row filter, never a schema change — ChannelPublic intact."""
    monkeypatch.setattr(cs_mod.settings, "catalog_entitlement_filter", True)
    app = _app(entitlement_world, redis_client)
    try:
        async with httpx.AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as c:
            r = await c.get("/api/client/catalog/channels")
            assert r.status_code == 200, r.text
            item = r.json()[0]
    finally:
        app.dependency_overrides.clear()

    assert set(item) == {
        "id", "channel_key", "number", "name",
        "category", "logo_url", "requires_subscription",
    }
    assert "stream_key" not in item  # never leaked to clients
