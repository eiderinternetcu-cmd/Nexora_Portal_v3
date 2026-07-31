import uuid
from datetime import datetime, timezone
from sqlalchemy import String, DateTime, ForeignKey, Boolean
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.dialects.postgresql import UUID
from app.database import Base


class Session(Base):
    """
    Sesión de suscriptor IPTV.
    - Una sesión por dispositivo activo.
    - Controla conexiones concurrentes.
    - Permite logout real y auditoría de acceso.
    """
    __tablename__ = "sessions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    subscriber_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("subscribers.id", ondelete="CASCADE"),
        nullable=False, index=True
    )
    device_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("devices.id", ondelete="SET NULL"), index=True
    )
    device_fingerprint: Mapped[str | None] = mapped_column(String(128), index=True)

    access_token_jti: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    refresh_token_jti: Mapped[str | None] = mapped_column(String(64), index=True)

    ip_address: Mapped[str | None] = mapped_column(String(45))
    user_agent: Mapped[str | None] = mapped_column(String(512))

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    last_heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)

    def __repr__(self) -> str:
        return f"<Session sub={self.subscriber_id} jti={self.access_token_jti[:8]}...>"


# ── Application-side caps ────────────────────────────────────────────────────
# Same pattern as app/models/audit.py: SessionService.create_iptv_session wrote
# ip_address/user_agent (both client-supplied — a header and get_client_ip())
# straight onto the model with no cap of its own. sessions.user_agent is
# varchar(512) in both migration 002 and this file, so
# tests/test_migration_schema_parity.py is structurally blind to this — ORM and
# migration agree, the divergence is between the column and what the service
# actually wrote. A User-Agent over 512 chars (routine for Android TV / STB
# clients — the same population that forced os_version from 32 to 512 in
# app/schemas/client.py) hits StringDataRightTruncation -> 500 on
# /api/stb/auth/play and /api/client/playback/authorize.
#
# One definition each, applied centrally in SessionService (the two entry
# points that persist client-supplied ip/user_agent), and _assert_fits below
# fails LOUDLY at import if a cap ever outgrows its column.
SESSION_USER_AGENT_MAX_LEN = 512
SESSION_IP_ADDRESS_MAX_LEN = 45  # max INET6_ADDRSTRLEN with a scope id


def _assert_fits(column_name: str, cap: int) -> None:
    """Fail at import if a cap could no longer fit its column."""
    length = getattr(Session.__table__.c[column_name].type, "length", None)
    if length is not None and cap > length:
        raise RuntimeError(
            f"sessions.{column_name} holds {length} chars but the application "
            f"cap is {cap}: values between {length + 1} and {cap} would pass "
            "truncation and then fail the INSERT. Align the column (migration) "
            "and the cap in app/models/session.py."
        )


_assert_fits("user_agent", SESSION_USER_AGENT_MAX_LEN)
_assert_fits("ip_address", SESSION_IP_ADDRESS_MAX_LEN)
