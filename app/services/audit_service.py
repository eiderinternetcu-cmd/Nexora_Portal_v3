import uuid
from typing import Any
from sqlalchemy import select, desc, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit import (
    AuditLog,
    AUDIT_IP_ADDRESS_MAX_LEN,
    AUDIT_USER_AGENT_MAX_LEN,
)
from app.models.user import User


def _clip(value: str | None, cap: int) -> str | None:
    """Bound a client-supplied string to what its column accepts.

    Applied HERE rather than at each call site: log() is the single point every
    audit row goes through, and the previous arrangement — a `[:512]` literal
    repeated in two places in auth_service.py, none in the other ~20 callers —
    is what let the cap drift away from the column and 500 the login path.
    """
    return (value or "")[:cap] or None


class AuditService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def list(
        self,
        action: str | None = None,
        actor_username: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[AuditLog]:
        """Most-recent-first audit entries, optionally filtered. Read-only —
        the trail is append-only (migration 007 blocks UPDATE/DELETE)."""
        q = select(AuditLog).order_by(desc(AuditLog.created_at))
        if action:
            q = q.where(AuditLog.action == action)
        if actor_username:
            q = q.where(AuditLog.actor_username == actor_username)
        q = q.limit(max(1, min(limit, 200))).offset(max(0, offset))
        return list((await self.db.execute(q)).scalars().all())

    async def log(
        self,
        action: str,
        actor: User | None = None,
        target_type: str | None = None,
        target_id: str | None = None,
        details: dict | None = None,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> AuditLog:
        entry = AuditLog(
            actor_id=actor.id if actor else None,
            actor_username=actor.username if actor else None,
            action=action,
            target_type=target_type,
            target_id=target_id,
            details=details,
            ip_address=_clip(ip_address, AUDIT_IP_ADDRESS_MAX_LEN),
            user_agent=_clip(user_agent, AUDIT_USER_AGENT_MAX_LEN),
        )
        self.db.add(entry)
        await self.db.flush()
        return entry

    # ── Partition maintenance (migration 011) ────────────────────────────────
    #
    # audit_logs is RANGE-partitioned by month. Somebody has to create next
    # month's partition, and "somebody" must not mean a human with a calendar
    # reminder. These two wrappers are the callable surface a scheduler needs; the
    # logic itself is SQL in the `audit_part` schema so a cron job, pg_cron, psql
    # or the application can all drive one implementation. See
    # docs/AUDIT_PARTITIONING.md.
    #
    # Missing the schedule is survivable by design — a row matching no partition
    # lands in the DEFAULT partition instead of raising, so a forgotten job cannot
    # take down the login path that writes these rows.

    async def _partitioning_installed(self) -> bool:
        """False on a database whose schema came from the ORM rather than Alembic.

        tests/conftest.py builds every test database with
        `Base.metadata.create_all()`, which produces a plain, unpartitioned table
        and none of the `audit_part` machinery. Without this probe both methods
        below would raise UndefinedFunction there — so a scheduler wired to them
        would be reported as broken by the test suite while being perfectly
        healthy in production. Degrade to a no-op instead.
        """
        return bool(
            (
                await self.db.execute(
                    text("SELECT to_regprocedure('audit_part.ensure_partitions(int)') IS NOT NULL")
                )
            ).scalar()
        )

    async def ensure_partitions(self, premake: int | None = None) -> list[str]:
        """Create the current month's partition and the next few. Idempotent.

        Safe to call on any schedule and from more than one process at once:
        months that already exist, or that another run created concurrently, are
        skipped rather than treated as errors. Returns the partitions that exist
        as a result. `premake=None` uses audit_part.retention_policy.premake_months.
        """
        if not await self._partitioning_installed():
            return []
        rows = await self.db.execute(
            text("SELECT unnest(audit_part.ensure_partitions(:premake))"),
            {"premake": premake},
        )
        return [r[0] for r in rows]

    async def apply_retention(self, dry_run: bool = True) -> list[dict[str, Any]]:
        """Drop partitions whose whole range has aged out of the retention window.

        `dry_run=True` by DEFAULT, matching the SQL function: the careless call
        reports what it *would* drop and removes nothing. Even `dry_run=False`
        deletes nothing unless `audit_part.retention_policy.enabled` has been set
        to true by a human — two independent gates, because an audit trail that
        quietly starts forgetting is an audit trail nobody can rely on.

        Dropping a partition is DDL and fires no row trigger, so this does not
        weaken the append-only guarantee migration 007 installed: whole records
        are discarded under a stated policy; no row is ever rewritten.
        """
        if not await self._partitioning_installed():
            return []
        rows = await self.db.execute(
            text(
                "SELECT partition_name, upper_bound, dropped "
                "FROM audit_part.apply_retention(:dry_run)"
            ),
            {"dry_run": dry_run},
        )
        return [
            {"partition": name, "upper_bound": upper, "dropped": dropped}
            for name, upper, dropped in rows
        ]
