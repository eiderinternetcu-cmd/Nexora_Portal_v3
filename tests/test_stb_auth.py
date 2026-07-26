"""STB surface hardening — /api/stb/* must authenticate the caller.

Before this, POST /api/stb/auth/play and POST /api/stb/auth/token had NO
authentication dependency at all and read `subscriber_id`/`device_id` straight
from the request body, while nginx published the whole prefix
(`location ^~ /api/` in deploy/nginx/nexoraplay.conf). Two distinct holes:

  1. IDOR — any caller could mint a playback token, open an IPTV session and
     burn a connection slot for ANY subscriber.
  2. Entitlement bypass — the route passed the body's public `channel_id` into
     StreamAuthService.authorize's positional `channel_id` (which is the
     internal stream_key), leaving `channel_key=None`. `_check_entitlement`
     returns early on a None channel_key, so EntitlementService was never
     consulted from this surface — ENTITLEMENT_ENFORCE=true did nothing here.

These tests pin both closed, plus the token bindings (chn/node/cip) that the
old call shape left as None.
"""
import time
import uuid

import httpx
import jwt
import pytest
import pytest_asyncio
from httpx import ASGITransport

from app.api.stb import deps as stb_deps
from app.config import get_settings
from app.core import security
from app.services import stream_auth_service as sas_mod

pytestmark = pytest.mark.asyncio
settings = get_settings()

PLAY = "/api/stb/auth/play"
TOKEN = "/api/stb/auth/token"
HEARTBEAT = "/api/stb/heartbeat"

STB_DEVICE_ID = "stb-device-01"


# ── fixtures / helpers ───────────────────────────────────────────────────────

@pytest_asyncio.fixture
async def stb_world(entitlement_world):
    """entitlement_world + a device whose device_id satisfies min_length=6.

    The shared fixture's "dev-1" is 5 chars, which the request schemas reject
    with a 422 before any auth logic runs — useless for testing 401/403.
    """
    from app.models.device import Device

    session = entitlement_world["session"]
    device = Device(
        subscriber_id=entitlement_world["subscriber"].id,
        device_id=STB_DEVICE_ID,
        device_type="stb",
        is_blocked=False,
    )
    session.add(device)
    await session.commit()
    entitlement_world["stb_device"] = device
    return entitlement_world


def _app(world, redis):
    from app.main import app
    from app.database import get_db
    from app.redis_client import get_redis

    app.dependency_overrides[get_db] = lambda: world["session"]
    app.dependency_overrides[get_redis] = lambda: redis
    return app


