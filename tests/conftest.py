"""Pytest fixtures for Nexora tests.

Uses a dedicated PostgreSQL test database (NEVER production):
  TEST_DATABASE_URL=postgresql+psycopg://user:pass@host:port/nexora_test
If TEST_DATABASE_URL is not set, tests that need the DB are skipped.

Requires: pytest, pytest-asyncio  (add to requirements-dev.txt).
"""
import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.database import Base
import app.models  # noqa: F401  ensure all models are registered on Base.metadata

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL, reason="TEST_DATABASE_URL not set"
)


@pytest_asyncio.fixture(scope="function")
async def db_session():
    if not TEST_DATABASE_URL:
        pytest.skip("TEST_DATABASE_URL not set")
    engine = create_async_engine(TEST_DATABASE_URL, future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    SessionLocal = async_sessionmaker(engine, expire_on_commit=False)
    async with SessionLocal() as session:
        yield session
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest_asyncio.fixture
async def redis_client():
    """Isolated Redis (db 15) for tests that exercise concurrency/sessions."""
    import redis.asyncio as aioredis
    url = os.environ.get("TEST_REDIS_URL", "redis://redis:6379/15")
    r = aioredis.from_url(url, encoding="utf-8", decode_responses=True)
    await r.flushdb()
    yield r
    await r.flushdb()
    await r.aclose()


@pytest_asyncio.fixture
async def entitlement_world(db_session):
    """Create a coherent subscriber+plan+subscription+device+channel graph.

    Returns a dict of created objects + helpers.
    """
    from app.models.subscriber import Subscriber, SubscriberStatus
    from app.models.plan import Plan
    from app.models.subscription import Subscription
    from app.models.device import Device
    from app.models.channel import Channel
    from app.models.plan_channel import PlanChannel

    now = datetime.now(timezone.utc)

    sub = Subscriber(username="t_user", status=SubscriberStatus.active, password_hash="x")
    plan = Plan(name="t_plan", max_connections=2, max_devices=5, duration_days=365, is_active=True)
    db_session.add_all([sub, plan])
    await db_session.flush()

    subscription = Subscription(
        subscriber_id=sub.id, plan_id=plan.id,
        starts_at=now - timedelta(days=1), expires_at=now + timedelta(days=30),
        is_active=True,
    )
    device = Device(subscriber_id=sub.id, device_id="dev-1", device_type="web_player", is_blocked=False)
    ch_in = Channel(channel_key="canal-1", number=1, name="IN", stream_key="K1", is_active=True)
    ch_out = Channel(channel_key="canal-99", number=99, name="OUT", stream_key="K99", is_active=True)
    db_session.add_all([subscription, device, ch_in, ch_out])
    await db_session.flush()

    db_session.add(PlanChannel(plan_id=plan.id, channel_id=ch_in.id, is_enabled=True))
    await db_session.commit()

    return {
        "session": db_session,
        "subscriber": sub, "plan": plan, "subscription": subscription,
        "device": device, "ch_in": ch_in, "ch_out": ch_out,
    }
