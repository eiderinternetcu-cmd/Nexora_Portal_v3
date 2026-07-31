"""sessions.user_agent — SessionService.create_iptv_session wrote it unclipped.

sessions.user_agent is varchar(512) in both migration 002
(002_sessions_device_fingerprint.py) and app/models/session.py:34 — ORM and
migration AGREE, so tests/test_migration_schema_parity.py is structurally blind
to this by design: the divergence is not between the column and the model, it is
between the column and what SessionService.create_iptv_session actually wrote,
which was the header value with no cap at all.

app/api/stb/playback.py:91 and app/api/client/playback.py:190 both pass
request.headers.get("User-Agent") straight through. A real Android TV / STB
navigator.userAgent routinely exceeds 512 chars — the same client population
that already forced os_version from 32 to 512 in app/schemas/client.py — so a
long User-Agent hits StringDataRightTruncation on the INSERT and 500s
/api/stb/auth/play and /api/client/playback/authorize.

Follows the pattern in app/models/audit.py / app/services/audit_service.py:
the cap lives next to the model (app/models/session.py:
SESSION_USER_AGENT_MAX_LEN), the clip is centralized in SessionService (the two
entry points that persist client-supplied ip/user_agent), and _assert_fits
raises at import if the cap and the column ever diverge.
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.models.session import (
    Session,
    SESSION_IP_ADDRESS_MAX_LEN,
    SESSION_USER_AGENT_MAX_LEN,
)
from app.models.subscriber import Subscriber, SubscriberStatus
from app.models.device import Device
from app.services.session_service import SessionService

pytestmark = pytest.mark.asyncio

# A real Android TV / STB User-Agent, padded past the 512-char column.
UA_600 = (
    "Mozilla/5.0 (Linux; Android 14; BRAVIA 4K VH2 Build/PTT1.220621.001; wv) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/120.0.6099.230 "
    "Safari/537.36 CrKey/1.56.500000 " + "x" * 380
)


async def _make_subscriber_and_device(db_session, suffix: str):
    sub = Subscriber(
        username=f"ua_test_{suffix}", status=SubscriberStatus.active, password_hash="x"
    )
    db_session.add(sub)
    await db_session.flush()
    device = Device(
        subscriber_id=sub.id, device_id=f"dev-ua-{suffix}", device_type="android_tv"
    )
    db_session.add(device)
    await db_session.flush()
    return sub, device


async def test_long_user_agent_creates_session_without_blowing_up(db_session, redis_client):
    """The regression this bug asked for: a ~600-char User-Agent creates the
    IPTV session (no StringDataRightTruncation) and is stored truncated to the
    column width, not silently dropped or left over-length."""
    assert len(UA_600) > SESSION_USER_AGENT_MAX_LEN

    sub, device = await _make_subscriber_and_device(db_session, "1")
    svc = SessionService(redis_client, db_session)

    session = await svc.create_iptv_session(
        subscriber_id=sub.id,
        device_id=device.id,
        access_jti=str(uuid.uuid4()),
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        ip="203.0.113.10",
        user_agent=UA_600,
    )
    await db_session.commit()

    assert session.user_agent == UA_600[:SESSION_USER_AGENT_MAX_LEN]
    assert len(session.user_agent) == SESSION_USER_AGENT_MAX_LEN


async def test_long_ip_creates_session_without_blowing_up(db_session, redis_client):
    """ip_address is the same class of client-supplied, column-bounded field —
    get_client_ip() returns the raw X-Forwarded-For value unbounded under the
    default CLIENT_IP_SOURCE=legacy."""
    sub, device = await _make_subscriber_and_device(db_session, "2")
    svc = SessionService(redis_client, db_session)

    long_ip = "1.2.3.4, " * 20  # a spoofed/forwarded chain far past varchar(45)
    assert len(long_ip) > SESSION_IP_ADDRESS_MAX_LEN

    session = await svc.create_iptv_session(
        subscriber_id=sub.id,
        device_id=device.id,
        access_jti=str(uuid.uuid4()),
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        ip=long_ip,
        user_agent="short-ua",
    )
    await db_session.commit()

    assert len(session.ip_address) == SESSION_IP_ADDRESS_MAX_LEN


async def test_empty_strings_are_stored_as_null(db_session, redis_client):
    """Preserved from the `(x or "")[:n] or None` idiom AuditService.log uses."""
    sub, device = await _make_subscriber_and_device(db_session, "3")
    svc = SessionService(redis_client, db_session)

    session = await svc.create_iptv_session(
        subscriber_id=sub.id,
        device_id=device.id,
        access_jti=str(uuid.uuid4()),
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        ip="",
        user_agent=None,
    )
    await db_session.commit()

    assert session.ip_address is None
    assert session.user_agent is None


# ── the divergence guard ─────────────────────────────────────────────────────

def test_caps_fit_their_columns():
    """The invariant whose violation caused this: an application cap that
    exceeds its column silently produces values that pass truncation and then
    fail the INSERT. _assert_fits enforces it at import time; this pins it so
    the guard itself cannot be dropped unnoticed."""
    from app.models import session as session_mod

    for column, cap in (
        ("user_agent", SESSION_USER_AGENT_MAX_LEN),
        ("ip_address", SESSION_IP_ADDRESS_MAX_LEN),
    ):
        length = getattr(Session.__table__.c[column].type, "length", None)
        assert length is None or cap <= length, (
            f"sessions.{column} holds {length} but the cap is {cap}"
        )

    with pytest.raises(RuntimeError, match="would pass truncation"):
        session_mod._assert_fits("user_agent", SESSION_USER_AGENT_MAX_LEN + 1)
