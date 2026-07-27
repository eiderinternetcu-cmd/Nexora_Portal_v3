"""Multi-domain playback_url — origin derived from the request, host allowlisted.

The web player is served from several domains (nexoraplay.net,
tvdigital.laredtelco.com, …). playback_url must be same-origin with the page or
the browser blocks the HLS request, so its ORIGIN comes from the request while
the PATH configured in the node base_url is preserved.

Host is client-controlled, so these tests also pin the security contract: only a
host present in PLAYBACK_HOST_ALLOWLIST may ever appear in a playback_url.

Pure unit tests — no DB, no Redis.
"""
import pytest
from starlette.requests import Request

from app.api.client import playback as pb_mod
from app.integrations import flussonic_client as fc
from app.models.channel import Channel

BASE_HOST = "nexoraplay.net"
NEW_HOST = "tvdigital.laredtelco.com"
EC_MAIN_BASE = f"https://{BASE_HOST}/stream/ec-main"
CO_MAIN_BASE = f"https://{BASE_HOST}/stream/co-main"

# The exact string production returns today for canal-1 / K1 on ec-main.
LEGACY_URL = f"https://{BASE_HOST}/stream/ec-main/K1/index.m3u8"


# ── helpers ──────────────────────────────────────────────────────────────────

def _req(host: str | None = None, *, xf_host: str | None = None,
         proto: str | None = None, scheme: str = "http") -> Request:
    """Minimal ASGI request. nginx forwards Host + X-Forwarded-{Host,Proto}."""
    headers: list[tuple[bytes, bytes]] = []
    if host is not None:
        headers.append((b"host", host.encode()))
    if xf_host is not None:
        headers.append((b"x-forwarded-host", xf_host.encode()))
    if proto is not None:
        headers.append((b"x-forwarded-proto", proto.encode()))
    return Request({
        "type": "http", "http_version": "1.1", "method": "GET",
        "scheme": scheme, "root_path": "",
        "path": "/api/client/playback/canal-1",
        "raw_path": b"/api/client/playback/canal-1",
        "query_string": b"", "headers": headers,
        "client": ("10.0.0.1", 51234), "server": ("nexora_api", 8000),
    })


def _channel(*, node: str = "ec-main", hls_path: str | None = None,
             source_url: str | None = None) -> Channel:
    return Channel(channel_key="canal-1", number=1, name="C1", stream_key="K1",
                   is_active=True, flussonic_node=node, hls_path=hls_path,
                   source_url=source_url)


@pytest.fixture
def nodes(monkeypatch):
    """Configure ec-main/co-main on the fixed base and reset the client cache."""
    s = pb_mod.settings
    monkeypatch.setattr(s, "flussonic_base_url", EC_MAIN_BASE)
    monkeypatch.setattr(s, "flussonic_readonly_user", "ro")
    monkeypatch.setattr(s, "flussonic_readonly_password", "x")
    monkeypatch.setattr(s, "flussonic_co_main_base_url", CO_MAIN_BASE)
    monkeypatch.setattr(s, "flussonic_co_main_user", "ro")
    monkeypatch.setattr(s, "flussonic_co_main_password", "x")
    monkeypatch.setattr(s, "signed_url_enforce", False)
    monkeypatch.setattr(s, "playback_host_allowlist", f"{BASE_HOST},{NEW_HOST}")
    fc._node_clients.clear()
    yield s
    fc._node_clients.clear()


# ── allowlist parsing ────────────────────────────────────────────────────────

def test_allowlist_parses_and_normalizes(nodes, monkeypatch):
    monkeypatch.setattr(nodes, "playback_host_allowlist",
                        f" {BASE_HOST} , TvDigital.LaredTelco.com ,, ")
    assert nodes.playback_allowed_hosts == {
        BASE_HOST: BASE_HOST,
        NEW_HOST: "TvDigital.LaredTelco.com",
    }


def test_allowlist_empty_by_default(nodes, monkeypatch):
    monkeypatch.setattr(nodes, "playback_host_allowlist", "")
    assert nodes.playback_allowed_hosts == {}


# ── rewrite: allowlisted host ────────────────────────────────────────────────

def test_allowlisted_host_rewrites_origin_and_keeps_base_path(nodes):
    url = pb_mod._resolve_playback_url(
        _channel(), "K1", _req(NEW_HOST, proto="https"))
    assert url == f"https://{NEW_HOST}/stream/ec-main/K1/index.m3u8"


