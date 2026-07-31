"""P0.8 — stream_key / source_url must never ride along in an admin listing.

The frontend already avoided PAINTING them, but GET /api/admin/channels put both
in the response body for all 41 channels, for admins AND resellers: one devtools
tab away. `source_url` carries the origin's user:password on pull sources, and
`stream_key` plus the Flussonic host is a playable URL that never touches
EntitlementService.

What is pinned here:
  * no listing (list, detail, stream-status, flussonic) returns either value,
    for either role;
  * the mask is not reversible — no host, no path, no credentials;
  * /secrets gives the real values to an admin and 403 (not 404) to a reseller;
  * every reveal lands in audit_logs, and the secret itself does NOT (the audit
    trail is readable by resellers via GET /api/admin/audit, so a secret in
    `details` would just reopen the hole one door down).
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
from app.models.user import User, UserRole
from app.schemas.channel import mask_source_url, mask_stream_key

# asyncio_mode = auto (pytest.ini) — no module-level asyncio mark, or the pure
# mask tests below get flagged as "marked asyncio but not async".

# A worst-case row: credentials embedded AND the stream_key sitting in the path,
# which is the exact shape scripts/backfill_channel_source_urls_same_origin.py
# was written to rewrite. The host is deliberately a private-range address that
# no environment can have configured: GET /api/admin/flussonic/health returns the
# REAL configured origin host:port by design (no key, so not exploitable), and if
# the fixture reused it the sweep below would trip over that instead of over an
# actual leak of this channel's stored source_url.
DIRTY_SOURCE_URL = "http://provider:s3cr3t@10.99.0.7:8002/GOLDEN_PLUS/index.m3u8"
DIRTY_STREAM_KEY = "GOLDEN_PLUS"


@asynccontextmanager
async def _client(session, redis, user: User | None = None):
    """Real app; `user` (None → unauthenticated) injected at get_current_user so
    require_admin / require_admin_or_reseller still run for real."""
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
    admin = User(username="cs_admin", email="cs_admin@test.local",
                 password_hash="x", role=UserRole.admin, is_active=True)
    reseller = User(username="cs_reseller", email="cs_reseller@test.local",
                    password_hash="x", role=UserRole.reseller, is_active=True)
    db_session.add_all([admin, reseller])

    dirty = Channel(channel_key="ch-dirty", number=1, name="Dirty",
                    stream_key=DIRTY_STREAM_KEY, source_url=DIRTY_SOURCE_URL,
                    is_active=True)
    relative = Channel(channel_key="ch-rel", number=2, name="Relative",
                       stream_key="K2",
                       source_url="/stream/co-main/K2/index.m3u8", is_active=True)
    bare = Channel(channel_key="ch-bare", number=3, name="Bare",
                   stream_key="K3", source_url=None, is_active=True)
    db_session.add_all([dirty, relative, bare])
    await db_session.commit()

    return {"session": db_session, "admin": admin, "reseller": reseller,
            "dirty": dirty, "relative": relative, "bare": bare}


def _leaks(payload: str) -> list[str]:
    """Secrets that must not appear anywhere in a serialized response."""
    return [s for s in (DIRTY_STREAM_KEY, "s3cr3t", "provider", "10.99.0.7",
                        "/stream/co-main/K2/index.m3u8")
            if s in payload]


# ── the mask itself (pure) ───────────────────────────────────────────────────

def test_mask_stream_key_hides_the_value_but_not_an_empty_one():
    assert mask_stream_key("GOLDEN_PLUS") == "***"
    assert mask_stream_key("") == ""       # empty stream_key is a real bug, stays visible
    assert mask_stream_key(None) == ""


def test_mask_source_url_drops_credentials_host_and_path():
    masked = mask_source_url(DIRTY_SOURCE_URL)
    assert masked == "http://***:***@***/***"
    for secret in ("s3cr3t", "provider", "10.99.0.7", "8002", DIRTY_STREAM_KEY):
        assert secret not in masked


def test_mask_source_url_keeps_only_the_shape():
    # relative same-origin — the safe configuration, still recognisable as such
    assert mask_source_url("/stream/co-main/K1/index.m3u8") == "/***"
    # absolute, TLS, no credentials
    assert mask_source_url("https://nexoraplay.net/stream/co-main/K1/x.m3u8") == "https://***/***"
    # http vs https survives, which is what the offline auditor flags
    assert mask_source_url("http://nexoraplay.net/stream/x").startswith("http://")
    # absence is not a secret
    assert mask_source_url(None) is None
    assert mask_source_url("") == ""


def test_mask_source_url_never_raises_on_garbage():
    """A mask must never be the reason a listing 500s."""
    for garbage in ("http://[oops", "::::", "not a url at all", "%%%"):
        assert isinstance(mask_source_url(garbage), str)


# ── listings ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("role", ["admin", "reseller"])
async def test_list_never_carries_the_raw_values(world, redis_client, role):
    async with _client(world["session"], redis_client, world[role]) as c:
        r = await c.get("/api/admin/channels")
    assert r.status_code == 200, r.text
    assert _leaks(r.text) == []

    rows = {row["channel_key"]: row for row in r.json()}
    assert "stream_key" not in rows["ch-dirty"]
    assert "source_url" not in rows["ch-dirty"]
    assert rows["ch-dirty"]["stream_key_masked"] == "***"
    assert rows["ch-dirty"]["source_url_masked"] == "http://***:***@***/***"
    assert rows["ch-rel"]["source_url_masked"] == "/***"
    assert rows["ch-bare"]["source_url_masked"] is None


@pytest.mark.parametrize("role", ["admin", "reseller"])
async def test_detail_is_masked_exactly_like_the_list(world, redis_client, role):
    """Plugging the listing and leaving the detail open would just move the hole."""
    async with _client(world["session"], redis_client, world[role]) as c:
        r = await c.get(f"/api/admin/channels/{world['dirty'].id}")
    assert r.status_code == 200, r.text
    assert _leaks(r.text) == []
    assert r.json()["stream_key_masked"] == "***"


async def test_stream_status_does_not_echo_the_stream_key(world, redis_client):
    """This route used to return the stream_key, making it a per-channel oracle
    for the value the listing masks. 503/404 are fine — the point is the body."""
    async with _client(world["session"], redis_client, world["reseller"]) as c:
        r = await c.get(f"/api/admin/channels/{world['dirty'].id}/stream-status")
    assert _leaks(r.text) == []


async def test_no_admin_route_leaks_the_secrets_to_a_reseller(world, redis_client):
    """Sweep, not memory: every GET the panel can reach as a reseller."""
    routes = [
        "/api/admin/channels",
        f"/api/admin/channels/{world['dirty'].id}",
        f"/api/admin/channels/{world['dirty'].id}/stream-status",
        "/api/admin/flussonic/health",
        "/api/admin/flussonic/streams",
        f"/api/admin/flussonic/streams/{DIRTY_STREAM_KEY}",
        "/api/admin/audit",
    ]
    async with _client(world["session"], redis_client, world["reseller"]) as c:
        for route in routes:
            r = await c.get(route)
            assert _leaks(r.text) == [], f"{route} leaked {_leaks(r.text)}"


async def test_flussonic_stream_routes_are_admin_only(world, redis_client):
    """`name` IS the stream_key namespace and `hls_url` is a ready-made playable
    URL — the whole catalog at once, without even needing the channel mapping."""
    async with _client(world["session"], redis_client, world["reseller"]) as c:
        listado = await c.get("/api/admin/flussonic/streams")
        uno = await c.get(f"/api/admin/flussonic/streams/{DIRTY_STREAM_KEY}")
    assert [listado.status_code, uno.status_code] == [403, 403]

    # /health stays open to resellers on purpose. It returns configured /
    # reachable / base_url_host — and host:port ALONE builds nothing: the URL
    # that bypasses the gate needs host + stream_key, and no route hands a
    # reseller a stream_key any more. Knowing which origin the API points at is
    # exactly what makes the panel useful when a channel is dead.
    async with _client(world["session"], redis_client, world["reseller"]) as c:
        assert (await c.get("/api/admin/flussonic/health")).status_code == 200


# ── the reveal endpoint ──────────────────────────────────────────────────────

def _url(world, key: str = "dirty") -> str:
    return f"/api/admin/channels/{world[key].id}/secrets"


async def test_admin_gets_the_real_values(world, redis_client):
    async with _client(world["session"], redis_client, world["admin"]) as c:
        r = await c.get(_url(world))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["stream_key"] == DIRTY_STREAM_KEY
    assert body["source_url"] == DIRTY_SOURCE_URL
    assert body["channel_key"] == "ch-dirty"
    assert body["channel_id"] == str(world["dirty"].id)
    assert r.headers.get("cache-control") == "no-store"


async def test_null_source_url_comes_back_as_null(world, redis_client):
    async with _client(world["session"], redis_client, world["admin"]) as c:
        r = await c.get(_url(world, "bare"))
    assert r.status_code == 200, r.text
    assert r.json()["source_url"] is None


async def test_reseller_gets_403_not_404(world, redis_client):
    """403, deliberately. The channel is in the listing the reseller just read,
    so a 404 would be a lie that conceals nothing — only the privilege differs."""
    async with _client(world["session"], redis_client, world["reseller"]) as c:
        r = await c.get(_url(world))
    assert r.status_code == 403, r.text
    assert _leaks(r.text) == []


async def test_unauthenticated_gets_401(world, redis_client):
    async with _client(world["session"], redis_client, None) as c:
        r = await c.get(_url(world))
    assert r.status_code == 401


async def test_unknown_channel_is_404_for_admin(world, redis_client):
    async with _client(world["session"], redis_client, world["admin"]) as c:
        r = await c.get(f"/api/admin/channels/{uuid.uuid4()}/secrets")
    assert r.status_code == 404


# ── audit ────────────────────────────────────────────────────────────────────

async def _audit_rows(world) -> list[AuditLog]:
    return list((await world["session"].execute(
        select(AuditLog).order_by(AuditLog.created_at)
    )).scalars().all())


async def test_every_reveal_is_audited(world, redis_client):
    async with _client(world["session"], redis_client, world["admin"]) as c:
        assert (await c.get(_url(world))).status_code == 200
        assert (await c.get(_url(world, "relative"))).status_code == 200

    rows = await _audit_rows(world)
    assert [r.action for r in rows] == [
        "channel.secrets_reveal", "channel.secrets_reveal",
    ]
    assert {r.actor_username for r in rows} == {"cs_admin"}
    assert {r.target_type for r in rows} == {"channel"}
    assert [r.target_id for r in rows] == [
        str(world["dirty"].id), str(world["relative"].id),
    ]
    assert rows[0].details["channel_key"] == "ch-dirty"
    assert rows[0].details["has_source_url"] is True
    assert rows[0].created_at is not None


async def test_audit_entry_never_stores_the_secret(world, redis_client):
    """GET /api/admin/audit is readable by resellers. A secret in `details` would
    reopen the exact hole this task closes, one door down."""
    async with _client(world["session"], redis_client, world["admin"]) as c:
        await c.get(_url(world))

    rows = await _audit_rows(world)
    assert _leaks(str(rows[0].details)) == []

    async with _client(world["session"], redis_client, world["reseller"]) as c:
        r = await c.get("/api/admin/audit", params={"action": "channel.secrets_reveal"})
    assert r.status_code == 200, r.text
    assert len(r.json()) == 1
    assert _leaks(r.text) == []


async def test_a_denied_reveal_writes_nothing(world, redis_client):
    """A reseller's 403 must not forge an audit entry in their name."""
    async with _client(world["session"], redis_client, world["reseller"]) as c:
        assert (await c.get(_url(world))).status_code == 403
    assert await _audit_rows(world) == []


async def test_reading_the_masked_listing_is_not_audited(world, redis_client):
    """Otherwise the trail fills with noise and stops meaning anything."""
    async with _client(world["session"], redis_client, world["admin"]) as c:
        assert (await c.get("/api/admin/channels")).status_code == 200
        assert (await c.get(f"/api/admin/channels/{world['dirty'].id}")).status_code == 200
    assert await _audit_rows(world) == []
