"""The M3U importer must NOT persist a direct-origin source_url (gate bypass)."""
import scripts.import_m3u_channels as imp


def test_default_node_mode_source_url_is_null(monkeypatch):
    monkeypatch.setattr(imp, "_SOURCE_MODE", "node")
    assert imp._safe_source_url("TeleNostalgia", "co-main") is None


def test_relative_mode_is_same_origin(monkeypatch):
    monkeypatch.setattr(imp, "_SOURCE_MODE", "relative")
    out = imp._safe_source_url("TeleNostalgia", "co-main")
    assert out == "/stream/co-main/TeleNostalgia/index.m3u8"


def test_absolute_mode_uses_https_public_base(monkeypatch):
    monkeypatch.setattr(imp, "_SOURCE_MODE", "absolute")
    monkeypatch.setattr(imp, "_PUBLIC_BASE", "https://nexoraplay.net")
    out = imp._safe_source_url("TeleNostalgia", "co-main")
    assert out == "https://nexoraplay.net/stream/co-main/TeleNostalgia/index.m3u8"


def test_never_builds_direct_origin(monkeypatch):
    for mode in ("node", "relative"):
        monkeypatch.setattr(imp, "_SOURCE_MODE", mode)
        out = imp._safe_source_url("X", "ec-main") or ""
        assert "8002" not in out
        assert "181.78.246.211" not in out and "38.210.187.13" not in out
        assert not out.startswith("http://")


def test_enrich_default_has_no_direct_origin(monkeypatch):
    monkeypatch.setattr(imp, "_SOURCE_MODE", "node")
    row = imp._enrich({"number": 1, "name": "X", "category": "c",
                       "stream_key": "TeleNostalgia", "flussonic_node": "co-main"})
    assert row["source_url"] is None
    assert row["flussonic_node"] == "co-main"
    assert row["stream_key"] == "TeleNostalgia"
