"""NX-CONC — atomic concurrent-connection limit (ConnectionService, Lua).

The limit check and the add happen in a single server-side script, so concurrent
opens of distinct new devices can never exceed max_connections.
"""
import asyncio
import uuid

import pytest

from app.services.connection_service import ConnectionService

pytestmark = pytest.mark.asyncio

SUB = "11111111-1111-1111-1111-111111111111"


async def test_open_up_to_limit_then_denies(redis_client):
    svc = ConnectionService(redis_client)
    assert await svc.open_connection(SUB, "dev-a", 2) is True
    assert await svc.open_connection(SUB, "dev-b", 2) is True
    assert await svc.open_connection(SUB, "dev-c", 2) is False  # 3rd distinct → limit
    assert await svc.count_active(SUB) == 2


async def test_existing_device_renew_does_not_count(redis_client):
    svc = ConnectionService(redis_client)
    assert await svc.open_connection(SUB, "dev-a", 1) is True
    # same device again → renew, still allowed even at max=1
    assert await svc.open_connection(SUB, "dev-a", 1) is True
    assert await svc.count_active(SUB) == 1


async def test_close_frees_a_slot(redis_client):
    svc = ConnectionService(redis_client)
    await svc.open_connection(SUB, "dev-a", 1)
    assert await svc.open_connection(SUB, "dev-b", 1) is False
    await svc.close_connection(SUB, "dev-a")
    assert await svc.open_connection(SUB, "dev-b", 1) is True


async def test_concurrent_opens_never_exceed_limit(redis_client):
    """The core race: N concurrent opens of DISTINCT devices, limit M → exactly M
    succeed. The old check-then-add had a window where all N could pass."""
    svc = ConnectionService(redis_client)
    MAX = 3
    N = 40
    devices = [f"dev-{uuid.uuid4()}" for _ in range(N)]
    results = await asyncio.gather(*(svc.open_connection(SUB, d, MAX) for d in devices))
    granted = sum(1 for r in results if r)
    assert granted == MAX, f"granted {granted}, expected {MAX}"
    assert await svc.count_active(SUB) == MAX  # ZSET never exceeded the cap
