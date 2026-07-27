"""Admin API for plan_channels — the plan ↔ channel whitelist.

plan_channels is a STRICT whitelist (EntitlementService: no enabled row → deny
CHANNEL_NOT_INCLUDED), so a plan without rows grants access to NOTHING. Until
these endpoints existed the table could only be seeded by hand. The decisive
test here is the last one: after a PUT, can_watch_channel must actually flip.
"""
import uuid
from contextlib import asynccontextmanager

import httpx
import pytest
import pytest_asyncio
from httpx import ASGITransport
from sqlalchemy import select

from app.models.audit import AuditLog
from app.models.channel import Channel
from app.models.plan import Plan
from app.models.plan_channel import PlanChannel
from app.models.user import User, UserRole

pytestmark = pytest.mark.asyncio


# ── fixtures / helpers ───────────────────────────────────────────────────────

@asynccontextmanager
async def _client(session, redis, user: User | None = None):
    """HTTP client against the real app. `user` (None → unauthenticated) is
    injected at get_current_user, so require_admin / require_admin_or_reseller
    still run for real and the role checks are exercised."""
    from app.main import app
    from app.database import get_db
    from app.redis_client import get_redis
    from app.core.dependencies import get_current_user

    app.dependency_overrides[get_db] = lambda: session
    app.dependency_overrides[get_redis] = lambda: redis
    if user is not None:
        app.dependency_overrides[get_current_user] = lambda: user
    try:
        async with httpx.AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            yield client
    finally:
        app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def world(db_session):
    """A plan including ONE of four catalog channels (one of them inactive),
    plus a second plan that must never be touched, and both roles."""
    plan = Plan(name="p_main", max_connections=2, max_devices=5,
                duration_days=30, is_active=True)
    other = Plan(name="p_other", max_connections=1, max_devices=1,
                 duration_days=30, is_active=True)
    admin = User(username="pc_admin", email="pc_admin@test.local",
                 password_hash="x", role=UserRole.admin, is_active=True)
    reseller = User(username="pc_reseller", email="pc_reseller@test.local",
                    password_hash="x", role=UserRole.reseller, is_active=True)
    db_session.add_all([plan, other, admin, reseller])
    await db_session.flush()

    channels = [
        Channel(channel_key=f"ch-{i}", number=i, name=f"Channel {i}",
                stream_key=f"K{i}", is_active=(i != 4))
        for i in (1, 2, 3, 4)
    ]
    db_session.add_all(channels)
    await db_session.flush()

    db_session.add(PlanChannel(plan_id=plan.id, channel_id=channels[0].id,
                               is_enabled=True))
    db_session.add(PlanChannel(plan_id=other.id, channel_id=channels[1].id,
                               is_enabled=True))
    await db_session.commit()

    return {
        "session": db_session, "plan": plan, "other_plan": other,
        "admin": admin, "reseller": reseller, "channels": channels,
    }


def _url(world, suffix: str = "") -> str:
    return f"/api/admin/plans/{world['plan'].id}/channels{suffix}"


async def _included(world, redis) -> list[str]:
    async with _client(world["session"], redis, world["admin"]) as c:
        r = await c.get(_url(world))
        assert r.status_code == 200, r.text
        return [item["channel_key"] for item in r.json()["data"]]


async def _enabled_ids(world) -> set[uuid.UUID]:
    rows = (await world["session"].execute(
        select(PlanChannel.channel_id).where(
            PlanChannel.plan_id == world["plan"].id,
            PlanChannel.is_enabled.is_(True),
        )
    )).scalars().all()
    return set(rows)


# ── GET ──────────────────────────────────────────────────────────────────────

async def test_list_returns_only_included_channels(world, redis_client):
    assert await _included(world, redis_client) == ["ch-1"]


async def test_list_of_empty_plan_is_empty(world, redis_client):
    world["plan"].id  # touch
    async with _client(world["session"], redis_client, world["admin"]) as c:
        r = await c.get(f"/api/admin/plans/{world['other_plan'].id}/channels")
        assert r.status_code == 200, r.text
        assert [i["channel_key"] for i in r.json()["data"]] == ["ch-2"]

    # and a plan with no rows at all → [] (not "everything")
    empty = Plan(name="p_empty", max_connections=1, max_devices=1,
                 duration_days=30, is_active=True)
    world["session"].add(empty)
    await world["session"].commit()
    async with _client(world["session"], redis_client, world["admin"]) as c:
        r = await c.get(f"/api/admin/plans/{empty.id}/channels")
        assert r.status_code == 200, r.text
        assert r.json()["data"] == []


