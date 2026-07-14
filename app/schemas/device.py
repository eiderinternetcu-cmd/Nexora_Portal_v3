import uuid
from datetime import datetime
from pydantic import BaseModel, Field, ConfigDict


class DeviceRegister(BaseModel):
    device_id: str = Field(..., min_length=6, max_length=128)
    mac_address: str | None = Field(None, max_length=32)
    model: str | None = Field(None, max_length=128)
    brand: str | None = Field(None, max_length=64)
    device_type: str | None = Field(None, max_length=32)
    app_version: str | None = Field(None, max_length=32)
    os_version: str | None = Field(None, max_length=32)
    user_agent: str | None = None


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
