import uuid
from datetime import datetime, timezone
from sqlalchemy import String, Boolean, DateTime, ForeignKey, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.dialects.postgresql import UUID
from app.database import Base


class Device(Base):
    __tablename__ = "devices"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    subscriber_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("subscribers.id", ondelete="CASCADE"),
        nullable=False, index=True
    )

    # ── Primary identifier ────────────────────────────────────────────────────
    # Para STB/MAG: MAC address. Para apps: android_id o UUID generado por la app.
    device_id: Mapped[str] = mapped_column(String(128), unique=True, nullable=False, index=True)

    # ── Extended fingerprint ──────────────────────────────────────────────────
    mac_address: Mapped[str | None] = mapped_column(String(32), index=True)
    android_id: Mapped[str | None] = mapped_column(String(64), index=True)
    device_fingerprint: Mapped[str | None] = mapped_column(String(128), index=True)
    serial_hash: Mapped[str | None] = mapped_column(String(64))  # sha256 del serial, nunca el serial crudo

    # ── Hardware info ─────────────────────────────────────────────────────────
    model: Mapped[str | None] = mapped_column(String(128))
    brand: Mapped[str | None] = mapped_column(String(64))
    device_type: Mapped[str | None] = mapped_column(String(32))  # android_tv, android, ios, mag, web, stb

    # ── Software info ─────────────────────────────────────────────────────────
    app_version: Mapped[str | None] = mapped_column(String(32))
    os_version: Mapped[str | None] = mapped_column(String(32))
    user_agent: Mapped[str | None] = mapped_column(Text)

    # ── Network ───────────────────────────────────────────────────────────────
    last_ip: Mapped[str | None] = mapped_column(String(45))

    # ── State ─────────────────────────────────────────────────────────────────
    is_blocked: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, index=True)
    block_reason: Mapped[str | None] = mapped_column(String(255))

    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    registered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    subscriber: Mapped["Subscriber"] = relationship("Subscriber", back_populates="devices")

    def __repr__(self) -> str:
        return f"<Device {self.device_id[:20]}... type={self.device_type} blocked={self.is_blocked}>"
