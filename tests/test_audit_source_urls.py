"""Unit tests for the read-only channel source_url auditor (classify())."""
from scripts.audit_channel_source_urls import classify, sanitize_url, DEFAULT_NODES

ORIGINS = ["https://nexoraplay.net"]
PREFIXES = ["/stream/"]
NODES = ["ec-main", "co-main", "ec-quito"]


def test_ec_quito_is_an_allowed_default_node():
    assert "ec-quito" in DEFAULT_NODES


def test_ec_quito_same_origin_ok():
    status, _ = classify(
        {"source_url": "/stream/ec-quito/K1/index.m3u8", "hls_path": "index.m3u8",
         "flussonic_node": "ec-quito"},
        ORIGINS, PREFIXES, NODES,
    )
    assert status == "OK"


def _c(**over):
    base = {"source_url": None, "hls_path": "index.m3u8", "flussonic_node": "co-main"}
    base.update(over)
    return classify(base, ORIGINS, PREFIXES, NODES)


# ── OK cases ──────────────────────────────────────────────────────────────────

def test_empty_source_url_resolves_by_node_ok():
    status, _ = _c(source_url=None)
    assert status == "OK"


def test_relative_stream_path_ok():
    status, _ = _c(source_url="/stream/co-main/K1/index.m3u8")
    assert status == "OK"


def test_same_origin_https_ok():
    status, _ = _c(source_url="https://nexoraplay.net/stream/co-main/K1/index.m3u8")
    assert status == "OK"


# ── RISK cases ────────────────────────────────────────────────────────────────

def test_direct_ip_and_port_is_risk():
    status, reasons = _c(source_url="http://181.78.246.211:8002/K1/index.m3u8")
    assert status == "RISK"
    joined = " ".join(reasons)
    assert "IP directa" in joined and "no HTTPS" in joined


def test_flussonic_port_host_is_risk():
    status, reasons = _c(source_url="http://origin.example.com:8002/K1/index.m3u8")
    assert status == "RISK"
    assert any("puerto directo" in r for r in reasons)


def test_external_host_is_risk():
    status, reasons = _c(source_url="https://evil.example/stream/co-main/K1/index.m3u8")
    assert status == "RISK"
    assert any("host externo" in r for r in reasons)


def test_allowed_host_wrong_path_is_risk():
    status, reasons = _c(source_url="https://nexoraplay.net/live/K1/index.m3u8")
    assert status == "RISK"
    assert any("path no /stream" in r for r in reasons)


def test_relative_non_stream_path_is_risk():
    status, reasons = _c(source_url="/live/K1/index.m3u8")
    assert status == "RISK"


def test_http_not_https_same_host_is_risk():
    status, reasons = _c(source_url="http://nexoraplay.net/stream/co-main/K1/index.m3u8")
    assert status == "RISK"
    assert any("no HTTPS" in r for r in reasons)


def test_absolute_hls_path_is_risk():
    status, reasons = _c(hls_path="http://181.78.246.211:8002/x.m3u8")
    assert status == "RISK"
    assert any("hls_path absoluto" in r for r in reasons)


def test_unknown_node_is_risk():
    status, reasons = _c(flussonic_node="rogue-node")
    assert status == "RISK"
    assert any("node no permitido" in r for r in reasons)


# ── sanitizer never leaks secrets ─────────────────────────────────────────────

def test_sanitize_strips_userinfo_and_query():
    out = sanitize_url("https://user:secretpass@nexoraplay.net/stream/co-main/K1/index.m3u8?token=ey.ABC.def")
    assert "secretpass" not in out
    assert "token" not in out
    assert out == "https://nexoraplay.net/stream/co-main/K1/index.m3u8"
