import uuid
from datetime import datetime, timezone
from pydantic import BaseModel, model_validator


class SessionOut(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    subscriber_id: uuid.UUID
    device_id: uuid.UUID | None = None
    device_fingerprint: str | None = None
    ip_address: str | None = None
    user_agent: str | None = None
    created_at: datetime
    expires_at: datetime
    last_heartbeat_at: datetime | None = None
    revoked_at: datetime | None = None
    is_active: bool = False

    @model_validator(mode="after")
    def _compute_is_active(self) -> "SessionOut":
        now = datetime.now(timezone.utc)
        exp = self.expires_at
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=timezone.utc)
        self.is_active = self.revoked_at is None and exp > now
        return self


class SessionRevoke(BaseModel):
    access_jti: str
