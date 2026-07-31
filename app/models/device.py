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
    # MAC address para dispositivos físicos; android_id o UUID para apps.
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

    # ── Strong identity (M1) ──────────────────────────────────────────────────
    # High-entropy secret issued at registration; only its keyed hash is stored.
    device_secret_hash: Mapped[str | None] = mapped_column(String(255))
    # 'active' (default / legacy auto-register) | 'pending' (awaiting secret
    # activation when DEVICE_SECRET_ENFORCE is on) | 'revoked'.
    status: Mapped[str] = mapped_column(String(16), default="active", nullable=False, index=True)

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


# ── Application-side caps ────────────────────────────────────────────────────
# Same pattern as app/models/audit.py and app/models/session.py.
# DeviceService.register()/heartbeat() write last_ip=ip straight onto the model.
# `ip` is app.core.dependencies.get_client_ip(request), which under the default
# CLIENT_IP_SOURCE=legacy returns the raw X-Forwarded-For header value with no
# length bound — the same class of client-controlled string that overflowed
# audit_logs.user_agent. devices.last_ip is varchar(45); nothing capped it
# before the INSERT.
DEVICE_LAST_IP_MAX_LEN = 45  # max INET6_ADDRSTRLEN with a scope id


def _assert_fits(column_name: str, cap: int) -> None:
    """Fail at import if a cap could no longer fit its column."""
    length = getattr(Device.__table__.c[column_name].type, "length", None)
    if length is not None and cap > length:
        raise RuntimeError(
            f"devices.{column_name} holds {length} chars but the application "
            f"cap is {cap}: values between {length + 1} and {cap} would pass "
            "truncation and then fail the INSERT. Align the column (migration) "
            "and the cap in app/models/device.py."
        )


_assert_fits("last_ip", DEVICE_LAST_IP_MAX_LEN)
