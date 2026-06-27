import uuid
from datetime import datetime, timezone
from sqlalchemy import Boolean, DateTime, ForeignKey, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.dialects.postgresql import UUID
from app.database import Base


class PlanChannel(Base):
    """Entitlement join: which channels a plan includes.

    A channel is watchable by a subscriber only if there is a
    plan_channels row (is_enabled=True) for the subscriber's active plan.
    """
    __tablename__ = "plan_channels"
    __table_args__ = (
        UniqueConstraint("plan_id", "channel_id", name="uq_plan_channels_plan_channel"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    plan_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("plans.id", ondelete="CASCADE"),
        nullable=False, index=True
    )
    channel_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("channels.id", ondelete="CASCADE"),
        nullable=False, index=True
    )
    is_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    created_by_admin_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )

    plan: Mapped["Plan"] = relationship("Plan", back_populates="plan_channels")
    channel: Mapped["Channel"] = relationship("Channel", back_populates="plan_channels")

    def __repr__(self) -> str:
        return f"<PlanChannel plan={self.plan_id} channel={self.channel_id} enabled={self.is_enabled}>"
