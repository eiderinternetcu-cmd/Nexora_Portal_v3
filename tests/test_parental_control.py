"""NX-PARENTAL — server-side parental control (channels.censored + PIN).

The acceptance criterion is "adult channel without a PIN → 403", with the PIN
validated on the SERVER. These tests pin that, and the four decisions around it:

  * the control lives at the routes that MINT a playback token (/authorize AND
    the reissue), not in the client and not in the catalog;
  * a 4-6 digit secret is protected by an attempt LIMITER, not by its hash, so a
    wrong PIN has to count and a run of them has to block;
  * a subscriber with NO PIN configured is BLOCKED, not waved through;
  * a censored channel is LISTED (marked), and listing it leaks nothing playable.

Everything sits behind PARENTAL_CONTROL_ENFORCE, whose default must leave the
catalog payload and the playback path byte-for-byte as they are today.
"""
import httpx
import pytest
import pytest_asyncio
from httpx import ASGITransport
from sqlalchemy import func, select

from app.config import get_settings
from app.models.session import Session
from app.redis_client import key_login_attempts, key_lockout, key_parental_unlock
from app.services import parental_service as ps_mod

pytestmark = pytest.mark.asyncio

settings = get_settings()

PIN = "4821"
OTHER_PIN = "1111"
ADULT = "canal-x"
NORMAL = "canal-1"
DEVICE = "dev-web-01"
DEVICE_2 = "dev-web-02"


# ── Fixtures ─────────────────────────────────────────────────────────────────


@pytest_asyncio.fixture
async def parental_world(entitlement_world):
    """entitlement_world + a censored channel in the plan + two real devices.

    The adult channel is deliberately INSIDE the plan: this suite must fail on
    the parental gate, never on entitlement, or a green run would prove nothing.
    The device ids satisfy the endpoints' min_length=6 (the fixture's 'dev-1'
    does not).
    """
    from app.models.channel import Channel
    from app.models.device import Device
    from app.models.plan_channel import PlanChannel

    session = entitlement_world["session"]
    adult = Channel(
        channel_key=ADULT, number=50, name="ADULT", stream_key="KX",
        is_active=True, censored=True,
    )
    session.add(adult)
    await session.flush()
    session.add(
        PlanChannel(plan_id=entitlement_world["plan"].id, channel_id=adult.id, is_enabled=True)
    )
    devices = [
        Device(subscriber_id=entitlement_world["subscriber"].id, device_id=d,
               device_type="web_player", is_blocked=False)
        for d in (DEVICE, DEVICE_2)
    ]
    session.add_all(devices)
    await session.commit()

    entitlement_world["ch_adult"] = adult
    return entitlement_world


@pytest.fixture(autouse=True)
def _no_rate_limit(monkeypatch):
    """Take RateLimitMiddleware out of the way for this file.

    Not a workaround for a bug in the code under test — it is orthogonal
    infrastructure that would make these tests lie. This suite drives the real
    HTTP surface (that is the point: the gate has to be AT the endpoint), and it
    sends far more requests than any other file: ~25 to
    /api/client/playback/authorize, which is capped at 20 per 60s per IP.

    Worse, the middleware calls app.redis_client.get_redis() directly, so its
    bucket lives in the CONFIGURED Redis rather than in the per-test one the
    fixtures flush — the counter therefore survives the test, the file, and the
    pytest process, and two runs a minute apart would fail differently from one.
    A 429 here would say nothing about parental control and everything about how
    recently the file was last run.
    """
    from app.middleware import rate_limit as rl_mod

    monkeypatch.setattr(rl_mod, "RATE_LIMITS", {})
    monkeypatch.setattr(rl_mod.settings, "rate_limit_per_minute", 10_000_000)


@pytest.fixture
def enforced(monkeypatch):
    """PARENTAL_CONTROL_ENFORCE on.

    get_settings() is @lru_cache'd, so every module's `settings` is the SAME
    object — patching it here reaches the gate, the catalog route and the
    service at once.
    """
    monkeypatch.setattr(ps_mod.settings, "parental_control_enforce", True)


