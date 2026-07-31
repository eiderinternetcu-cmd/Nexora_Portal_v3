"""NX-NODE — node binding and node allowlist on the /stream/* gate.

Two independent holes in `StreamAuthService.validate_stream_request`:

  1. The 'node' claim failed OPEN while 'sk' failed CLOSED. A token with no
     'node' claim was valid on EVERY node.
  2. The node arrives as a PATH SEGMENT of /stream/<node>/<stream_key>/… — pure
     caller input — and was never checked against any list of real nodes.

Both closures are behind flags whose defaults reproduce the old behavior, so the
"legacy" tests below are the guard on that default, and the "enforce" ones are
the proof that the hole is shut.
"""
import pytest

from app.core.exceptions import NexoraException
from app.services.stream_auth_service import StreamAuthService
from app.services import stream_auth_service as sas_mod

pytestmark = pytest.mark.asyncio

IP = "1.2.3.4"
NODE = "co-main"
OTHER_NODE = "ec-main"


async def _authorize(world, redis, *, stream_key="K1", node=NODE, channel_key="canal-1"):
    """Mint a real playback token. node=None reproduces the emitters that existed
    before this branch (STB /play and /token never resolved the node)."""
    svc = StreamAuthService(world["session"], redis)
    res = await svc.authorize(
        subscriber_id=world["subscriber"].id,
        device_id_str=world["device"].device_id,
        channel_id=stream_key,
        ip=IP,
        channel_key=channel_key,
        node=node,
    )
    return svc, res


# ── 1. node claim: fail open (legacy) vs fail closed (enforced) ───────────────

async def test_nodeless_token_passes_on_any_node_by_default(entitlement_world, redis_client):
    """The hole itself. Default flags = today: a token minted WITHOUT a node
    claim is accepted on a node it was never issued for."""
    svc, res = await _authorize(entitlement_world, redis_client, node=None)
    out = await svc.validate_stream_request(
        res.token, stream_key="K1", node=OTHER_NODE, client_ip=IP
    )
    assert out["node"] is None


async def test_nodeless_token_rejected_when_binding_enforced(
    entitlement_world, redis_client, monkeypatch
):
    """The hole closed. Same token, same request, flag on → 403 like a wrong 'sk'.

    Reverting the fail-closed branch in validate_stream_request makes this fail:
    the legacy `not in (None, node)` tolerance returns the payload instead.
    """
    monkeypatch.setattr(sas_mod.settings, "playback_node_binding_enforce", True)
    svc, res = await _authorize(entitlement_world, redis_client, node=None)
    with pytest.raises(NexoraException) as ei:
        await svc.validate_stream_request(
            res.token, stream_key="K1", node=OTHER_NODE, client_ip=IP
        )
    assert ei.value.status_code == 403


async def test_node_bound_token_still_valid_on_its_own_node_when_enforced(
    entitlement_world, redis_client, monkeypatch
):
    """No false positive: enforcement must not break the tokens actually issued
    today, which all carry their node."""
    monkeypatch.setattr(sas_mod.settings, "playback_node_binding_enforce", True)
    svc, res = await _authorize(entitlement_world, redis_client, node=NODE)
    out = await svc.validate_stream_request(
        res.token, stream_key="K1", node=NODE, client_ip=IP
    )
    assert out["node"] == NODE


@pytest.mark.parametrize("enforce", [False, True])
async def test_wrong_node_rejected_in_both_modes(
    entitlement_world, redis_client, monkeypatch, enforce
):
    """A token that DOES name a node was already bound in both modes — the flag
    only changes what happens when the claim is absent."""
    monkeypatch.setattr(sas_mod.settings, "playback_node_binding_enforce", enforce)
    svc, res = await _authorize(entitlement_world, redis_client, node=NODE)
    with pytest.raises(NexoraException) as ei:
        await svc.validate_stream_request(
            res.token, stream_key="K1", node=OTHER_NODE, client_ip=IP
        )
    assert ei.value.status_code == 403


async def test_nodeless_token_is_already_sk_less_in_practice(entitlement_world, redis_client):
    """Why closing the asymmetry is expected to be a no-op for real traffic.

    Every emitter in the tree sets 'sk' and 'node' together or neither, so a
    node-less token is also stream_key-less — and the stream_key check one line
    earlier already refuses it. Enforcement changes the REASON, not the outcome.
    """
    svc, res = await _authorize(entitlement_world, redis_client, stream_key=None, node=None,
                                channel_key=None)
    with pytest.raises(NexoraException) as ei:
        await svc.validate_stream_request(
            res.token, stream_key="K1", node=NODE, client_ip=IP
        )
    assert ei.value.status_code == 403


# ── 2. node allowlist ────────────────────────────────────────────────────────

async def test_unknown_node_rejected_by_allowlist(entitlement_world, redis_client, monkeypatch):
    """A node id that is not ours never reaches token evaluation.

    Reverting the allowlist guard makes this fail: without it the request is
    evaluated normally and the token's node mismatch is what answers (or, with a
    matching token, nothing answers at all).
    """
    monkeypatch.setattr(sas_mod.settings, "playback_node_allowlist", f"{NODE},{OTHER_NODE}")
    svc, res = await _authorize(entitlement_world, redis_client, node=NODE)
    with pytest.raises(NexoraException) as ei:
        await svc.validate_stream_request(
            res.token, stream_key="K1", node="../../evil", client_ip=IP
        )
    assert ei.value.status_code == 403
    assert ei.value.detail == "Unknown stream node"


async def test_allowlist_runs_before_the_token_is_decoded(
    entitlement_world, redis_client, monkeypatch
):
    """'should not even get evaluated': an unknown node wins over a garbage
    token, which would otherwise answer 401 from _decode_jwt."""
    monkeypatch.setattr(sas_mod.settings, "playback_node_allowlist", NODE)
    svc = StreamAuthService(entitlement_world["session"], redis_client)
    with pytest.raises(NexoraException) as ei:
        await svc.validate_stream_request(
            "not.a.jwt", stream_key="K1", node="tc-mia", client_ip=IP
        )
    assert ei.value.status_code == 403
    assert ei.value.detail == "Unknown stream node"


async def test_listed_node_passes_allowlist(entitlement_world, redis_client, monkeypatch):
    monkeypatch.setattr(sas_mod.settings, "playback_node_allowlist", f" {NODE} , {OTHER_NODE} ")
    svc, res = await _authorize(entitlement_world, redis_client, node=NODE)
    out = await svc.validate_stream_request(
        res.token, stream_key="K1", node=NODE, client_ip=IP
    )
    assert out["node"] == NODE


async def test_empty_allowlist_is_off_by_default(entitlement_world, redis_client):
    """Default = no validation, so an operator adding a node to nginx before
    adding it here does not get a silent outage."""
    assert sas_mod.settings.playback_allowed_nodes == set()
    svc, res = await _authorize(entitlement_world, redis_client, node="tc-mia")
    out = await svc.validate_stream_request(
        res.token, stream_key="K1", node="tc-mia", client_ip=IP
    )
    assert out["node"] == "tc-mia"
