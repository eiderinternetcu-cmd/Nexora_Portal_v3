import uuid
from sqlalchemy import select, desc
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
