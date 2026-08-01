import uuid
from datetime import datetime, timezone
from sqlalchemy import String, DateTime, ForeignKey, Text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.dialects.postgresql import UUID, JSONB
from app.database import Base


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    actor_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    actor_username: Mapped[str | None] = mapped_column(String(64))

    action: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    target_type: Mapped[str | None] = mapped_column(String(64), index=True)
    target_id: Mapped[str | None] = mapped_column(String(128), index=True)

    details: Mapped[dict | None] = mapped_column(JSONB)
    ip_address: Mapped[str | None] = mapped_column(String(45))
    user_agent: Mapped[str | None] = mapped_column(Text)

    # Part of the PRIMARY KEY, not decoration. Migration 011 partitions
    # audit_logs by RANGE (created_at), and Postgres requires every unique or
    # primary key on a partitioned table to contain all partition-key columns —
    # so PRIMARY KEY (id) cannot survive partitioning and becomes
    # PRIMARY KEY (id, created_at). Declared here so the ORM and the migrated
    # schema keep agreeing (tests/test_migration_schema_parity.py compares them).
    #
    # Consequence, recorded rather than glossed over: `id` on its own is no
    # longer ENFORCED unique, because Postgres cannot build a global unique index
    # that omits the partition key. It is an application-generated UUIDv4 and the
    # pair stays unique, so nothing in the codebase relies on what was lost —
    # nothing looks an audit row up by bare id.
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        primary_key=True,
        default=lambda: datetime.now(timezone.utc),
        index=True,
    )

    def __repr__(self) -> str:
        return f"<AuditLog {self.action} by {self.actor_username}>"


# ── Application-side caps ────────────────────────────────────────────────────
# These live next to the model ON PURPOSE. The 500 that migration 009 fixes came
# from a bare `[:512]` literal in app/services/auth_service.py: it was chosen by
# reading THIS file (which says Text, unbounded) while the real column was
# varchar(255), so a 256-512 char User-Agent passed the truncation and then blew
# up the INSERT. A literal sitting two modules away from the column it is
# supposed to respect cannot be kept honest by review.
#
# One definition each, applied centrally in AuditService.log (the single point
# every audit row goes through), and _assert_fits below makes a future
# divergence fail LOUDLY at import instead of silently at 2am on the login path.
#
# user_agent: the column is now unbounded, so this is hygiene rather than
# correctness — a client-supplied header should not be able to write rows of
# arbitrary size. 512 is the bound this project already validated for the same
# class of input (os_version in app/schemas/client.py, raised from 32 precisely
# because navigator.userAgent overflowed it), so the fix stays confined to the
# schema and does not silently change what gets stored.
AUDIT_USER_AGENT_MAX_LEN = 512

# ip_address: the column IS bounded (45 = max INET6_ADDRSTRLEN with a scope id).
AUDIT_IP_ADDRESS_MAX_LEN = 45


def _assert_fits(column_name: str, cap: int) -> None:
    """Fail at import if a cap could no longer fit its column.

    `length` is None for Text (unbounded), an int for String(n). Narrowing the
    column below its cap, or raising a cap above a bounded column, is exactly the
    divergence that produced the login 500 — so it stops being shippable.
    """
    length = getattr(AuditLog.__table__.c[column_name].type, "length", None)
    if length is not None and cap > length:
        raise RuntimeError(
            f"audit_logs.{column_name} holds {length} chars but the application "
            f"cap is {cap}: values between {length + 1} and {cap} would pass "
            "truncation and then fail the INSERT. Align the column (migration) "
            "and the cap in app/models/audit.py."
        )


_assert_fits("user_agent", AUDIT_USER_AGENT_MAX_LEN)
_assert_fits("ip_address", AUDIT_IP_ADDRESS_MAX_LEN)