@pytest.fixture
def not_enforced(monkeypatch):
    monkeypatch.setattr(ps_mod.settings, "parental_control_enforce", False)


# ── HTTP helpers ─────────────────────────────────────────────────────────────


def _app(world, redis):
    from app.main import app
    from app.core.dependencies import get_current_subscriber
    from app.database import get_db
    from app.redis_client import get_redis

    app.dependency_overrides[get_db] = lambda: world["session"]
    app.dependency_overrides[get_redis] = lambda: redis
    app.dependency_overrides[get_current_subscriber] = lambda: world["subscriber"]
    return app


async def _call(world, redis, method: str, url: str, **kwargs) -> httpx.Response:
    app = _app(world, redis)
    try:
        async with httpx.AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as c:
            return await c.request(method, url, **kwargs)
    finally:
        app.dependency_overrides.clear()


async def _authorize(world, redis, channel_key=ADULT, device_id=DEVICE):
    return await _call(
        world, redis, "POST", "/api/client/playback/authorize",
        json={"device_id": device_id, "channel_id": channel_key},
    )


async def _reissue(world, redis, channel_key=ADULT, device_id=DEVICE):
    return await _call(
        world, redis, "GET", f"/api/client/playback/{channel_key}",
        params={"device_id": device_id},
    )


async def _set_pin(world, redis, new_pin=PIN, current_pin=None):
    body: dict = {"new_pin": new_pin}
    if current_pin is not None:
        body["current_pin"] = current_pin
    return await _call(world, redis, "PUT", "/api/client/parental/pin", json=body)


async def _unlock(world, redis, pin=PIN, device_id=DEVICE):
    return await _call(
        world, redis, "POST", "/api/client/parental/unlock",
        json={"device_id": device_id, "pin": pin},
    )


async def _catalog(world, redis):
    r = await _call(world, redis, "GET", "/api/client/catalog/channels")
    assert r.status_code == 200, r.text
    return r.json()


async def _configure_pin(world, redis, pin=PIN):
    r = await _set_pin(world, redis, new_pin=pin)
    assert r.status_code == 200, r.text


def _attempts_key(world) -> str:
    return key_login_attempts(f"parental:{world['subscriber'].id}")


def _lockout_key(world) -> str:
    return key_lockout(f"parental:{world['subscriber'].id}")


async def _session_count(world) -> int:
    return (
        await world["session"].execute(select(func.count()).select_from(Session))
    ).scalar()


# ── The acceptance criterion ─────────────────────────────────────────────────


async def test_censored_channel_without_a_configured_pin_is_denied(
    parental_world, redis_client, enforced
):
    """The AC, in its harshest form: no PIN exists, so there is nothing to type.

    Blocking here is the deliberate choice — allowing would mean the default
    state of every account is "parental control off".
    """
    r = await _authorize(parental_world, redis_client)
    assert r.status_code == 403, r.text
    assert r.json()["reason_code"] == "PARENTAL_PIN_NOT_SET"


async def test_censored_channel_with_a_pin_but_no_unlock_is_denied(
    parental_world, redis_client, enforced
):
    await _configure_pin(parental_world, redis_client)
    r = await _authorize(parental_world, redis_client)
    assert r.status_code == 403, r.text
    assert r.json()["reason_code"] == "PARENTAL_PIN_REQUIRED"


async def test_censored_channel_plays_after_the_correct_pin(
    parental_world, redis_client, enforced
):
    await _configure_pin(parental_world, redis_client)
    unlocked = await _unlock(parental_world, redis_client, pin=PIN)
    assert unlocked.status_code == 200, unlocked.text
    assert unlocked.json()["expires_in"] == settings.parental_pin_unlock_ttl_seconds

    r = await _authorize(parental_world, redis_client)
    assert r.status_code == 200, r.text
    assert r.json()["token"]
    assert r.json()["channel_id"] == ADULT


