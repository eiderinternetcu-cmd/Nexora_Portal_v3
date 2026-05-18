import uuid
from pydantic import BaseModel, Field


class PlayRequest(BaseModel):
    """Request body for POST /api/stb/auth/play.

    device_id: external string identifier (MAC address, android_id, or app-generated UUID).
    Matches Device.device_id column, consistent with DeviceHeartbeat.
    """
    device_id: str = Field(..., min_length=6, max_length=128)
    subscriber_id: uuid.UUID
    channel_id: str | None = Field(None, max_length=128)


class PlaybackTokenOut(BaseModel):
    token: str
    expires_in: int          # seconds
    subscriber_id: str
    device_id: str           # internal Device.id UUID
    channel_id: str | None = None


class ValidateRequest(BaseModel):
    """Flussonic backend-auth payload.

    Flussonic hits /api/stb/auth/validate with the token it received from the player.
    subscriber_id is optional context — validation relies on the JWT + Redis key.
    """
    token: str
    subscriber_id: uuid.UUID | None = None


class ValidateResponse(BaseModel):
    valid: bool
    subscriber_id: str | None = None
    device_id: str | None = None      # internal Device.id UUID
    channel_id: str | None = None
    expires_at: int | None = None     # unix timestamp


class TokenRequest(BaseModel):
    """Reissue a playback token for a device already in the active ZSET."""
    device_id: str = Field(..., min_length=6, max_length=128)
    subscriber_id: uuid.UUID
    channel_id: str | None = Field(None, max_length=128)