async def _post(world, redis, path: str, body: dict, token: str | None = None,
                client_ip: str | None = None) -> httpx.Response:
    """POST through the real ASGI stack (middleware + dependencies included).

    A per-call X-Forwarded-For keeps each test in its own rate-limit bucket
    (/api/stb/auth/play is capped at 20/min per IP) and makes the 'cip' claim
    deterministic.
    """
    app = _app(world, redis)
    headers = {"X-Forwarded-For": client_ip or f"10.0.0.{uuid.uuid4().int % 200 + 1}"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        async with httpx.AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as c:
            return await c.post(path, json=body, headers=headers)
    finally:
        app.dependency_overrides.clear()


def _stb_token(world, device_id: str = STB_DEVICE_ID) -> str:
    token, _, _ = security.create_stb_access_token(
        str(world["subscriber"].id), device_id
    )
    return token


def _raw(**claims) -> str:
    base = {"jti": str(uuid.uuid4()), "sub": str(uuid.uuid4()),
            "iat": int(time.time()), "exp": int(time.time()) + 3600}
    base.update(claims)
    return jwt.encode(base, settings.secret_key, algorithm=settings.jwt_algorithm)


def _claims_of(playback_token: str) -> dict:
    return jwt.decode(playback_token, settings.secret_key,
                      algorithms=[settings.jwt_algorithm],
                      options={"verify_aud": False})


# ── 1. no token → 401 ────────────────────────────────────────────────────────

async def test_play_without_token_is_401(stb_world, redis_client):
    r = await _post(stb_world, redis_client, PLAY,
                    {"subscriber_id": str(stb_world["subscriber"].id),
                     "device_id": STB_DEVICE_ID, "channel_id": "canal-1"})
    assert r.status_code == 401, r.text


async def test_reissue_without_token_is_401(stb_world, redis_client):
    r = await _post(stb_world, redis_client, TOKEN,
                    {"subscriber_id": str(stb_world["subscriber"].id),
                     "device_id": STB_DEVICE_ID, "channel_id": "canal-1"})
    assert r.status_code == 401, r.text


async def test_heartbeat_without_token_is_401(stb_world, redis_client):
    r = await _post(stb_world, redis_client, HEARTBEAT, {"device_id": STB_DEVICE_ID})
    assert r.status_code == 401, r.text


async def test_play_without_token_creates_nothing(stb_world, redis_client):
    """The 401 must land before any session/token/slot is created."""
    await _post(stb_world, redis_client, PLAY,
                {"subscriber_id": str(stb_world["subscriber"].id),
                 "device_id": STB_DEVICE_ID, "channel_id": "canal-1"})
    assert await redis_client.keys("nexora:playback:*") == []
    assert await redis_client.keys("nexora:active_conns:*") == []


# ── 2. token from another surface → 401 ──────────────────────────────────────

async def test_client_token_rejected_on_stb_play(stb_world, redis_client):
    token, _, _ = security.create_client_access_token(str(stb_world["subscriber"].id))
    r = await _post(stb_world, redis_client, PLAY, {"channel_id": "canal-1"}, token=token)
    assert r.status_code == 401, r.text


async def test_admin_token_rejected_on_stb_play(stb_world, redis_client):
    token, _, _ = security.create_access_token(str(uuid.uuid4()), "admin")
    r = await _post(stb_world, redis_client, PLAY, {"channel_id": "canal-1"}, token=token)
    assert r.status_code == 401, r.text


async def test_playback_token_rejected_on_stb_play(stb_world, redis_client):
    token = _raw(type=security.TYPE_PLAYBACK, aud=security.AUD_PLAYBACK,
                 iss=settings.jwt_issuer, dev=STB_DEVICE_ID)
    r = await _post(stb_world, redis_client, PLAY, {"channel_id": "canal-1"}, token=token)
    assert r.status_code == 401, r.text


async def test_client_token_rejected_on_stb_heartbeat(stb_world, redis_client):
    token, _, _ = security.create_client_access_token(str(stb_world["subscriber"].id))
    r = await _post(stb_world, redis_client, HEARTBEAT,
                    {"device_id": STB_DEVICE_ID}, token=token)
    assert r.status_code == 401, r.text


async def test_stb_token_without_device_claim_is_401(stb_world, redis_client):
    """A stb_access token with no `dev` claim cannot identify a device."""
    token = _raw(type=security.TYPE_STB_ACCESS, aud=security.AUD_STB,
                 iss=settings.jwt_issuer, sub=str(stb_world["subscriber"].id))
    r = await _post(stb_world, redis_client, PLAY, {"channel_id": "canal-1"}, token=token)
    assert r.status_code == 401, r.text


async def test_stb_token_rejected_on_client_surface(stb_world, redis_client, monkeypatch):
    """The new surface is not a skeleton key: stb_access is rejected elsewhere."""
    from fastapi.security import HTTPAuthorizationCredentials
    from app.core.dependencies import get_client_token_payload, _get_token_payload
    from app.core.exceptions import NexoraException

    creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials=_stb_token(stb_world))
    for dep in (get_client_token_payload, _get_token_payload):
        with pytest.raises(NexoraException) as ei:
            await dep(credentials=creds, redis=redis_client)
        assert ei.value.status_code == 401


# ── 3. valid token, someone else's identity in the body → 403 ────────────────

async def test_play_rejects_foreign_subscriber_id_in_body(stb_world, redis_client):
    r = await _post(stb_world, redis_client, PLAY,
                    {"subscriber_id": str(uuid.uuid4()), "channel_id": "canal-1"},
                    token=_stb_token(stb_world))
    assert r.status_code == 403, r.text
    assert "subscriber_id" in r.json()["error"]


async def test_play_rejects_foreign_device_id_in_body(stb_world, redis_client):
    r = await _post(stb_world, redis_client, PLAY,
                    {"device_id": "somebody-elses-device", "channel_id": "canal-1"},
                    token=_stb_token(stb_world))
    assert r.status_code == 403, r.text
    assert "device_id" in r.json()["error"]