async def test_denial_mints_no_token_and_creates_no_session(
    parental_world, redis_client, enforced
):
    """The gate runs BEFORE StreamAuthService, so a refusal leaves nothing
    behind — no IPTV session, no playback token, no connection slot."""
    await _configure_pin(parental_world, redis_client)
    r = await _authorize(parental_world, redis_client)
    assert r.status_code == 403

    assert await _session_count(parental_world) == 0
    assert await redis_client.keys("nexora:playback:*") == []
    assert await redis_client.keys("nexora:active_conns:*") == []


# ── A 4-6 digit secret needs an attempt limiter, not just a hash ─────────────


async def test_wrong_pin_is_denied_and_counts_toward_the_limit(
    parental_world, redis_client, enforced
):
    await _configure_pin(parental_world, redis_client)

    r = await _unlock(parental_world, redis_client, pin=OTHER_PIN)
    assert r.status_code == 403, r.text
    assert r.json()["reason_code"] == "PARENTAL_PIN_INVALID"
    assert await redis_client.get(_attempts_key(parental_world)) == "1"

    # ...and it did NOT open the channel.
    assert (await _authorize(parental_world, redis_client)).status_code == 403


async def test_the_attempt_window_is_anchored_on_the_first_failure(
    parental_world, redis_client, enforced
):
    """The TTL must be set once, on the first failure, not refreshed on every
    one — otherwise a slow drip of guesses accumulates into a lockout and the
    documented contract ("N failures within WINDOW") stops being true."""
    await _configure_pin(parental_world, redis_client)
    await _unlock(parental_world, redis_client, pin=OTHER_PIN)
    first_ttl = await redis_client.ttl(_attempts_key(parental_world))
    await redis_client.expire(_attempts_key(parental_world), first_ttl - 60)

    await _unlock(parental_world, redis_client, pin=OTHER_PIN)
    assert await redis_client.get(_attempts_key(parental_world)) == "2"
    assert await redis_client.ttl(_attempts_key(parental_world)) <= first_ttl - 59


async def test_repeated_wrong_pins_block_pin_entry(parental_world, redis_client, enforced):
    """The control that actually protects a 10^4 keyspace: after N failures the
    door closes, and it closes for the CORRECT PIN too."""
    await _configure_pin(parental_world, redis_client)

    for _ in range(settings.parental_pin_max_attempts):
        r = await _unlock(parental_world, redis_client, pin=OTHER_PIN)
        assert r.status_code in (403, 423), r.text

    blocked = await _unlock(parental_world, redis_client, pin=PIN)
    assert blocked.status_code == 423, blocked.text
    assert blocked.json()["reason_code"] == "PARENTAL_PIN_LOCKED"
    assert (await _authorize(parental_world, redis_client)).status_code == 403


async def test_arming_the_block_drops_the_counter(parental_world, redis_client, enforced):
    """So that hammering while blocked cannot keep extending the block."""
    await _configure_pin(parental_world, redis_client)
    for _ in range(settings.parental_pin_max_attempts):
        await _unlock(parental_world, redis_client, pin=OTHER_PIN)

    assert await redis_client.exists(_lockout_key(parental_world))
    assert not await redis_client.exists(_attempts_key(parental_world))

    ttl_before = await redis_client.ttl(_lockout_key(parental_world))
    for _ in range(3):
        await _unlock(parental_world, redis_client, pin=OTHER_PIN)
    assert await redis_client.ttl(_lockout_key(parental_world)) <= ttl_before


async def test_a_correct_pin_clears_the_failure_run(parental_world, redis_client, enforced):
    await _configure_pin(parental_world, redis_client)
    await _unlock(parental_world, redis_client, pin=OTHER_PIN)
    await _unlock(parental_world, redis_client, pin=OTHER_PIN)
    assert await redis_client.get(_attempts_key(parental_world)) == "2"

    assert (await _unlock(parental_world, redis_client, pin=PIN)).status_code == 200
    assert not await redis_client.exists(_attempts_key(parental_world))


