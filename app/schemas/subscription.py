import uuid
from datetime import datetime
from pydantic import BaseModel, Field, ConfigDict
from app.schemas.plan import PlanOut


class SubscriptionCreate(BaseModel):
    subscriber_id: uuid.UUID
    plan_id: uuid.UUID
    starts_at: datetime | None = None   # defaults to now
    renewal_note: str | None = Field(None, max_length=255)


class SubscriptionAdminCreate(BaseModel):
    """Body for POST /api/admin/subscribers/{sub_id}/subscriptions.
    subscriber_id comes from the URL path, not the body.
    """
    plan_id: uuid.UUID
    renewal_note: str | None = Field(None, max_length=255)


class SubscriptionRenew(BaseModel):
    plan_id: uuid.UUID | None = None    # keep current plan if omitted
    renewal_note: str | None = Field(None, max_length=255)


class SubscriptionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    subscriber_id: uuid.UUID
    plan_id: uuid.UUID
    starts_at: datetime
    expires_at: datetime
    is_active: bool
    renewal_note: str | None
    created_at: datetime
    plan: PlanOut | None = None


class SubscriberActiveStatus(BaseModel):
    subscriber_id: uuid.UUID
    username: str
    is_active: bool
    subscription_expires_at: datetime | None
    max_connections: int
    max_devices: int
    device_count: int
    days_remaining: int | None