async def test_include_excluded_returns_full_catalog_with_flags(world, redis_client):
    async with _client(world["session"], redis_client, world["admin"]) as c:
        r = await c.get(_url(world), params={"include_excluded": "true"})
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert [i["channel_key"] for i in data] == ["ch-1", "ch-2", "ch-3", "ch-4"]
    assert {i["channel_key"]: i["included"] for i in data} == {
        "ch-1": True, "ch-2": False, "ch-3": False, "ch-4": False,
    }
    # the inactive channel is listed (with its flag) so the panel can explain it
    assert {i["channel_key"]: i["is_active"] for i in data}["ch-4"] is False
    assert data[0]["id"] == str(world["channels"][0].id)  # channel id, not row id


async def test_disabled_row_is_not_included_in_either_view(world, redis_client):
    row = (await world["session"].execute(
        select(PlanChannel).where(PlanChannel.plan_id == world["plan"].id)
    )).scalar_one()
    row.is_enabled = False
    await world["session"].commit()

    assert await _included(world, redis_client) == []
    async with _client(world["session"], redis_client, world["admin"]) as c:
        r = await c.get(_url(world), params={"include_excluded": "true"})
    assert all(i["included"] is False for i in r.json()["data"])


# ── PUT (atomic replacement) ─────────────────────────────────────────────────

async def _put(world, redis, ids, user=None):
    async with _client(world["session"], redis, user or world["admin"]) as c:
        return await c.put(_url(world), json={"channel_ids": [str(i) for i in ids]})


async def test_put_adds_and_removes_in_one_call(world, redis_client):
    chans = world["channels"]
    r = await _put(world, redis_client, [chans[1].id, chans[2].id])
    assert r.status_code == 200, r.text
    body = r.json()["data"]
    assert (body["added"], body["removed"], body["total"]) == (2, 1, 2)
    assert await _included(world, redis_client) == ["ch-2", "ch-3"]
    # the other plan is untouched
    assert (await world["session"].execute(
        select(PlanChannel).where(PlanChannel.plan_id == world["other_plan"].id)
    )).scalars().all() != []


async def test_put_is_idempotent(world, redis_client):
    chans = world["channels"]
    await _put(world, redis_client, [chans[0].id, chans[1].id])
    r = await _put(world, redis_client, [chans[0].id, chans[1].id])
    assert r.status_code == 200, r.text
    assert r.json()["data"] == {
        "plan_id": str(world["plan"].id), "added": 0, "removed": 0, "total": 2,
    }
    assert await _included(world, redis_client) == ["ch-1", "ch-2"]
    assert len(await _enabled_ids(world)) == 2  # no duplicate rows


async def test_put_with_duplicate_ids_does_not_duplicate_rows(world, redis_client):
    cid = world["channels"][1].id
    r = await _put(world, redis_client, [cid, cid, cid])
    assert r.status_code == 200, r.text
    assert r.json()["data"]["total"] == 1
    assert await _enabled_ids(world) == {cid}


async def test_put_empty_list_clears_the_whitelist(world, redis_client):
    r = await _put(world, redis_client, [])
    assert r.status_code == 200, r.text
    assert (r.json()["data"]["removed"], r.json()["data"]["total"]) == (1, 0)
    assert await _included(world, redis_client) == []


async def test_put_include_all_active_channels(world, redis_client):
    """The one-click case the seed script used to cover."""
    active = [c.id for c in world["channels"] if c.is_active]
    r = await _put(world, redis_client, active)
    assert r.status_code == 200, r.text
    assert r.json()["data"]["total"] == 3
    assert await _included(world, redis_client) == ["ch-1", "ch-2", "ch-3"]


async def test_put_re_enables_a_soft_disabled_row(world, redis_client):
    """is_enabled=False is a soft exclusion; a PUT that includes the channel
    must make it watchable again, not silently leave it disabled."""
    row = (await world["session"].execute(
        select(PlanChannel).where(PlanChannel.plan_id == world["plan"].id)
    )).scalar_one()
    row.is_enabled = False
    await world["session"].commit()

    r = await _put(world, redis_client, [world["channels"][0].id])
    assert r.status_code == 200, r.text
    assert r.json()["data"]["added"] == 1
    assert await _included(world, redis_client) == ["ch-1"]


