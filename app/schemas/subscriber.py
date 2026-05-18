import uuid
from datetime import datetime
from pydantic import BaseModel, EmailStr, Field, ConfigDict
from app.models.subscriber import SubscriberStatus


class SubscriberCreate(BaseModel):
    username: str = Field(..., min_length=3, max_length=64, pattern=r"^[a-zA-Z0-9_.\-]+$")
    password: str | None = Field(None, min_length=6, max_length=128)
    activation_code: str | None = Field(None, max_length=64)
    email: EmailStr | None = None
    phone: str | None = Field(None, max_length=32)
    full_name: str | None = Field(None, max_length=128)
    id_cedula: str | None = Field(None, max_length=32)
    notes: str | None = None


class SubscriberUpdate(BaseModel):
    email: EmailStr | None = None
    phone: str | None = Field(None, max_length=32)
    full_name: str | None = Field(None, max_length=128)
    id_cedula: str | None = Field(None, max_length=32)
    status: SubscriberStatus | None = None
    notes: str | None = None


class SubscriberPasswordChange(BaseModel):
    new_password: str = Field(..., min_length=6, max_length=128)


class SubscriberOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    username: str
    email: str | None
    phone: str | None
    full_name: str | None
    id_cedula: str | None
    status: SubscriberStatus
    notes: str | None
    created_at: datetime
    updated_at: datetime


class SubscriberOutFull(SubscriberOut):
    activation_code: str | None
    created_by: uuid.UUID | None
