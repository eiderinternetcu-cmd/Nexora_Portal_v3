import enum
import uuid
from datetime import datetime, timezone
from sqlalchemy import String, Boolean, Enum, DateTime, Text, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.dialects.postgresql import UUID
from app.database import Base


class SubscriberStatus(str, enum.Enum):
    active = "active"
    expired = "expired"
    suspended = "suspended"
    banned = "banned"


class Subscriber(Base):
    __tablename__ = "subscribers"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    username: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    password_hash: Mapped[str | None] = mapped_column(String(255))
    activation_code: Mapped[str | None] = mapped_column(String(64), unique=True, index=True)

    # NX-PARENTAL — Argon2id hash of this subscriber's parental PIN (4-6 digits).
    # NULL = no PIN configured yet, which the gate treats as "blocked", not
    # "open" (see app/services/parental_service.py for why).
    # Per SUBSCRIBER, not global and not per device: it is the account holder's
    # secret, and the household shares the account across devices.
    # The plaintext is never stored, never logged and never returned by any
    # endpoint. Same width as password_hash — argon2 output is ~97 chars.
    parental_pin_hash: Mapped[str | None] = mapped_column(String(255))

    email: Mapped[str | None] = mapped_column(String(255), index=True)
    phone: Mapped[str | None] = mapped_column(String(32))
    full_name: Mapped[str | None] = mapped_column(String(128))
    id_cedula: Mapped[str | None] = mapped_column(String(32), index=True)

    status: Mapped[SubscriberStatus] = mapped_column(
        Enum(SubscriberStatus), nullable=False, default=SubscriberStatus.active, index=True
    )
    notes: Mapped[str | None] = mapped_column(Text)

    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    # Relationships
    subscriptions: Mapped[list["Subscription"]] = relationship(
        "Subscription", back_populates="subscriber", lazy="select"
    )
    devices: Mapped[list["Device"]] = relationship(
        "Device", back_populates="subscriber", lazy="select"
    )

    def __repr__(self) -> str:
        return f"<Subscriber {self.username} ({self.status})>"