async def test_attempts_are_counted_per_subscriber_not_per_device(
    parental_world, redis_client, enforced
):
    """Rotating the device id must not hand out a fresh budget of guesses."""
    await _configure_pin(parental_world, redis_client)
    await _unlock(parental_world, redis_client, pin=OTHER_PIN, device_id=DEVICE)
    await _unlock(parental_world, redis_client, pin=OTHER_PIN, device_id=DEVICE_2)
    assert await redis_client.get(_attempts_key(parental_world)) == "2"


async def test_the_pin_is_stored_hashed_and_never_echoed(parental_world, redis_client):
    from app.core.security import verify_password

    responses = [await _set_pin(parental_world, redis_client, new_pin=PIN)]
    responses.append(await _unlock(parental_world, redis_client, pin=PIN))
    responses.append(await _call(parental_world, redis_client, "GET", "/api/client/parental"))

    stored = parental_world["subscriber"].parental_pin_hash
    assert stored and stored != PIN
    assert stored.startswith("$argon2")
    assert verify_password(PIN, stored)

    for r in responses:
        assert PIN not in r.text, r.text


@pytest.mark.parametrize("bad", ["", "123", "1234567", "abcd", "12 34", "12a4"])
async def test_malformed_pins_are_rejected_before_any_verification(
    parental_world, redis_client, bad
):
    """A value that cannot be a PIN is refused at the schema (422), so it never
    reaches an Argon2id verification and never consumes an attempt."""
    await _configure_pin(parental_world, redis_client)
    r = await _unlock(parental_world, redis_client, pin=bad)
    assert r.status_code == 422, r.text
    assert not await redis_client.exists(_attempts_key(parental_world))


# ── Setting and changing the PIN ─────────────────────────────────────────────


async def test_first_pin_needs_no_current_pin(parental_world, redis_client):
    r = await _set_pin(parental_world, redis_client, new_pin=PIN)
    assert r.status_code == 200, r.text
    assert r.json()["pin_set"] is True


async def test_changing_the_pin_requires_the_current_one(parental_world, redis_client):
    await _configure_pin(parental_world, redis_client)

    missing = await _set_pin(parental_world, redis_client, new_pin="9999")
    assert missing.status_code == 403, missing.text
    assert missing.json()["reason_code"] == "PARENTAL_PIN_INVALID"

    wrong = await _set_pin(parental_world, redis_client, new_pin="9999", current_pin=OTHER_PIN)
    assert wrong.status_code == 403, wrong.text

    ok = await _set_pin(parental_world, redis_client, new_pin="9999", current_pin=PIN)
    assert ok.status_code == 200, ok.text

    from app.core.security import verify_password
    assert verify_password("9999", parental_world["subscriber"].parental_pin_hash)


async def test_a_wrong_current_pin_counts_toward_the_same_limiter(
    parental_world, redis_client
):
    """Otherwise the change endpoint is an unthrottled oracle for exactly the
    secret the unlock endpoint throttles."""
    await _configure_pin(parental_world, redis_client)
    await _set_pin(parental_world, redis_client, new_pin="9999", current_pin=OTHER_PIN)
    assert await redis_client.get(_attempts_key(parental_world)) == "1"

    for _ in range(settings.parental_pin_max_attempts - 1):
        await _set_pin(parental_world, redis_client, new_pin="9999", current_pin=OTHER_PIN)
    blocked = await _set_pin(parental_world, redis_client, new_pin="9999", current_pin=PIN)
    assert blocked.status_code == 423, blocked.text


async def test_changing_the_pin_revokes_existing_unlocks(
    parental_world, redis_client, enforced
):
    """Consent given under the old PIN does not survive it."""
    await _configure_pin(parental_world, redis_client)
    await _unlock(parental_world, redis_client, pin=PIN)
    assert (await _authorize(parental_world, redis_client)).status_code == 200

    ok = await _set_pin(parental_world, redis_client, new_pin="9999", current_pin=PIN)
    assert ok.status_code == 200, ok.text
    assert (await _authorize(parental_world, redis_client)).status_code == 403


