import uuid
from datetime import datetime
from pydantic import BaseModel, Field


class ClientLoginRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=64)
    password: str | None = Field(None, min_length=1, max_length=128)
    activation_code: str | None = Field(None, min_length=1, max_length=64)
    device_id: str = Field(..., min_length=6, max_length=128)
    device_type: str | None = Field(None, max_length=32)
    model: str | None = Field(None, max_length=128)
    brand: str | None = Field(None, max_length=64)
    app_version: str | None = Field(None, max_length=32)
    os_version: str | None = Field(None, max_length=512)


class ClientTokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int
    subscriber_id: str
    # 'registered' | 'limit_reached' — login never fails on device cap (P0-003)
    device_registration: str | None = None
    # NX-DEV: plaintext device secret, delivered EXACTLY ONCE — on the login that
    # actually creates the device row. Always None on refresh, on any later login
    # with the same device_id (the plaintext no longer exists anywhere), and when
    # device_registration == 'limit_reached' (no device was created). Clients must
    # persist it at first login; it is the credential for POST
    # /profile/devices/activate and cannot be re-issued.
    device_secret: str | None = None


class ClientRefreshRequest(BaseModel):
    refresh_token: str


class ClientLogoutRequest(BaseModel):
    refresh_token: str | None = None


class ClientProfileResponse(BaseModel):
    subscriber_id: str
    username: str
    full_name: str | None
    email: str | None
    status: str
    subscription_expires_at: datetime | None
    max_connections: int
    max_devices: int
    device_count: int
    days_remaining: int | None


class Channel(BaseModel):
    id: str
    name: str
    category: str
    logo_url: str | None = None
    is_hd: bool = True


class EpgEntry(BaseModel):
    channel_id: str
    title: str
    description: str | None = None
    start_at: datetime
    end_at: datetime


class PlaybackAuthorizeRequest(BaseModel):
    device_id: str = Field(..., min_length=6, max_length=128)
    channel_id: str | None = Field(None, max_length=128)


class PlaybackResponse(BaseModel):
    token: str
    expires_in: int
    channel_id: str | None = None
    subscriber_id: str
    playback_url: str | None = None  # Direct HLS URL when source_url is configured on the channel


# ── Parental control (NX-PARENTAL) ──────────────────────────────────────────
# The PIN is 4-6 DIGITS, pinned by pattern here so a malformed value is rejected
# before it reaches an Argon2id verification (and before it can consume an
# attempt). No response model ever carries a PIN, in any form.

_PIN_PATTERN = r"^\d{4,6}$"


class ParentalStatusResponse(BaseModel):
    """What the client needs to decide between "set a PIN" and "ask for it".

    `pin_set` is a boolean about the caller's own account — not a secret, and
    already inferable from the deny codes at playback.
    """
    enforced: bool          # PARENTAL_CONTROL_ENFORCE — is the gate live at all
    pin_set: bool
    unlock_ttl_seconds: int


class ParentalPinSetRequest(BaseModel):
    """Set or change the PIN. `current_pin` is required only when one exists —
    see ParentalService.set_pin for why the change path demands it."""
    new_pin: str = Field(..., pattern=_PIN_PATTERN)
    current_pin: str | None = Field(None, pattern=_PIN_PATTERN)


class ParentalUnlockRequest(BaseModel):
    """The one place a PIN is accepted. Deliberately NOT a field on
    PlaybackAuthorizeRequest — see the ParentalService module docstring."""
    device_id: str = Field(..., min_length=6, max_length=128)
    pin: str = Field(..., pattern=_PIN_PATTERN)


class ParentalUnlockResponse(BaseModel):
    expires_in: int         # unlock grant TTL, seconds (slides while in use)


class ClientHeartbeatRequest(BaseModel):
    device_id: str = Field(..., min_length=6, max_length=128)
    app_version: str | None = Field(None, max_length=32)


class ClientDeviceRegister(BaseModel):
    device_id: str = Field(..., min_length=6, max_length=128)
    mac_address: str | None = Field(None, max_length=32)
    model: str | None = Field(None, max_length=128)
    brand: str | None = Field(None, max_length=64)
    device_type: str | None = Field(None, max_length=32)
    app_version: str | None = Field(None, max_length=32)
    os_version: str | None = Field(None, max_length=512)
