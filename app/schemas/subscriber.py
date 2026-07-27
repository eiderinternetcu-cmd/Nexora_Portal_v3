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

    # Enriquecimiento del panel (join con la suscripcion ACTIVA + su plan + owner).
    # Tienen default None para que model_validate() sobre un Subscriber sin estos
    # atributos calculados (p.ej. la respuesta de create/update) no falle: el
    # listado y el detalle los rellenan, el resto los deja en null. Aditivo.
    subscription_expires_at: datetime | None = None
    plan_name: str | None = None
    days_remaining: int | None = None
    owner_username: str | None = None


class SubscriberOutFull(SubscriberOut):
    activation_code: str | None
    created_by: uuid.UUID | None
