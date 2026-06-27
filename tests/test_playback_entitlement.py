"""FASE 2 — EntitlementService integrated into StreamAuthService.authorize.

Verifies the feature-flag behavior:
  - ENTITLEMENT_ENFORCE=True  → channel not in plan ⇒ 403, no session/token/url.
  - ENTITLEMENT_ENFORCE=False → not blocked, but a warning is logged.
  - channel in plan ⇒ allow regardless.
"""
import logging

import pytest
from sqlalchemy import func, select

from app.core.exceptions import NexoraException
from app.models.session import Session
from app.services.stream_auth_service import StreamAuthService
from app.services import stream_auth_service as sas_mod

pytestmark = pytest.mark.asyncio


async def _authorize(world, redis, channel_key, stream_key):
    svc = StreamAuthService(world["session"], redis)
    return await svc.authorize(
        subscriber_id=world["subscriber"].id,
        device_id_str=world["device"].device_id,
        channel_id=stream_key,
        channel_key=channel_key,
    )


async def _session_count(world) -> int:
    return (
        await world["session"].execute(select(func.count()).select_from(Session))
    ).scalar()


async def test_playback_authorize_allows_channel_in_plan(entitlement_world, redis_client, monkeypatch):
    monkeypatch.setattr(sas_mod.settings, "entitlement_enforce", True)
    res = await _authorize(entitlement_world, redis_client, "canal-1", "K1")
    assert res.token
    assert await _session_count(entitlement_world) == 1


async def test_playback_authorize_denies_when_enforced(entitlement_world, redis_client, monkeypatch):
    monkeypatch.setattr(sas_mod.settings, "entitlement_enforce", True)
    with pytest.raises(NexoraException) as ei:
        await _authorize(entitlement_world, redis_client, "canal-99", "K99")
    assert ei.value.status_code == 403
    assert ei.value.detail == "CHANNEL_NOT_INCLUDED"


async def test_playback_authorize_denied_creates_no_session_token_url(entitlement_world, redis_client, monkeypatch):
    monkeypatch.setattr(sas_mod.settings, "entitlement_enforce", True)
    with pytest.raises(NexoraException):
        await _authorize(entitlement_world, redis_client, "canal-99", "K99")
    # nothing was created
    assert await _session_count(entitlement_world) == 0
    keys = await redis_client.keys("nexora:playback:*")
    assert keys == []


async def test_playback_authorize_does_not_block_when_enforce_off(entitlement_world, redis_client, monkeypatch, caplog):
    monkeypatch.setattr(sas_mod.settings, "entitlement_enforce", False)
    with caplog.at_level(logging.WARNING, logger="app.services.stream_auth_service"):
        res = await _authorize(entitlement_world, redis_client, "canal-99", "K99")
    assert res.token  # not blocked even though channel is not in plan
    assert any("entitlement deny" in r.getMessage() for r in caplog.records)


async def test_playback_authorize_testuser_like_annual_plan_allows(entitlement_world, redis_client, monkeypatch):
    # canal-1 is seeded into the plan (entitlement_world) → mirrors testuser1+annual+seed
    monkeypatch.setattr(sas_mod.settings, "entitlement_enforce", True)
    res = await _authorize(entitlement_world, redis_client, "canal-1", "K1")
    assert res.token