def test_rewrite_keeps_per_node_base_path(nodes):
    url = pb_mod._resolve_playback_url(
        _channel(node="co-main"), "K1", _req(NEW_HOST, proto="https"))
    assert url == f"https://{NEW_HOST}/stream/co-main/K1/index.m3u8"


def test_rewrite_keeps_custom_hls_path(nodes):
    url = pb_mod._resolve_playback_url(
        _channel(hls_path="video.m3u8"), "K1", _req(NEW_HOST, proto="https"))
    assert url == f"https://{NEW_HOST}/stream/ec-main/K1/video.m3u8"


def test_host_match_is_case_insensitive_and_emits_configured_spelling(nodes):
    url = pb_mod._resolve_playback_url(
        _channel(), "K1", _req("TVDigital.LaredTelco.COM", proto="https"))
    # the CONFIGURED spelling is emitted, never the client's bytes
    assert url == f"https://{NEW_HOST}/stream/ec-main/K1/index.m3u8"


def test_x_forwarded_host_takes_precedence_over_host(nodes):
    url = pb_mod._resolve_playback_url(
        _channel(), "K1", _req("nexora_api:8000", xf_host=NEW_HOST, proto="https"))
    assert url == f"https://{NEW_HOST}/stream/ec-main/K1/index.m3u8"


def test_first_hop_of_chained_x_forwarded_host_is_used(nodes):
    url = pb_mod._resolve_playback_url(
        _channel(), "K1", _req(xf_host=f"{NEW_HOST}, proxy.internal", proto="https"))
    assert url == f"https://{NEW_HOST}/stream/ec-main/K1/index.m3u8"


def test_allowlist_entry_with_port_matches_host_with_port(nodes, monkeypatch):
    monkeypatch.setattr(nodes, "playback_host_allowlist", f"{BASE_HOST},tv.local:8443")
    url = pb_mod._resolve_playback_url(
        _channel(), "K1", _req("tv.local:8443", proto="https"))
    assert url == "https://tv.local:8443/stream/ec-main/K1/index.m3u8"


# ── no regression: nexoraplay.net and the legacy call path ───────────────────

def test_current_production_host_is_byte_identical_to_today(nodes):
    """Host nexoraplay.net must produce EXACTLY today's URL, character for character."""
    legacy = fc.get_flussonic_node_client("ec-main").stream_hls_url("K1", "index.m3u8")
    url = pb_mod._resolve_playback_url(
        _channel(), "K1", _req(BASE_HOST, proto="https"))
    assert url == legacy == LEGACY_URL


def test_no_request_keeps_fixed_base(nodes):
    assert pb_mod._resolve_playback_url(_channel(), "K1", None) == LEGACY_URL
    assert pb_mod._resolve_playback_url(_channel(), "K1") == LEGACY_URL


def test_empty_allowlist_disables_rewriting_entirely(nodes, monkeypatch):
    monkeypatch.setattr(nodes, "playback_host_allowlist", "")
    url = pb_mod._resolve_playback_url(
        _channel(), "K1", _req(NEW_HOST, proto="https"))
    assert url == LEGACY_URL


def test_channel_none_still_returns_none(nodes):
    assert pb_mod._resolve_playback_url(None, "K1", _req(NEW_HOST)) is None


# ── security: Host header injection ──────────────────────────────────────────

@pytest.mark.parametrize("evil_host", [
    "evil.tld",
    "nexoraplay.net.evil.tld",
    "evil.tld,nexoraplay.net",
    "nexoraplay.net@evil.tld",
    "evil.tld:443",
    " evil.tld ",
    "",
])
def test_non_allowlisted_host_falls_back_to_fixed_base(nodes, evil_host):
    url = pb_mod._resolve_playback_url(
        _channel(), "K1", _req(evil_host, proto="https"))
    assert url == LEGACY_URL
    assert "evil" not in url


def test_non_allowlisted_x_forwarded_host_falls_back(nodes):
    url = pb_mod._resolve_playback_url(
        _channel(), "K1", _req(BASE_HOST, xf_host="evil.tld", proto="https"))
    # X-Forwarded-Host wins, is not allowlisted → fixed base (never evil.tld)
    assert url == LEGACY_URL


