"""NX-AUTH — the client IP must come from the edge, not from the caller.

`get_client_ip` fed the login lockout, the per-IP rate limiter, audit rows and
the playback token's 'cip' claim from the FIRST value of X-Forwarded-For — a
header the caller writes. That is:

  · evadable  — a fresh fake address per request and no per-IP bucket ever fills;
  · abusable  — send a colleague's address and the legacy lockout in
                AuthService (`_record_failed_attempt(ip)`) blocks THEM for
                LOGIN_LOCKOUT_MINUTES.

CLIENT_IP_SOURCE=edge honors a forwarded header only when the TCP peer is a
proxy listed in TRUSTED_PROXY_CIDRS, and reads a multi-hop chain from the RIGHT.
Default stays `legacy`, so the tests that assert the old behavior are the guard
on that default.
"""
import os
import uuid

import httpx
import pytest
from httpx import ASGITransport
from starlette.requests import Request

from app.core import dependencies as dep_mod
from app.core.dependencies import get_client_ip
from app.middleware.rate_limit import RateLimitMiddleware

pytestmark = pytest.mark.asyncio

PROXY_NET = "172.18.0.0/16"
PROXY_PEER = "172.18.0.5"
REAL_CLIENT = "198.51.100.7"
SPOOFED = "203.0.113.66"


def _request(peer: str | None, **headers: str) -> Request:
    """Minimal ASGI scope — no app needed, get_client_ip only reads peer+headers."""
    return Request({
        "type": "http",
        "method": "GET",
        "path": "/",
        "query_string": b"",
        "headers": [(k.replace("_", "-").lower().encode(), v.encode())
                    for k, v in headers.items()],
        "client": (peer, 51234) if peer else None,
    })


def _edge(monkeypatch, cidrs: str = PROXY_NET) -> None:
    monkeypatch.setattr(dep_mod.settings, "client_ip_source", "edge")
    monkeypatch.setattr(dep_mod.settings, "trusted_proxy_cidrs", cidrs)


# ── legacy mode: today's behavior, preserved by default ──────────────────────

def test_legacy_mode_is_the_default():
    assert dep_mod.settings.client_ip_source == "legacy"


def test_legacy_mode_still_believes_the_caller():
    """The hole itself, and the guarantee that deploying this branch changes
    nothing until the flag is flipped."""
    req = _request(PROXY_PEER, X_Forwarded_For=SPOOFED)
    assert get_client_ip(req) == SPOOFED


# ── edge mode: only the edge may assert a client IP ──────────────────────────

def test_edge_mode_ignores_a_spoofed_header_from_an_untrusted_peer(monkeypatch):
    """The abuse case. A client connecting directly (or any peer not in
    TRUSTED_PROXY_CIDRS) cannot name someone else's address any more, so it can
    neither dodge its own rate-limit bucket nor lock a third party out.

    Reverting get_client_ip to the X-Forwarded-For[0] read makes this fail.
    """
    _edge(monkeypatch)
    req = _request(REAL_CLIENT, X_Forwarded_For=SPOOFED, X_Real_IP=SPOOFED)
    assert get_client_ip(req) == REAL_CLIENT


def test_edge_mode_takes_x_real_ip_from_the_trusted_proxy(monkeypatch):
    """What nginx actually sends: X-Real-IP $remote_addr
    (deploy/nginx/snippets/proxy-common.conf). The spoofed XFF is ignored."""
    _edge(monkeypatch)
    req = _request(PROXY_PEER, X_Real_IP=REAL_CLIENT, X_Forwarded_For=SPOOFED)
    assert get_client_ip(req) == REAL_CLIENT


def test_edge_mode_reads_the_last_forwarded_hop_not_the_first(monkeypatch):
    """A forwarded chain grows to the RIGHT: everything left of the last hop is
    unverified client input. Reverting to `split(",")[0]` returns the spoofed
    value and this fails.
    """
    _edge(monkeypatch)
    req = _request(PROXY_PEER, X_Forwarded_For=f"{SPOOFED}, {REAL_CLIENT}")
    assert get_client_ip(req) == REAL_CLIENT


def test_edge_mode_falls_back_to_the_peer_when_the_proxy_asserts_nothing(monkeypatch):
    _edge(monkeypatch)
    assert get_client_ip(_request(PROXY_PEER)) == PROXY_PEER