async def test_status_reports_whether_a_pin_is_configured(parental_world, redis_client, enforced):
    before = await _call(parental_world, redis_client, "GET", "/api/client/parental")
    assert before.json() == {
        "enforced": True,
        "pin_set": False,
        "unlock_ttl_seconds": settings.parental_pin_unlock_ttl_seconds,
    }
    await _configure_pin(parental_world, redis_client)
    after = await _call(parental_world, redis_client, "GET", "/api/client/parental")
    assert after.json()["pin_set"] is True


# ── The unlock grant ─────────────────────────────────────────────────────────


async def test_unlock_is_scoped_to_one_device(parental_world, redis_client, enforced):
    """Unlocking on the parent's phone must not unlock the child's television."""
    await _configure_pin(parental_world, redis_client)
    assert (await _unlock(parental_world, redis_client, device_id=DEVICE)).status_code == 200

    assert (await _authorize(parental_world, redis_client, device_id=DEVICE)).status_code == 200
    denied = await _authorize(parental_world, redis_client, device_id=DEVICE_2)
    assert denied.status_code == 403, denied.text
    assert denied.json()["reason_code"] == "PARENTAL_PIN_REQUIRED"


async def test_the_unlock_window_slides_while_the_channel_is_watched(
    parental_world, redis_client, enforced
):
    """A player reissues a token every ~45s; a fixed window would cut an adult
    programme off mid-scene."""
    await _configure_pin(parental_world, redis_client)
    await _unlock(parental_world, redis_client)

    key = key_parental_unlock(str(parental_world["subscriber"].id), DEVICE)
    await redis_client.expire(key, 5)
    assert (await _authorize(parental_world, redis_client)).status_code == 200
    assert await redis_client.ttl(key) > 5


async def test_unlock_refuses_a_device_that_is_not_the_callers(
    parental_world, redis_client, enforced
):
    from app.models.device import Device
    from app.models.subscriber import Subscriber, SubscriberStatus

    session = parental_world["session"]
    stranger = Subscriber(username="t_other", status=SubscriberStatus.active, password_hash="x")
    session.add(stranger)
    await session.flush()
    session.add(
        Device(subscriber_id=stranger.id, device_id="dev-someone-else",
               device_type="web_player", is_blocked=False)
    )
    await session.commit()

    await _configure_pin(parental_world, redis_client)
    r = await _unlock(parental_world, redis_client, device_id="dev-someone-else")
    assert r.status_code == 403, r.text


async def test_unlock_without_a_configured_pin_says_so(parental_world, redis_client, enforced):
    r = await _unlock(parental_world, redis_client, pin=PIN)
    assert r.status_code == 403, r.text
    assert r.json()["reason_code"] == "PARENTAL_PIN_NOT_SET"


# ── The reissue route mints tokens too, so it is gated too ───────────────────


async def _open_session(world, redis, device_id=DEVICE):
    """A real IPTV session, which the reissue route requires."""
    from app.services.stream_auth_service import StreamAuthService

    svc = StreamAuthService(world["session"], redis)
    await svc.authorize(
        subscriber_id=world["subscriber"].id,
        device_id_str=device_id,
        channel_id="K1",
        channel_key=NORMAL,
        node="ec-main",
    )
    await world["session"].commit()


async def test_reissue_is_gated_like_authorize(parental_world, redis_client, enforced):
    """Otherwise the adult tier is one path parameter away for any client that
    already holds an open session."""
    await _configure_pin(parental_world, redis_client)
    await _open_session(parental_world, redis_client)

    r = await _reissue(parental_world, redis_client, channel_key=ADULT)
    assert r.status_code == 403, r.text
    assert r.json()["reason_code"] == "PARENTAL_PIN_REQUIRED"


