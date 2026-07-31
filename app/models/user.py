import enum
import uuid
from datetime import datetime, timezone
from sqlalchemy import String, Boolean, Enum, DateTime, Text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.dialects.postgresql import UUID
from app.database import Base


class UserRole(str, enum.Enum):
    admin = "admin"
    reseller = "reseller"


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    username: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    full_name: Mapped[str | None] = mapped_column(String(128))
    role: Mapped[UserRole] = mapped_column(Enum(UserRole), nullable=False, default=UserRole.reseller)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_login_ip: Mapped[str | None] = mapped_column(String(45))

    def __repr__(self) -> str:
        return f"<User {self.username} ({self.role})>"


# ── Application-side caps ────────────────────────────────────────────────────
# Same pattern as app/models/audit.py, app/models/session.py, app/models/device.py.
# AuthService.login() writes last_login_ip=ip straight into an UPDATE .values().
# `ip` is app.core.dependencies.get_client_ip(request), which under the default
# CLIENT_IP_SOURCE=legacy returns the raw X-Forwarded-For header value with no
# length bound. users.last_login_ip is varchar(45); nothing capped it before the
# UPDATE.
USER_LAST_LOGIN_IP_MAX_LEN = 45  # max INET6_ADDRSTRLEN with a scope id


def _assert_fits(column_name: str, cap: int) -> None:
    """Fail at import if a cap could no longer fit its column."""
    length = getattr(User.__table__.c[column_name].type, "length", None)
    if length is not None and cap > length:
        raise RuntimeError(
            f"users.{column_name} holds {length} chars but the application cap "
            f"is {cap}: values between {length + 1} and {cap} would pass "
            "truncation and then fail the UPDATE. Align the column (migration) "
            "and the cap in app/models/user.py."
        )


_assert_fits("last_login_ip", USER_LAST_LOGIN_IP_MAX_LEN)
