"""Stress F2 — the Redis pool must be bounded by US, and must wait, not explode.

`get_redis()` used to call `from_url` with no pool arguments, so the ceiling was
whatever redis-py's default happened to be. That is not a constant across the
range requirements.txt allows (`redis[asyncio]>=5.2.0`): 7.4.0 is effectively
unbounded, 8.1.0 caps at 100. The deployed image runs 8.1.0, so the API had a
100-op ceiling nobody chose — and the plain pool RAISES MaxConnectionsError
instead of queueing, so every op past the ceiling became a 500. Measured then:
errors == concurrency - 100, exactly (150 → 50, 300 → 200, 1000 → 900).

The tests below pin both halves of the fix: the bound is configuration, and
above the bound an operation WAITS. They use tiny pools so the behavior is
deterministic rather than raced.
"""
import asyncio

import pytest
import redis.asyncio as aioredis
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import MaxConnectionsError

import app.redis_client as rc

pytestmark = pytest.mark.asyncio


class _Cfg:
    """Minimal stand-in for Settings — get_redis() reads exactly these three."""

    def __init__(self, url: str, max_connections: int, timeout: float):
        self.redis_url = url
        self.redis_max_connections = max_connections
        self.redis_pool_timeout_seconds = timeout


@pytest.fixture
def redis_url():
    import os

    return os.environ.get("TEST_REDIS_URL", "redis://redis:6379/15")


async def _client(monkeypatch, redis_url, max_connections, timeout):
    """Build the app's singleton under a given pool config.

    The conftest `_isolate_global_redis` autouse fixture nulls and closes
    rc._redis around every test, so this never leaks into another test.
    """
    monkeypatch.setattr(rc, "settings", _Cfg(redis_url, max_connections, timeout))
    rc._redis = None
    return await rc.get_redis()


async def test_pool_is_bounded_by_configuration_not_by_the_library(monkeypatch, redis_url):
    """The regression that started F2: whatever redis-py's default is, ours wins."""
    client = await _client(monkeypatch, redis_url, max_connections=17, timeout=5.0)
    assert client.connection_pool.max_connections == 17

    default_pool = aioredis.from_url(redis_url).connection_pool
    try:
        assert client.connection_pool.max_connections != default_pool.max_connections, (
            "the configured bound coincides with the library default — this test "
            "cannot tell the two apart; pick a different value"
        )
    finally:
        await default_pool.disconnect()


async def test_default_pool_waits_for_a_free_connection(monkeypatch, redis_url):
    """The behavioral half: with every connection busy, an operation queues.

    Deterministic on purpose — the connections are held by hand instead of
    racing N coroutines, so this cannot flake.
    """
    client = await _client(monkeypatch, redis_url, max_connections=2, timeout=5.0)
    pool = client.connection_pool
    assert isinstance(pool, aioredis.BlockingConnectionPool)

    held = [await pool.get_connection(), await pool.get_connection()]
    try:
        waiting = asyncio.create_task(client.ping())
        await asyncio.sleep(0.2)
        assert not waiting.done(), "the op did not wait — the pool is not blocking"

        await pool.release(held.pop())
        assert await asyncio.wait_for(waiting, timeout=5) is True
    finally:
        for conn in held:
            await pool.release(conn)


async def test_the_wait_is_bounded(monkeypatch, redis_url):
    """Waiting is not the same as waiting forever: an unbounded queue would let a
    stuck Redis hang every worker silently instead of surfacing."""
    client = await _client(monkeypatch, redis_url, max_connections=1, timeout=0.3)
    pool = client.connection_pool

    held = await pool.get_connection()
    try:
        with pytest.raises(RedisConnectionError):
            await client.ping()
    finally:
        await pool.release(held)


async def test_fail_fast_escape_hatch_still_bounded(monkeypatch, redis_url):
    """timeout <= 0 restores the old raise-immediately behavior — but the size is
    still ours, never the library's."""
    client = await _client(monkeypatch, redis_url, max_connections=3, timeout=0)
    pool = client.connection_pool
    assert not isinstance(pool, aioredis.BlockingConnectionPool)
    assert pool.max_connections == 3

    held = [await pool.get_connection() for _ in range(3)]
    try:
        with pytest.raises(MaxConnectionsError):
            await client.ping()
    finally:
        for conn in held:
            await pool.release(conn)


async def test_burst_above_the_pool_size_completes_instead_of_erroring(monkeypatch, redis_url):
    """The end-to-end shape of F2: a reconnect stampede is a latency problem, not
    an error. 60 concurrent ops through a pool of 5 must all succeed."""
    client = await _client(monkeypatch, redis_url, max_connections=5, timeout=5.0)
    key = "nexora:test:f2:burst"
    await client.delete(key)
    try:
        results = await asyncio.gather(
            *(client.incr(key) for _ in range(60)), return_exceptions=True
        )
        errors = [r for r in results if isinstance(r, BaseException)]
        assert not errors, f"pool errors under burst: {[type(e).__name__ for e in errors]}"
        assert int(await client.get(key)) == 60
    finally:
        await client.delete(key)