def test_missing_host_header_falls_back(nodes):
    assert pb_mod._resolve_playback_url(_channel(), "K1", _req()) == LEGACY_URL


def test_allowlist_is_the_only_gate(nodes, monkeypatch):
    """Guards the guard: the SAME request that falls back above does rewrite once
    the host is allowlisted. Proves the fallback comes from the allowlist check
    and not from some unrelated short-circuit that would mask a broken gate."""
    req = _req("host-under-test.tld", proto="https")
    assert pb_mod._resolve_playback_url(_channel(), "K1", req) == LEGACY_URL

    monkeypatch.setattr(nodes, "playback_host_allowlist",
                        f"{BASE_HOST},host-under-test.tld")
    assert pb_mod._resolve_playback_url(_channel(), "K1", req) == \
        "https://host-under-test.tld/stream/ec-main/K1/index.m3u8"


# ── X-Forwarded-Proto ────────────────────────────────────────────────────────

def test_x_forwarded_proto_upgrades_plain_http_request(nodes, monkeypatch):
    """Behind nginx the request arrives over http; XFP carries the real scheme."""
    monkeypatch.setattr(nodes, "flussonic_base_url", f"http://{BASE_HOST}/stream/ec-main")
    fc._node_clients.clear()
    url = pb_mod._resolve_playback_url(
        _channel(), "K1", _req(NEW_HOST, proto="https", scheme="http"))
    assert url == f"https://{NEW_HOST}/stream/ec-main/K1/index.m3u8"


def test_x_forwarded_proto_http_stays_http_when_base_is_http(nodes, monkeypatch):
    monkeypatch.setattr(nodes, "flussonic_base_url", f"http://{BASE_HOST}/stream/ec-main")
    fc._node_clients.clear()
    url = pb_mod._resolve_playback_url(
        _channel(), "K1", _req(NEW_HOST, proto="http", scheme="http"))
    assert url == f"http://{NEW_HOST}/stream/ec-main/K1/index.m3u8"


def test_https_base_is_never_downgraded_to_cleartext(nodes):
    url = pb_mod._resolve_playback_url(
        _channel(), "K1", _req(NEW_HOST, proto="http", scheme="http"))
    assert url == f"https://{NEW_HOST}/stream/ec-main/K1/index.m3u8"


@pytest.mark.parametrize("bad_proto", ["javascript", "ftp", "https evil", ""])
def test_bogus_forwarded_proto_falls_back_to_request_scheme(nodes, monkeypatch, bad_proto):
    monkeypatch.setattr(nodes, "flussonic_base_url", f"http://{BASE_HOST}/stream/ec-main")
    fc._node_clients.clear()
    url = pb_mod._resolve_playback_url(
        _channel(), "K1", _req(NEW_HOST, proto=bad_proto, scheme="http"))
    assert url == f"http://{NEW_HOST}/stream/ec-main/K1/index.m3u8"


def test_request_scheme_used_when_no_forwarded_proto(nodes, monkeypatch):
    monkeypatch.setattr(nodes, "flussonic_base_url", f"http://{BASE_HOST}/stream/ec-main")
    fc._node_clients.clear()
    url = pb_mod._resolve_playback_url(
        _channel(), "K1", _req(NEW_HOST, scheme="https"))
    assert url == f"https://{NEW_HOST}/stream/ec-main/K1/index.m3u8"


# ── source_url fallback branch ───────────────────────────────────────────────

def test_source_url_relative_is_never_rewritten(nodes, monkeypatch):
    """Relative source_url has no origin — the browser resolves it per-domain."""
    monkeypatch.setattr(nodes, "flussonic_readonly_user", "")  # force the fallback
    fc._node_clients.clear()
    ch = _channel(source_url="/stream/ec-main/K1/index.m3u8")
    url = pb_mod._resolve_playback_url(ch, "K1", _req(NEW_HOST, proto="https"))
    assert url == "/stream/ec-main/K1/index.m3u8"


def test_source_url_foreign_origin_is_never_rewritten(nodes, monkeypatch):
    """A third-party/legacy origin has its own path layout — our host can't serve it."""
    monkeypatch.setattr(nodes, "flussonic_readonly_user", "")
    fc._node_clients.clear()
    ch = _channel(source_url="http://181.78.246.211:8002/K1/index.m3u8")
    url = pb_mod._resolve_playback_url(ch, "K1", _req(NEW_HOST, proto="https"))
    assert url == "http://181.78.246.211:8002/K1/index.m3u8"


