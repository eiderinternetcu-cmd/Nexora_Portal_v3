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
    os_version: str | None = Field(None, max_length=32)


class ClientTokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int
    subscriber_id: str


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
    os_version: str | None = Field(None, max_length=32)
