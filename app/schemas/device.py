import uuid
from datetime import datetime
from pydantic import BaseModel, Field, ConfigDict, field_validator

# Width of devices.os_version in the DB (String(32)). Keep in sync with the model:
# a longer value would blow up on INSERT/UPDATE, so it is truncated here instead.
OS_VERSION_MAX = 32


class DeviceRegister(BaseModel):
    """Internal DTO consumed by DeviceService.register().

    Field limits mirror the `devices` column widths exactly, EXCEPT `os_version`,
    which is truncated instead of rejected (see the validator below). Identity
    fields (`device_id`, `mac_address`) are never truncated — a silently mangled
    identifier is far worse than a 422.
    """

    device_id: str = Field(..., min_length=6, max_length=128)
    mac_address: str | None = Field(None, max_length=32)
    model: str | None = Field(None, max_length=128)
    brand: str | None = Field(None, max_length=64)
    device_type: str | None = Field(None, max_length=32)
    app_version: str | None = Field(None, max_length=32)
    os_version: str | None = Field(None, max_length=OS_VERSION_MAX)
    user_agent: str | None = None

    @field_validator("os_version", mode="before")
    @classmethod
    def _truncate_os_version(cls, v):
        """Public schemas accept os_version up to 512 chars (browsers send
        navigator.userAgent here, which routinely exceeds 32). The column is
        String(32), so a long value used to pass input validation and then blow
        up building this DTO → 500. os_version is diagnostic metadata, not an
        identifier, and the full string is still preserved in
        `devices.user_agent` (Text) on the login path — truncating is lossless
        where it matters and avoids an Alembic migration to widen the column."""
        if isinstance(v, str):
            return v[:OS_VERSION_MAX]
        return v


class DeviceHeartbeat(BaseModel):
    device_id: str = Field(..., min_length=6, max_length=128)
    app_version: str | None = Field(None, max_length=32)


class DeviceBlockRequest(BaseModel):
    reason: str | None = Field(None, max_length=255)


class DeviceActivateRequest(BaseModel):
    device_id: str = Field(..., min_length=6, max_length=128)
    device_secret: str = Field(..., min_length=16, max_length=128)


class DeviceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    subscriber_id: uuid.UUID
    device_id: str
    mac_address: str | None
    model: str | None
    brand: str | None
    device_type: str | None
    app_version: str | None
    os_version: str | None
    last_ip: str | None
    status: str
    is_blocked: bool
    block_reason: str | None
    last_seen_at: datetime | None
    registered_at: datetime


class DeviceRegisterResponse(DeviceOut):
    # Present ONLY on a fresh registration — the plaintext device secret, shown once.
    device_secret: str | None = None