async def test_reissue_allowed_after_unlock(parental_world, redis_client, enforced):
    await _configure_pin(parental_world, redis_client)
    await _open_session(parental_world, redis_client)
    await _unlock(parental_world, redis_client)

    r = await _reissue(parental_world, redis_client, channel_key=ADULT)
    assert r.status_code == 200, r.text
    assert r.json()["token"]


async def test_reissue_of_a_normal_channel_is_untouched(parental_world, redis_client, enforced):
    await _open_session(parental_world, redis_client)
    r = await _reissue(parental_world, redis_client, channel_key=NORMAL)
    assert r.status_code == 200, r.text


# ── A normal channel is never affected ───────────────────────────────────────


async def test_normal_channel_plays_with_no_pin_configured(
    parental_world, redis_client, enforced
):
    r = await _authorize(parental_world, redis_client, channel_key=NORMAL)
    assert r.status_code == 200, r.text
    assert r.json()["token"]


async def test_normal_channel_plays_while_pin_entry_is_blocked(
    parental_world, redis_client, enforced
):
    """A parental lockout must not take the whole household off the air."""
    await _configure_pin(parental_world, redis_client)
    for _ in range(settings.parental_pin_max_attempts):
        await _unlock(parental_world, redis_client, pin=OTHER_PIN)

    r = await _authorize(parental_world, redis_client, channel_key=NORMAL)
    assert r.status_code == 200, r.text


# ── Flag OFF changes nothing ─────────────────────────────────────────────────


async def test_flag_defaults_to_off(monkeypatch):
    """Deploying this branch must not change production behaviour."""
    monkeypatch.delenv("PARENTAL_CONTROL_ENFORCE", raising=False)
    from app.config import Settings
    assert Settings(_env_file=None).parental_control_enforce is False


async def test_flag_off_plays_a_censored_channel_with_no_pin_at_all(
    parental_world, redis_client, not_enforced
):
    r = await _authorize(parental_world, redis_client, channel_key=ADULT)
    assert r.status_code == 200, r.text
    assert r.json()["token"]


async def test_flag_off_leaves_the_reissue_untouched(
    parental_world, redis_client, not_enforced
):
    await _open_session(parental_world, redis_client)
    r = await _reissue(parental_world, redis_client, channel_key=ADULT)
    assert r.status_code == 200, r.text


async def test_flag_off_keeps_the_catalog_payload_identical(
    parental_world, redis_client, not_enforced
):
    """A new JSON key is a behaviour change, however additive it looks."""
    items = await _catalog(parental_world, redis_client)
    assert items
    for item in items:
        assert set(item) == {
            "id", "channel_key", "number", "name",
            "category", "logo_url", "requires_subscription",
        }


# ── The catalog: listed and marked, never hidden, never playable ─────────────


async def test_censored_channel_is_listed_and_marked(parental_world, redis_client, enforced):
    items = await _catalog(parental_world, redis_client)
    by_key = {item["channel_key"]: item for item in items}

    assert ADULT in by_key, "hiding the row is not a control, and it breaks the grid"
    assert by_key[ADULT]["censored"] is True
    assert by_key[NORMAL]["censored"] is False


async def test_the_marked_catalog_leaks_nothing_playable(
    parental_world, redis_client, enforced
):
    """The marker adds a boolean and nothing else: the two values that
    reconstruct a direct URL stay absent, and channel_key alone plays nothing —
    the only route that turns it into a token is the gated one."""
    parental_world["ch_adult"].source_url = "http://u:p@1.2.3.4:8002/KX/index.m3u8"
    await parental_world["session"].commit()

    items = await _catalog(parental_world, redis_client)
    adult = next(item for item in items if item["channel_key"] == ADULT)

    assert set(adult) == {
        "id", "channel_key", "number", "name",
        "category", "logo_url", "requires_subscription", "censored",
    }
    body = (await _call(parental_world, redis_client, "GET", "/api/client/catalog/channels")).text
    assert "KX" not in body
    assert "1.2.3.4" not in body

    # And the listing did not become a way in: still 403 without the PIN.
    assert (await _authorize(parental_world, redis_client)).status_code == 403
