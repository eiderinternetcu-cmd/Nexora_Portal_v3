"""M2 fix — Flussonic management URL separated from the (same-origin) playback base.

Health/list calls must target the real management origin, not the gated
/stream/* path used for playback URLs.
"""
from app.integrations.flussonic_client import FlussonicClient, _API_PREFIX


def test_mgmt_base_separated_from_playback_base():
    c = FlussonicClient(
        base_url="https://nexoraplay.net/stream/ec-main",
        user="u", password="p",
        mgmt_base_url="http://181.78.246.211:8002",
    )
    # playback URLs stay same-origin
    assert c.stream_hls_url("S1") == "https://nexoraplay.net/stream/ec-main/S1/index.m3u8"
    # management calls target the real origin
    assert c._mgmt == "http://181.78.246.211:8002"
    assert f"{c._mgmt}{_API_PREFIX}/streams" == "http://181.78.246.211:8002/flussonic/api/v3/streams"


def test_mgmt_defaults_to_base_when_absent():
    c = FlussonicClient(base_url="http://origin:8002/", user="u", password="p")
    assert c._mgmt == "http://origin:8002"
    assert c._base == "http://origin:8002"
