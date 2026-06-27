"""ec-quito (Quito Astra) must be a wired Flussonic node."""
from app.config import get_settings
from app.integrations import flussonic_client as fc


def test_ec_quito_client_configured(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "flussonic_ec_quito_base_url", "https://nexoraplay.net/stream/ec-quito")
    monkeypatch.setattr(s, "flussonic_ec_quito_user", "admin")
    monkeypatch.setattr(s, "flussonic_ec_quito_password", "x")
    fc._node_clients.clear()
    c = fc.get_flussonic_node_client("ec-quito")
    assert c is not None and c.is_configured
    fc._node_clients.clear()


def test_ec_quito_client_unconfigured_returns_none(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "flussonic_ec_quito_base_url", "")
    monkeypatch.setattr(s, "flussonic_ec_quito_user", "")
    monkeypatch.setattr(s, "flussonic_ec_quito_password", "")
    fc._node_clients.clear()
    c = fc.get_flussonic_node_client("ec-quito")
    # missing base/creds → not configured (client may exist but is_configured False)
    assert c is None or not c.is_configured
    fc._node_clients.clear()


def test_unknown_node_still_none():
    fc._node_clients.clear()
    assert fc.get_flussonic_node_client("rogue-node") is None
