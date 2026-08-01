"""NX-CONC / stress F1 — the heartbeat must never mint a concurrency slot.

Regression suite for the bypass found by the playback stress run
(docs/STRESS_TESTS_PLAYBACK.md, F1): `extend_connection` did a bare ZADD, which
CREATES the ZSET member. `_OPEN_CONNECTION_LUA` deliberately skips the ZCARD
check when the member already exists (renewing your own slot must not consume a
second one), so a heartbeat sent just before an authorize made that authorize
skip the limit entirely. Measured then: 10 active connections on a plan of 3 —
the effective ceiling had become max_devices, not max_connections.

That matters commercially, not just technically: the concurrency limit is the
control that stops one subscription being shared across households. Every test
here fails if `extend_connection` can create or resurrect a member.

Every test below is Redis-only (no PostgreSQL, no HTTP), so it runs anywhere the
`redis_client` fixture does.
"""
import asyncio
import time
import uuid

import pytest

from app.redis_client import key_active_connections
from app.services.connection_service import ConnectionService

pytestmark = pytest.mark.asyncio

SUB = "22222222-2222-2222-2222-222222222222"
KEY = key_active_connections(SUB)


async def _fill(svc, max_conns: int) -> list[str]:
    """Occupy every slot with distinct devices. Returns their ids."""
    devices = [f"dev-{uuid.uuid4()}" for _ in range(max_conns)]
    for d in devices:
        assert await svc.open_connection(SUB, d, max_conns) is True
    return devices


async def test_heartbeat_cannot_create_a_slot(redis_client):
    """The primitive: a device with no slot heartbeats → nothing is created."""
    svc = ConnectionService(redis_client)
    MAX = 3
    await _fill(svc, MAX)
    intruder = "dev-intruder"
    assert await svc.open_connection(SUB, intruder, MAX) is False  # refused, as it must be

    renewed = await svc.extend_connection(SUB, intruder)

    assert renewed is False, "extend_connection reported a renewal it never had a slot for"
    assert await redis_client.zcard(KEY) == MAX, "heartbeat inserted a member into the ZSET"
    assert await redis_client.zscore(KEY, intruder) is None
    assert await svc.count_active(SUB) == MAX
    assert await svc.is_connected(SUB, intruder) is False


async def test_heartbeat_then_authorize_does_not_beat_the_limit(redis_client):
    """The full exploit chain, exactly as the stress probe drives it:
    refused authorize → heartbeat → authorize again. Step 3 used to return a
    playback token because the member now existed."""
    svc = ConnectionService(redis_client)
    MAX = 3
    await _fill(svc, MAX)
    intruder = "dev-intruder"

    assert await svc.open_connection(SUB, intruder, MAX) is False  # baseline
    await svc.extend_connection(SUB, intruder)                      # the trick

    assert await svc.open_connection(SUB, intruder, MAX) is False, (
        "a heartbeat let a refused device win the next authorize"
    )
    assert await redis_client.zcard(KEY) == MAX


async def test_zset_never_exceeds_max_under_repeated_abuse(redis_client):
    """Scaling the chain: the old code climbed to 10 slots on a limit of 3, i.e.
    all the way up to max_devices. The cap must hold whatever the call order."""
    svc = ConnectionService(redis_client)
    MAX = 3
    await _fill(svc, MAX)

    for _ in range(10):
        d = f"dev-{uuid.uuid4()}"
        await svc.extend_connection(SUB, d)
        await svc.open_connection(SUB, d, MAX)
        await svc.extend_connection(SUB, d)

    assert await redis_client.zcard(KEY) == MAX
    assert await svc.count_active(SUB) == MAX


async def test_concurrent_heartbeats_cannot_widen_the_zset(redis_client):
    """Same abuse in parallel — the check and the write are one server-side step,
    so there is no window between 'no slot' and the ZADD."""
    svc = ConnectionService(redis_client)
    MAX = 2
    await _fill(svc, MAX)
    intruders = [f"dev-{uuid.uuid4()}" for _ in range(30)]

    results = await asyncio.gather(*(svc.extend_connection(SUB, d) for d in intruders))

    assert not any(results)
    assert await redis_client.zcard(KEY) == MAX


async def test_heartbeat_cannot_resurrect_an_expired_slot(redis_client):
    """The slower door into the same bypass: let a slot lapse, let the freed
    capacity be taken by other devices, then heartbeat the dead member back.

    A bare `ZADD XX` would still resurrect it, because an expired member is
    physically present in the ZSET until something prunes it (nothing expires
    members proactively — see docs/STRESS_TESTS_PLAYBACK.md, section E). Hence
    the ZREMRANGEBYSCORE inside the same script, before the ZADD.
    """
    svc = ConnectionService(redis_client)
    MAX = 2
    lapsed = "dev-lapsed"
    now = time.time()

    # A slot that stopped beating 10s ago — still physically in the ZSET.
    await redis_client.zadd(KEY, {lapsed: now - 10})
    assert await redis_client.zcard(KEY) == 1
    assert await svc.is_connected(SUB, lapsed) is False

    # Meanwhile the household filled the freed capacity from other devices.
    await _fill(svc, MAX)
    assert await svc.count_active(SUB) == MAX

    assert await svc.extend_connection(SUB, lapsed) is False
    assert await redis_client.zcard(KEY) == MAX, "an expired member was resurrected"
    assert await redis_client.zscore(KEY, lapsed) is None


async def test_legitimate_renewal_still_works_indefinitely(redis_client):
    """The case the fix must NOT break: a device that is actually playing keeps
    its slot alive through heartbeats alone.

    This is load-bearing — the reissue path (GET /playback/{channel_id}) only
    calls is_connected() and never renews, so the heartbeat is the ONLY thing
    that keeps a live stream's slot from lapsing.
    """
    svc = ConnectionService(redis_client)
    MAX = 1
    device = "dev-watching"
    assert await svc.open_connection(SUB, device, MAX) is True
    first_score = await redis_client.zscore(KEY, device)

    for _ in range(20):
        assert await svc.extend_connection(SUB, device) is True

    assert await redis_client.zcard(KEY) == 1
    assert await svc.is_connected(SUB, device) is True
    assert await redis_client.zscore(KEY, device) >= first_score, "renewal moved the TTL backwards"
    assert await redis_client.ttl(KEY) > 0, "renewal must keep the key alive too"


async def test_renewal_of_one_device_does_not_disturb_the_others(redis_client):
    """A heartbeat touches exactly one member and leaves the rest of the
    household's slots — and their scores — alone."""
    svc = ConnectionService(redis_client)
    MAX = 3
    devices = await _fill(svc, MAX)
    scores_before = {d: await redis_client.zscore(KEY, d) for d in devices}

    assert await svc.extend_connection(SUB, devices[0]) is True

    assert await redis_client.zcard(KEY) == MAX
    for d in devices[1:]:
        assert await redis_client.zscore(KEY, d) == scores_before[d]
    assert await redis_client.zscore(KEY, devices[0]) >= scores_before[devices[0]]
