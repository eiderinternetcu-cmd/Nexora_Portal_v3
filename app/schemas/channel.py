import uuid
from datetime import datetime
from pydantic import BaseModel, ConfigDict


class ChannelPublic(BaseModel):
    """Client-facing — stream_key is NOT exposed."""
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    channel_key: str
    number: int
    name: str
    category: str | None = None
    logo_url: str | None = None
    requires_subscription: bool


class ChannelAdminOut(BaseModel):
    """Full channel detail for admin/reseller."""
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    channel_key: str
    number: int
    name: str
    category: str | None
    logo_url: str | None
    stream_key: str
    source_type: str
    source_url: str | None
    epg_id: str | None
    is_active: bool
    requires_subscription: bool
    created_at: datetime
    updated_at: datetime
