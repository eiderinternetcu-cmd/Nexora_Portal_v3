"""Unit tests for the controlled same-origin backfill planner (pure, no DB)."""
import scripts.backfill_channel_source_urls_same_origin as bf


def _ch(**over):
    base = {"id": "1", "channel_key": "canal-1", "number": 1, "name": "X",
            "source_url": None, "hls_path": "index.m3u8", "stream_key": "K1",
            "flussonic_node": "co-main"}
    base.update(over)
    return base


def test_null_source_url_is_ok():
    p = bf.plan_channel(_ch(source_url=None))
    assert p["status"] == "OK"


def test_relative_same_origin_is_ok():
    p = bf.plan_channel(_ch(source_url="/stream/co-main/K1/index.m3u8"))
    assert p["status"] == "OK"


def test_known_ip_co_main_maps_to_co_main():
    p = bf.plan_channel(_ch(source_url="http://38.210.187.13:8002/TeleNostalgia/index.m3u8",
                            stream_key="", flussonic_node=""))
    assert p["status"] == "FIX"
    assert p["proposed"]["flussonic_node"] == "co-main"
    assert p["proposed"]["stream_key"] == "TeleNostalgia"
    # default mode = relative → same-origin path
    assert p["proposed"]["source_url"] == "/stream/co-main/TeleNostalgia/index.m3u8"


def test_known_ip_ec_main_maps_to_ec_main():
    p = bf.plan_channel(_ch(source_url="http://181.78.246.211:8002/GOLDEN_PLUS/index.m3u8",
                            stream_key="GOLDEN_PLUS", flussonic_node="ec-main"))
    assert p["status"] == "FIX"
    # node/key already correct → source_url rewritten to same-origin relative
    assert p["proposed"]["source_url"] == "/stream/ec-main/GOLDEN_PLUS/index.m3u8"


def test_node_mode_nulls_source_url():
    p = bf.plan_channel(_ch(source_url="http://38.210.187.13:8002/TeleNostalgia/index.m3u8",
                            stream_key="TeleNostalgia"), mode="node")
    assert p["proposed"]["source_url"] is None


def test_unknown_host_is_risk_and_untouched():
    p = bf.plan_channel(_ch(source_url="https://cdn-externo.example/x/index.m3u8"))
    assert p["status"] == "RISK"
    assert p["proposed"] == {}


def test_unknown_ip_is_risk():
    p = bf.plan_channel(_ch(source_url="http://10.0.0.9:8002/x/index.m3u8"))
    assert p["status"] == "RISK"
    assert p["proposed"] == {}


def test_stream_key_mismatch_is_not_overwritten():
    p = bf.plan_channel(_ch(source_url="http://38.210.187.13:8002/PathKey/index.m3u8",
                            stream_key="ExistingKey"))
    assert p["status"] == "FIX"
    assert "stream_key" not in p["proposed"]  # safety: keep existing
    assert any("se conserva el actual" in r for r in p["reasons"])


def test_fix_result_is_never_direct_origin():
    p = bf.plan_channel(_ch(source_url="http://38.210.187.13:8002/TeleNostalgia/index.m3u8"))
    new = p["proposed"].get("source_url")
    assert new is None or new.startswith("/stream/") or new.startswith("https://")


def test_relative_mode_produces_same_origin_relative():
    p = bf.plan_channel(_ch(source_url="http://38.210.187.13:8002/TeleNostalgia/index.m3u8",
                            stream_key="TeleNostalgia"),
                        mode="relative")
    assert p["proposed"]["source_url"] == "/stream/co-main/TeleNostalgia/index.m3u8"


def test_plan_is_pure_no_mutation():
    ch = _ch(source_url="http://38.210.187.13:8002/TeleNostalgia/index.m3u8")
    snapshot = dict(ch)
    bf.plan_channel(ch)
    assert ch == snapshot  # plan_channel must not mutate input


def test_dry_run_is_default():
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--apply", action="store_true")
    assert p.parse_args([]).apply is False  # default = dry-run


def test_sanitize_strips_secrets():
    out = bf.sanitize_url("https://u:pw@nexoraplay.net/stream/co-main/K1/index.m3u8?token=ey.X.y")
    assert "pw" not in out and "token" not in out
