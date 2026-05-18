import uuid
from datetime import datetime
from decimal import Decimal
from pydantic import BaseModel, Field, ConfigDict


class PlanCreate(BaseModel):
    name: str = Field(..., min_length=2, max_length=128)
    description: str | None = None
    max_connections: int = Field(1, ge=1, le=100)
    max_devices: int = Field(2, ge=1, le=50)
    duration_days: int = Field(30, ge=1)
    price: Decimal | None = Field(None, ge=0)
    notes: str | None = None


class PlanUpdate(BaseModel):
    name: str | None = Field(None, min_length=2, max_length=128)
    description: str | None = None
    max_connections: int | None = Field(None, ge=1, le=100)
    max_devices: int | None = Field(None, ge=1, le=50)
    duration_days: int | None = Field(None, ge=1)
    price: Decimal | None = None
    is_active: bool | None = None
    notes: str | None = None


class PlanOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    description: str | None
    max_connections: int
    max_devices: int
    duration_days: int
    price: Decimal | None
    is_active: bool
    notes: str | None
    created_at: datetime