async def test_reissue_rejects_foreign_subscriber_id_in_body(stb_world, redis_client):
    r = await _post(stb_world, redis_client, TOKEN,
                    {"subscriber_id": str(uuid.uuid4()), "channel_id": "canal-1"},
                    token=_stb_token(stb_world))
    assert r.status_code == 403, r.text


async def test_heartbeat_rejects_foreign_device_id_in_body(stb_world, redis_client):
    r = await _post(stb_world, redis_client, HEARTBEAT,
                    {"device_id": "somebody-elses-device"},
                    token=_stb_token(stb_world))
    assert r.status_code == 403, r.text


async def test_foreign_identity_creates_nothing(stb_world, redis_client):
    await _post(stb_world, redis_client, PLAY,
                {"subscriber_id": str(uuid.uuid4()), "channel_id": "canal-1"},
                token=_stb_token(stb_world))
    assert await redis_client.keys("nexora:playback:*") == []


# ── 4. entitlement gate — the IDOR/bypass regression test ────────────────────

async def test_play_denies_channel_outside_plan_when_enforced(stb_world, redis_client, monkeypatch):
    """canal-99 is NOT in the plan. Before the fix this returned 200 with a
    playback token even with ENTITLEMENT_ENFORCE=true, because channel_key was
    never passed through and EntitlementService was never consulted."""
    monkeypatch.setattr(sas_mod.settings, "entitlement_enforce", True)
    r = await _post(stb_world, redis_client, PLAY, {"channel_id": "canal-99"},
                    token=_stb_token(stb_world))
    assert r.status_code == 403, r.text
    assert r.json()["error"] == "CHANNEL_NOT_INCLUDED"
    assert await redis_client.keys("nexora:playback:*") == []


async def test_play_allows_channel_inside_plan_when_enforced(stb_world, redis_client, monkeypatch):
    monkeypatch.setattr(sas_mod.settings, "entitlement_enforce", True)
    r = await _post(stb_world, redis_client, PLAY, {"channel_id": "canal-1"},
                    token=_stb_token(stb_world))
    assert r.status_code == 200, r.text
    assert r.json()["data"]["token"]


async def test_entitlement_runs_even_with_auth_enforcement_off(stb_world, redis_client, monkeypatch):
    """The escape hatch reopens the identity hole, never the entitlement one."""
    monkeypatch.setattr(stb_deps.settings, "stb_auth_enforce", False)
    monkeypatch.setattr(sas_mod.settings, "entitlement_enforce", True)
    r = await _post(stb_world, redis_client, PLAY,
                    {"subscriber_id": str(stb_world["subscriber"].id),
                     "device_id": STB_DEVICE_ID, "channel_id": "canal-99"})
    assert r.status_code == 403, r.text
    assert r.json()["error"] == "CHANNEL_NOT_INCLUDED"


async def test_unknown_channel_key_is_404(stb_world, redis_client):
    r = await _post(stb_world, redis_client, PLAY, {"channel_id": "does-not-exist"},
                    token=_stb_token(stb_world))
    assert r.status_code == 404, r.text


# ── 5. token bindings: chn / node / cip must be populated ────────────────────

async def test_issued_token_carries_chn_node_and_cip(stb_world, redis_client):
    r = await _post(stb_world, redis_client, PLAY, {"channel_id": "canal-1"},
                    token=_stb_token(stb_world), client_ip="203.0.113.7")
    assert r.status_code == 200, r.text
    claims = _claims_of(r.json()["data"]["token"])

    assert claims["chn"] == "canal-1"                 # public key → entitlement/binding
    assert claims["sk"] == "K1"                       # internal stream_key
    assert claims["node"] == "ec-main"                # node binding no longer exempt
    assert claims["cip"] == security.hash_ip("203.0.113.7")   # IP binding no longer exempt
    assert claims["sub"] == str(stb_world["subscriber"].id)
    assert claims["dev"] == str(stb_world["stb_device"].id)


async def test_response_echoes_public_key_not_stream_key(stb_world, redis_client):
    r = await _post(stb_world, redis_client, PLAY, {"channel_id": "canal-1"},
                    token=_stb_token(stb_world))
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["channel_id"] == "canal-1"
    assert "K1" not in r.text          # stream_key must never reach the device


