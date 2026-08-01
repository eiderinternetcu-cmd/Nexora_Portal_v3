"""Client parental-control endpoints (NX-PARENTAL).

The PIN is set, changed and presented HERE and nowhere else. Playback only ever
checks the short-lived unlock grant this surface leaves behind — see the
ParentalService module docstring for why the PIN does not travel on /authorize.

These three routes work whether or not PARENTAL_CONTROL_ENFORCE is on: they
change nothing about playback or the catalog by themselves, and having them live
while the flag is still off is what lets subscribers configure a PIN *before*
the gate starts refusing censored channels.
"""
from fastapi import APIRouter, Depends
import redis.asyncio as aioredis
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import get_db
from app.redis_client import get_redis
from app.core.dependencies import get_current_subscriber
from app.core.exceptions import not_found, forbidden
from app.models.subscriber import Subscriber
from app.schemas.client import (
    ParentalPinSetRequest,
    ParentalStatusResponse,
    ParentalUnlockRequest,
    ParentalUnlockResponse,
)
from app.services.device_service import DeviceService
from app.services.parental_service import ParentalService

router = APIRouter(prefix="/parental", tags=["Client Parental Control"])

settings = get_settings()


def _status(subscriber: Subscriber) -> ParentalStatusResponse:
    return ParentalStatusResponse(
        enforced=settings.parental_control_enforce,
        pin_set=subscriber.parental_pin_hash is not None,
        unlock_ttl_seconds=settings.parental_pin_unlock_ttl_seconds,
    )


@router.get("", response_model=ParentalStatusResponse)
async def get_parental_status(
    subscriber: Subscriber = Depends(get_current_subscriber),
):
    """Whether the gate is live and whether this account has a PIN configured.

    Returns booleans about the caller's own account — never the PIN, never its
    hash, and nothing an attacker could not already infer from the deny codes
    the playback routes return.
    """
    return _status(subscriber)


@router.put("/pin", response_model=ParentalStatusResponse)
async def set_parental_pin(
    data: ParentalPinSetRequest,
    subscriber: Subscriber = Depends(get_current_subscriber),
    db: AsyncSession = Depends(get_db),
    redis: aioredis.Redis = Depends(get_redis),
):
    """Set the PIN for the first time, or change it.

    First set: no `current_pin` — the client token already proves the account
    credential. Change: `current_pin` is required and verified, and a wrong one
    counts toward the same limiter as a failed unlock. See ParentalService.set_pin.

    NX-PARENTAL — deliberate absence of a self-service RESET. A "forgot my PIN"
    route guarded only by the client access token would undo the change-requires-
    the-old-PIN rule above by another name: whoever holds a logged-in device
    could clear the PIN and walk in. The same reasoning already keeps device
    secret re-issue off this surface (app/api/client/profile.py). Recovery
    belongs to an out-of-band operator action, not to a bearer token.
    """
    await ParentalService(db, redis).set_pin(
        subscriber, new_pin=data.new_pin, current_pin=data.current_pin
    )
    await db.commit()
    return _status(subscriber)


@router.post("/unlock", response_model=ParentalUnlockResponse)
async def unlock_parental(
    data: ParentalUnlockRequest,
    subscriber: Subscriber = Depends(get_current_subscriber),
    db: AsyncSession = Depends(get_db),
    redis: aioredis.Redis = Depends(get_redis),
):
    """Verify the PIN and open the unlock window for ONE device.

    Wrong PIN → 403 (counted). Too many wrong PINs → 423 with the remaining
    window. No PIN configured → 403 PARENTAL_PIN_NOT_SET.

    The device is checked to belong to the caller, mirroring the other
    device-scoped client routes: the grant key is built from the device id, so
    this keeps the keyspace to real devices instead of to arbitrary strings.
    """
    device = await DeviceService(db, redis).get_by_device_id(data.device_id)
    if device is None:
        raise not_found("Device not registered")
    if device.subscriber_id != subscriber.id:
        raise forbidden("Device does not belong to this subscriber")

    ttl = await ParentalService(db, redis).unlock(subscriber, data.device_id, data.pin)
    return ParentalUnlockResponse(expires_in=ttl)