def test_edge_mode_rejects_a_header_that_is_not_an_address(monkeypatch):
    """Garbage would otherwise become a Redis key fragment and an audit row."""
    _edge(monkeypatch)
    req = _request(PROXY_PEER, X_Real_IP="not-an-ip", X_Forwarded_For="'; DROP --")
    assert get_client_ip(req) == PROXY_PEER


def test_edge_mode_keeps_local_development_working(monkeypatch):
    """No proxy in front, no TRUSTED_PROXY_CIDRS: nothing is trusted and the
    connection address answers. The environment must not become unusable."""
    _edge(monkeypatch, cidrs="")
    assert get_client_ip(_request("127.0.0.1")) == "127.0.0.1"


def test_unparseable_cidr_entry_is_dropped_not_fatal(monkeypatch):
    """A typo in the variable must not trust everything, nor crash the app."""
    _edge(monkeypatch, cidrs="not-a-cidr")
    assert dep_mod.settings.trusted_proxy_networks == []
    assert get_client_ip(_request(PROXY_PEER, X_Real_IP=SPOOFED)) == PROXY_PEER


def test_no_peer_at_all_is_reported_as_unknown(monkeypatch):
    _edge(monkeypatch)
    assert get_client_ip(_request(None)) == "unknown"


# ── the same resolver must back the rate limiter ─────────────────────────────

def test_rate_limit_middleware_uses_the_shared_resolver(monkeypatch):
    """Fixing get_client_ip while RateLimitMiddleware kept its own private copy
    would have left the per-IP limiter wide open. Restoring that copy fails here.
    """
    _edge(monkeypatch)
    req = _request(REAL_CLIENT, X_Forwarded_For=SPOOFED)
    assert RateLimitMiddleware._get_ip(req) == REAL_CLIENT


# ── end to end: header rotation stops buying fresh rate-limit buckets ────────

async def _login_burst(n: int, header_for) -> list[int]:
    """POST the admin login path n times and return the status codes.

    The rate limiter runs as middleware, before routing and before any DB work,
    so an invalid body is enough to consume a bucket slot.
    """
    from app.main import app
    async with httpx.AsyncClient(transport=ASGITransport(app=app),
                                 base_url="http://test") as c:
        return [
            (await c.post("/api/v1/auth/login", json={}, headers=header_for(i))).status_code
            for i in range(n)
        ]


@pytest.fixture
def _global_redis(monkeypatch):
    """Point the middleware's module-level Redis singleton at the test instance.

    The autouse _isolate_global_redis fixture in conftest closes and nulls it
    afterwards, so nothing leaks into another test's event loop.
    """
    import redis.asyncio as aioredis
    import app.redis_client as rc
    url = os.environ.get("TEST_REDIS_URL", "redis://redis:6379/15")
    rc._redis = aioredis.from_url(url, encoding="utf-8", decode_responses=True)
    return rc._redis


async def test_rotating_xff_evades_the_login_limit_in_legacy_mode(_global_redis):
    """Demonstrates the evasion on the live default: 30 requests to a path capped
    at 10/min, each with a different X-Forwarded-For, and not one 429."""
    tag = uuid.uuid4().int
    codes = await _login_burst(30, lambda i: {"X-Forwarded-For": f"10.{tag % 251}.{i}.1"})
    assert 429 not in codes


async def test_rotating_xff_no_longer_evades_the_login_limit_in_edge_mode(
    _global_redis, monkeypatch
):
    """Same burst, same spoofing, CLIENT_IP_SOURCE=edge. The ASGI peer
    (127.0.0.1) is not a trusted proxy, so all 30 requests share one bucket and
    the 10/min cap fires.

    Reverting get_client_ip (or restoring the middleware's private copy) removes
    every 429 here.
    """
    _edge(monkeypatch, cidrs="")
    tag = uuid.uuid4().int
    codes = await _login_burst(30, lambda i: {"X-Forwarded-For": f"10.{tag % 251}.{i}.1"})
    assert 429 in codes


async def test_edge_mode_still_separates_genuinely_different_clients(
    _global_redis, monkeypatch
):
    """Not merely "ignore all headers": behind a trusted proxy, distinct clients
    keep distinct buckets, so a shared edge does not become one global limit."""
    _edge(monkeypatch, cidrs="127.0.0.0/8")
    tag = uuid.uuid4().int % 200
    codes = await _login_burst(9, lambda i: {"X-Real-IP": f"192.0.2.{tag}" if i % 3 else f"198.51.100.{tag}"})
    assert 429 not in codes