async def test_reissued_token_carries_the_same_bindings(stb_world, redis_client):
    token = _stb_token(stb_world)
    first = await _post(stb_world, redis_client, PLAY, {"channel_id": "canal-1"},
                        token=token, client_ip="203.0.113.9")
    assert first.status_code == 200, first.text

    second = await _post(stb_world, redis_client, TOKEN, {"channel_id": "canal-1"},
                         token=token, client_ip="203.0.113.9")
    assert second.status_code == 200, second.text
    claims = _claims_of(second.json()["data"]["token"])
    assert claims["chn"] == "canal-1"
    assert claims["sk"] == "K1"
    assert claims["node"] == "ec-main"
    assert claims["cip"] == security.hash_ip("203.0.113.9")


# ── 6. identity comes from the token, body is optional ───────────────────────

async def test_identity_is_taken_from_the_token_with_empty_body(stb_world, redis_client):
    r = await _post(stb_world, redis_client, PLAY, {"channel_id": "canal-1"},
                    token=_stb_token(stb_world))
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["subscriber_id"] == str(stb_world["subscriber"].id)
    assert data["device_id"] == str(stb_world["stb_device"].id)


async def test_heartbeat_with_matching_token_succeeds(stb_world, redis_client):
    r = await _post(stb_world, redis_client, HEARTBEAT, {"device_id": STB_DEVICE_ID},
                    token=_stb_token(stb_world))
    assert r.status_code == 200, r.text
    assert r.json()["data"]["device_id"] == STB_DEVICE_ID


async def test_stb_token_for_a_device_of_another_subscriber_is_403(stb_world, redis_client):
    """Token claims are self-consistent but the device is not this subscriber's —
    StreamAuthService still owns the ownership check."""
    from app.models.subscriber import Subscriber, SubscriberStatus
    from app.models.device import Device

    session = stb_world["session"]
    other = Subscriber(username="t_other", status=SubscriberStatus.active, password_hash="x")
    session.add(other)
    await session.flush()
    other_device = Device(subscriber_id=other.id, device_id="other-device-01",
                          device_type="stb", is_blocked=False)
    session.add(other_device)
    await session.commit()

    # attacker holds a token for their own subscriber but names the victim's device
    token, _, _ = security.create_stb_access_token(str(other.id), STB_DEVICE_ID)
    r = await _post(stb_world, redis_client, PLAY, {"channel_id": "canal-1"}, token=token)
    assert r.status_code == 403, r.text


# ── 7. the /api/v1 twin: same hole, same lock ────────────────────────────────
# DeviceService.heartbeat renews the connection ZSET slot and touches the IPTV
# session, so an unauthenticated caller could keep another subscriber's session
# alive and read their plan back in the response. Hardening only the /api/stb
# route left this one wide open behind the same `location ^~ /api/` in nginx,
# which made the STB fix bypassable rather than effective.

V1_HEARTBEAT = "/api/v1/devices/heartbeat"


async def test_v1_heartbeat_without_token_is_401(stb_world, redis_client):
    r = await _post(stb_world, redis_client, V1_HEARTBEAT, {"device_id": STB_DEVICE_ID})
    assert r.status_code == 401, r.text


async def test_v1_heartbeat_with_stb_token_works(stb_world, redis_client):
    r = await _post(stb_world, redis_client, V1_HEARTBEAT,
                    {"device_id": STB_DEVICE_ID}, token=_stb_token(stb_world))
    assert r.status_code == 200, r.text


async def test_v1_heartbeat_rejects_client_token(stb_world, redis_client):
    """A subscriber access token is not a skeleton key for the device surface."""
    token, _, _ = security.create_client_access_token(str(stb_world["subscriber"].id))
    r = await _post(stb_world, redis_client, V1_HEARTBEAT,
                    {"device_id": STB_DEVICE_ID}, token=token)
    assert r.status_code == 401, r.text


async def test_v1_heartbeat_foreign_device_id_is_403(stb_world, redis_client):
    """Body device_id must agree with the token's `dev` claim, never override it."""
    r = await _post(stb_world, redis_client, V1_HEARTBEAT,
                    {"device_id": "someone-elses-device"}, token=_stb_token(stb_world))
    assert r.status_code == 403, r.text


async def test_v1_heartbeat_without_token_renews_no_slot(stb_world, redis_client):
    """The 401 must land before the ZSET slot is touched."""
    await _post(stb_world, redis_client, V1_HEARTBEAT, {"device_id": STB_DEVICE_ID})
    assert await redis_client.keys("nexora:active_conns:*") == []
    assert await redis_client.keys("nexora:heartbeat:*") == []