# ── POST / DELETE (single membership) ────────────────────────────────────────

async def test_post_adds_one_channel_and_is_idempotent(world, redis_client):
    cid = world["channels"][1].id
    async with _client(world["session"], redis_client, world["admin"]) as c:
        first = await c.post(_url(world, f"/{cid}"))
        second = await c.post(_url(world, f"/{cid}"))
    assert first.status_code == 200, first.text
    assert first.json()["data"]["added"] == 1
    assert second.status_code == 200, second.text
    assert second.json()["data"]["added"] == 0
    assert second.json()["data"]["total"] == 2
    assert await _included(world, redis_client) == ["ch-1", "ch-2"]


async def test_delete_removes_one_channel_and_is_idempotent(world, redis_client):
    cid = world["channels"][0].id
    async with _client(world["session"], redis_client, world["admin"]) as c:
        first = await c.delete(_url(world, f"/{cid}"))
        second = await c.delete(_url(world, f"/{cid}"))
    assert first.status_code == 200, first.text
    assert (first.json()["data"]["removed"], first.json()["data"]["total"]) == (1, 0)
    assert second.status_code == 200, second.text
    assert second.json()["data"]["removed"] == 0  # was not there → no error
    assert await _included(world, redis_client) == []


async def test_delete_channel_that_was_never_in_the_plan(world, redis_client):
    async with _client(world["session"], redis_client, world["admin"]) as c:
        r = await c.delete(_url(world, f"/{world['channels'][2].id}"))
    assert r.status_code == 200, r.text
    assert (r.json()["data"]["removed"], r.json()["data"]["total"]) == (0, 1)


# ── Validation ───────────────────────────────────────────────────────────────

async def test_unknown_plan_is_404_on_every_verb(world, redis_client):
    ghost = uuid.uuid4()
    cid = world["channels"][0].id
    base = f"/api/admin/plans/{ghost}/channels"
    async with _client(world["session"], redis_client, world["admin"]) as c:
        assert (await c.get(base)).status_code == 404
        assert (await c.put(base, json={"channel_ids": []})).status_code == 404
        assert (await c.post(f"{base}/{cid}")).status_code == 404
        assert (await c.delete(f"{base}/{cid}")).status_code == 404


async def test_put_with_unknown_channel_is_422_and_changes_nothing(world, redis_client):
    ghost = uuid.uuid4()
    before = await _enabled_ids(world)
    r = await _put(world, redis_client, [world["channels"][1].id, ghost])
    assert r.status_code == 422, r.text
    assert str(ghost) in r.json()["error"]        # clear, not an FK 500
    assert await _enabled_ids(world) == before    # nothing was applied


async def test_post_and_delete_with_unknown_channel_are_404(world, redis_client):
    ghost = uuid.uuid4()
    async with _client(world["session"], redis_client, world["admin"]) as c:
        assert (await c.post(_url(world, f"/{ghost}"))).status_code == 404
        assert (await c.delete(_url(world, f"/{ghost}"))).status_code == 404


async def test_invalid_body_is_422(world, redis_client):
    async with _client(world["session"], redis_client, world["admin"]) as c:
        assert (await c.put(_url(world), json={})).status_code == 422
        assert (await c.put(_url(world),
                            json={"channel_ids": "not-a-list"})).status_code == 422
        assert (await c.put(_url(world),
                            json={"channel_ids": ["nope"]})).status_code == 422
    assert await _included(world, redis_client) == ["ch-1"]


async def test_malformed_uuid_in_path_is_422(world, redis_client):
    async with _client(world["session"], redis_client, world["admin"]) as c:
        r = await c.get("/api/admin/plans/not-a-uuid/channels")
    assert r.status_code == 422


# ── Permissions ──────────────────────────────────────────────────────────────

async def test_reseller_can_read(world, redis_client):
    async with _client(world["session"], redis_client, world["reseller"]) as c:
        r = await c.get(_url(world))
    assert r.status_code == 200, r.text


async def test_reseller_cannot_write(world, redis_client):
    cid = world["channels"][1].id
    async with _client(world["session"], redis_client, world["reseller"]) as c:
        put = await c.put(_url(world), json={"channel_ids": [str(cid)]})
        post = await c.post(_url(world, f"/{cid}"))
        dele = await c.delete(_url(world, f"/{world['channels'][0].id}"))
    assert [put.status_code, post.status_code, dele.status_code] == [403, 403, 403]
    assert await _included(world, redis_client) == ["ch-1"]  # untouched