def test_source_url_same_origin_is_rewritten(nodes, monkeypatch):
    """An absolute same-origin source_url names a host we own → swap it."""
    monkeypatch.setattr(nodes, "flussonic_readonly_user", "")
    fc._node_clients.clear()
    ch = _channel(source_url=f"https://{BASE_HOST}/stream/ec-main/K1/index.m3u8")
    url = pb_mod._resolve_playback_url(ch, "K1", _req(NEW_HOST, proto="https"))
    assert url == f"https://{NEW_HOST}/stream/ec-main/K1/index.m3u8"


def test_source_url_same_origin_unchanged_for_current_host(nodes, monkeypatch):
    monkeypatch.setattr(nodes, "flussonic_readonly_user", "")
    fc._node_clients.clear()
    ch = _channel(source_url=LEGACY_URL)
    assert pb_mod._resolve_playback_url(
        ch, "K1", _req(BASE_HOST, proto="https")) == LEGACY_URL


def test_source_url_not_rewritten_for_non_allowlisted_host(nodes, monkeypatch):
    monkeypatch.setattr(nodes, "flussonic_readonly_user", "")
    fc._node_clients.clear()
    ch = _channel(source_url=LEGACY_URL)
    assert pb_mod._resolve_playback_url(
        ch, "K1", _req("evil.tld", proto="https")) == LEGACY_URL


def test_no_stream_key_uses_source_url_branch(nodes):
    ch = _channel(source_url=LEGACY_URL)
    url = pb_mod._resolve_playback_url(ch, None, _req(NEW_HOST, proto="https"))
    assert url == f"https://{NEW_HOST}/stream/ec-main/K1/index.m3u8"


# ── interaction with SIGNED_URL_ENFORCE ──────────────────────────────────────

def test_signed_url_off_rewritten_url_carries_no_token(nodes, monkeypatch):
    monkeypatch.setattr(nodes, "signed_url_enforce", False)
    url = pb_mod._resolve_playback_url(_channel(), "K1", _req(NEW_HOST, proto="https"))
    assert pb_mod._maybe_sign(url, "TOK123") == \
        f"https://{NEW_HOST}/stream/ec-main/K1/index.m3u8"


def test_signed_url_on_appends_token_to_rewritten_url(nodes, monkeypatch):
    monkeypatch.setattr(nodes, "signed_url_enforce", True)
    url = pb_mod._resolve_playback_url(_channel(), "K1", _req(NEW_HOST, proto="https"))
    assert pb_mod._maybe_sign(url, "TOK123") == \
        f"https://{NEW_HOST}/stream/ec-main/K1/index.m3u8?token=TOK123"


def test_signed_url_on_non_allowlisted_host_signs_the_fixed_base(nodes, monkeypatch):
    monkeypatch.setattr(nodes, "signed_url_enforce", True)
    url = pb_mod._resolve_playback_url(_channel(), "K1", _req("evil.tld", proto="https"))
    signed = pb_mod._maybe_sign(url, "TOK123")
    assert signed == f"{LEGACY_URL}?token=TOK123"
    assert "evil" not in signed


def test_signed_url_on_current_host_is_byte_identical_to_today(nodes, monkeypatch):
    monkeypatch.setattr(nodes, "signed_url_enforce", True)
    url = pb_mod._resolve_playback_url(_channel(), "K1", _req(BASE_HOST, proto="https"))
    assert pb_mod._maybe_sign(url, "TOK123") == f"{LEGACY_URL}?token=TOK123"


def test_token_is_appended_once_with_existing_query(nodes, monkeypatch):
    """Rewriting must preserve an existing query string so _maybe_sign uses '&'."""
    monkeypatch.setattr(nodes, "signed_url_enforce", True)
    monkeypatch.setattr(nodes, "flussonic_readonly_user", "")
    fc._node_clients.clear()
    ch = _channel(source_url=f"https://{BASE_HOST}/stream/ec-main/K1/index.m3u8?a=1")
    url = pb_mod._resolve_playback_url(ch, "K1", _req(NEW_HOST, proto="https"))
    assert pb_mod._maybe_sign(url, "TOK") == \
        f"https://{NEW_HOST}/stream/ec-main/K1/index.m3u8?a=1&token=TOK"
