"""M1 — strong device identity (device secret, flag-gated).

Flag off (default): new devices are 'active' and issued a secret (no behavior
change for playback). Flag on: new devices are 'pending' and playback is denied
until the device is activated with its secret.
"""
import pytest

from app.core.exceptions import NexoraException
from app.core.security import verify_device_secret
from app.services.device_service import DeviceService
from app.services import device_service as dev_mod
from app.services.stream_auth_service import StreamAuthService
from app.schemas.device import DeviceRegister

pytestmark = pytest.mark.asyncio


async def _register(world, redis, device_id="dev-secret-1"):
    svc = DeviceService(world["session"], redis)
    dev = await svc.register(
        world["subscriber"].id,
        DeviceRegister(device_id=device_id, device_type="web_player"),
        ip="1.2.3.4",
    )
    await world["session"].commit()
    return svc, dev


async def test_register_issues_secret_and_stores_only_hash(entitlement_world, redis_client):
    svc, dev = await _register(entitlement_world, redis_client)
    secret = getattr(dev, "plaintext_secret", None)
    assert secret and len(secret) >= 16
    assert dev.device_secret_hash and dev.device_secret_hash != secret  # only the hash is stored
    assert verify_device_secret(secret, dev.device_secret_hash) is True
    assert verify_device_secret("wrong-secret", dev.device_secret_hash) is False
    assert dev.status == "active"  # flag off → active (legacy behavior)


async def test_verify_and_activate(entitlement_world, redis_client):
    svc, dev = await _register(entitlement_world, redis_client, device_id="dev-secret-2")
    secret = dev.plaintext_secret
    assert await svc.verify_secret("dev-secret-2", secret) is True
    assert await svc.verify_secret("dev-secret-2", "nope") is False
    with pytest.raises(NexoraException) as ei:
        await svc.activate_with_secret("dev-secret-2", "nope")
    assert ei.value.status_code == 403
    activated = await svc.activate_with_secret("dev-secret-2", secret)
    assert activated.status == "active"


async def test_enforce_new_device_pending_blocks_playback_then_activates(
    entitlement_world, redis_client, monkeypatch
):
    monkeypatch.setattr(dev_mod.settings, "device_secret_enforce", True)
    svc, dev = await _register(entitlement_world, redis_client, device_id="dev-secret-3")
    assert dev.status == "pending"
    secret = dev.plaintext_secret

    auth = StreamAuthService(entitlement_world["session"], redis_client)
    # Pending device → playback device load denied.
    with pytest.raises(NexoraException) as ei:
        await auth._load_device("dev-secret-3")
    assert ei.value.status_code == 403 and ei.value.detail == "DEVICE_NOT_ACTIVATED"

    # Activate with the secret → playback device load now passes.
    await svc.activate_with_secret("dev-secret-3", secret)
    await entitlement_world["session"].commit()
    loaded = await auth._load_device("dev-secret-3")
    assert loaded.status == "active"


async def test_enforce_off_default_does_not_block(entitlement_world, redis_client):
    # The seed device from the fixture is 'active'; playback load must pass.
    auth = StreamAuthService(entitlement_world["session"], redis_client)
    loaded = await auth._load_device(entitlement_world["device"].device_id)
    assert loaded.device_id == entitlement_world["device"].device_id