async def test_unauthenticated_can_do_nothing(world, redis_client):
    cid = world["channels"][0].id
    async with _client(world["session"], redis_client, None) as c:
        get = await c.get(_url(world))
        put = await c.put(_url(world), json={"channel_ids": []})
        post = await c.post(_url(world, f"/{cid}"))
        dele = await c.delete(_url(world, f"/{cid}"))
    assert [get.status_code, put.status_code, post.status_code, dele.status_code] \
        == [401, 401, 401, 401]
    assert await _enabled_ids(world) == {cid}


# ── Audit ────────────────────────────────────────────────────────────────────

async def test_every_write_is_audited(world, redis_client):
    chans = world["channels"]
    async with _client(world["session"], redis_client, world["admin"]) as c:
        await c.put(_url(world), json={"channel_ids": [str(chans[1].id)]})
        await c.post(_url(world, f"/{chans[2].id}"))
        await c.delete(_url(world, f"/{chans[1].id}"))

    rows = (await world["session"].execute(
        select(AuditLog).order_by(AuditLog.created_at)
    )).scalars().all()
    actions = [r.action for r in rows]
    assert actions == [
        "plan.channels_replace", "plan.channels_add", "plan.channels_remove",
    ]
    assert {r.target_type for r in rows} == {"plan"}
    assert {r.target_id for r in rows} == {str(world["plan"].id)}
    assert {r.actor_username for r in rows} == {"pc_admin"}
    assert rows[0].details["channel_ids"] == [str(chans[1].id)]
    assert rows[0].details["removed"] == 1
    assert rows[2].details["channel_id"] == str(chans[1].id)


async def test_reads_are_not_audited(world, redis_client):
    await _included(world, redis_client)
    rows = (await world["session"].execute(select(AuditLog))).scalars().all()
    assert rows == []


# ── The point of the whole feature ───────────────────────────────────────────

async def test_put_flips_the_entitlement_decision(entitlement_world, redis_client):
    """A PUT must change what EntitlementService answers for a subscriber of
    that plan — proof this replaces the manual seeding, not just that it 200s."""
    from app.services.entitlement_service import EntitlementService

    w = entitlement_world
    session = w["session"]
    admin = User(username="ent_admin", email="ent_admin@test.local",
                 password_hash="x", role=UserRole.admin, is_active=True)
    session.add(admin)
    await session.commit()

    ent = EntitlementService(session)

    async def decide(channel_key: str):
        return await ent.can_watch_channel(w["subscriber"].id,
                                           w["device"].device_id, channel_key)

    # before: canal-1 in the plan, canal-99 out
    assert (await decide("canal-1")).allow is True
    denied = await decide("canal-99")
    assert denied.allow is False and denied.code == "CHANNEL_NOT_INCLUDED"

    async with _client(session, redis_client, admin) as c:
        r = await c.put(f"/api/admin/plans/{w['plan'].id}/channels",
                        json={"channel_ids": [str(w["ch_out"].id)]})
    assert r.status_code == 200, r.text

    # after: exactly inverted
    assert (await decide("canal-99")).allow is True
    denied = await decide("canal-1")
    assert denied.allow is False and denied.code == "CHANNEL_NOT_INCLUDED"


async def test_post_and_delete_flip_the_entitlement_decision(entitlement_world, redis_client):
    from app.services.entitlement_service import EntitlementService

    w = entitlement_world
    session = w["session"]
    admin = User(username="ent_admin2", email="ent_admin2@test.local",
                 password_hash="x", role=UserRole.admin, is_active=True)
    session.add(admin)
    await session.commit()

    ent = EntitlementService(session)
    base = f"/api/admin/plans/{w['plan'].id}/channels"

    async with _client(session, redis_client, admin) as c:
        assert (await c.post(f"{base}/{w['ch_out'].id}")).status_code == 200
    assert (await ent.can_watch_channel(
        w["subscriber"].id, w["device"].device_id, "canal-99")).allow is True

    async with _client(session, redis_client, admin) as c:
        assert (await c.delete(f"{base}/{w['ch_out'].id}")).status_code == 200
    result = await ent.can_watch_channel(
        w["subscriber"].id, w["device"].device_id, "canal-99")
    assert result.allow is False and result.code == "CHANNEL_NOT_INCLUDED"
