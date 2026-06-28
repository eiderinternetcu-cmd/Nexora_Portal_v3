import uuid
from datetime import datetime, timezone
from sqlalchemy import String, Boolean, Integer, DateTime
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.dialects.postgresql import UUID
from app.database import Base


class Channel(Base):
    __tablename__ = "channels"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    channel_key: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    number: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    category: Mapped[str | None] = mapped_column(String(64))
    logo_url: Mapped[str | None] = mapped_column(String(512))
    stream_key: Mapped[str] = mapped_column(String(128), nullable=False)
    flussonic_node: Mapped[str] = mapped_column(String(64), nullable=False, default="ec-main", index=True)
    hls_path: Mapped[str] = mapped_column(String(32), nullable=False, default="index.m3u8")
    source_type: Mapped[str] = mapped_column(String(16), nullable=False, default="manual")
    source_url: Mapped[str | None] = mapped_column(String(512))
    epg_id: Mapped[str | None] = mapped_column(String(128))
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, index=True)
    requires_subscription: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    plan_channels: Mapped[list["PlanChannel"]] = relationship(
        "PlanChannel", back_populates="channel", lazy="select", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<Channel {self.channel_key} #{self.number} active={self.is_active}>"
