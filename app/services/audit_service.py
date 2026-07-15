import uuid
from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit import AuditLog
from app.models.user import User


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
            ip_address=ip_address,
            user_agent=user_agent,
        )
        self.db.add(entry)
        await self.db.flush()
        return entry
