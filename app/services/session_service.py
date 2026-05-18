"""
SessionService — gestión de sesiones en Redis (admin/reseller) y PostgreSQL (suscriptores IPTV).

Redis-only:   acceso y refresh tokens de admins/resellers.
DB + Redis:   sesiones IPTV de suscriptores (auditoría, logout real, concurrencia).

Separación de responsabilidades:
  - Métodos sin prefijo (store_access, store_refresh, revoke_access, revoke_refresh, is_blacklisted)
    → manejan JWTs de admin/reseller (blacklist JTI, TTL corto)
  - Métodos IPTV (create_iptv_session, get_active_iptv_session, touch_iptv_session,
    revoke_iptv_session, revoke_subscriber_sessions)
    → manejan sesiones de suscriptores en PostgreSQL + Redis cache
"""
import json
import uuid
from datetime import datetime, timezone

import redis.asyncio as aioredis
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models.session import Session
from app.redis_client import (
    key_session,
    key_refresh,
    key_blacklist,
    key_playback,
    key_session_playbacks,
)

settings = get_settings()

# Default IPTV session duration (seconds). 4 hours covers a typical viewing session.
_IPTV_SESSION_TTL = 4 * 3600


class SessionService:
    def __init__(self, redis: aioredis.Redis, db: AsyncSession | None = None):
        self.redis = redis
        self.db = db

    # ── Redis-only — admin/reseller JWT ───────────────────────────────────────

    async def store_access(self, jti: str, user_id: str, role: str) -> None:
        ttl = settings.access_token_expire_minutes * 60
        data = json.dumps({"user_id": user_id, "role": role})
        await self.redis.setex(key_session(jti), ttl, data)

    async def store_refresh(self, jti: str, user_id: str, role: str) -> None:
        ttl = settings.refresh_token_expire_days * 86400
        data = json.dumps({"user_id": user_id, "role": role})
        await self.redis.setex(key_refresh(jti), ttl, data)

    async def get_refresh(self, jti: str) -> dict | None:
        raw = await self.redis.get(key_refresh(jti))
        return json.loads(raw) if raw else None

    async def revoke_access(self, jti: str) -> None:
        ttl = settings.access_token_expire_minutes * 60
        await self.redis.setex(key_blacklist(jti), ttl, "1")
        await self.redis.delete(key_session(jti))

    async def revoke_refresh(self, jti: str) -> None:
        ttl = settings.refresh_token_expire_days * 86400
        await self.redis.setex(key_blacklist(jti), ttl, "1")
        await self.redis.delete(key_refresh(jti))

    async def is_blacklisted(self, jti: str) -> bool:
        return bool(await self.redis.exists(key_blacklist(jti)))

    # ── DB + Redis — subscriber IPTV sessions ─────────────────────────────────

    def _require_db(self) -> AsyncSession:
        if self.db is None:
            raise RuntimeError("SessionService requires db for subscriber session operations")
        return self.db

    async def get_active_iptv_session(
        self, subscriber_id: uuid.UUID, device_id: uuid.UUID
    ) -> Session | None:
        """Return the most recent active IPTV session for a specific device."""
        db = self._require_db()
        now = datetime.now(timezone.utc)
        result = await db.execute(
            select(Session)
            .where(
                Session.subscriber_id == subscriber_id,
                Session.device_id == device_id,
                Session.revoked_at.is_(None),
                Session.expires_at > now,
            )
            .order_by(Session.created_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def get_session_by_jti(self, access_jti: str) -> Session | None:
        """Load a session by its access_token_jti regardless of status."""
        db = self._require_db()
        result = await db.execute(
            select(Session).where(Session.access_token_jti == access_jti)
        )
        return result.scalar_one_or_none()

    async def create_iptv_session(
        self,
        subscriber_id: uuid.UUID,
        device_id: uuid.UUID,
        access_jti: str,
        expires_at: datetime,
        ip: str | None = None,
        user_agent: str | None = None,
        device_fingerprint: str | None = None,
    ) -> Session:
        """
        Create a new IPTV session in DB.
        If an active session already exists for this device, it is revoked first
        and its Redis keys + playback tokens are cleaned up.
        """
        db = self._require_db()
        now = datetime.now(timezone.utc)

        existing = await self.get_active_iptv_session(subscriber_id, device_id)
        if existing:
            await db.execute(
                update(Session)
                .where(Session.id == existing.id)
                .values(revoked_at=now)
            )
            await self._cleanup_session_redis(existing.access_token_jti)

        session = Session(
            subscriber_id=subscriber_id,
            device_id=device_id,
            device_fingerprint=device_fingerprint,
            access_token_jti=access_jti,
            refresh_token_jti=None,
            ip_address=ip,
            user_agent=user_agent,
            expires_at=expires_at,
        )
        db.add(session)
        await db.flush()

        ttl = int((expires_at - now).total_seconds())
        if ttl > 0:
            payload = json.dumps({
                "subscriber_id": str(subscriber_id),
                "device_id": str(device_id),
            })
            await self.redis.setex(key_session(access_jti), ttl, payload)

        return session

    async def touch_iptv_session(self, access_jti: str) -> None:
        """Update last_heartbeat_at for an active IPTV session."""
        await self.update_heartbeat(access_jti)

    async def revoke_iptv_session(self, access_jti: str) -> bool:
        """
        Revoke an IPTV session:
          - Sets revoked_at in DB
          - Deletes Redis session key (nexora:session:{jti})
          - Immediately deletes all associated playback tokens

        Returns True if the session existed and was revoked.
        """
        db = self._require_db()
        now = datetime.now(timezone.utc)
        result = await db.execute(
            update(Session)
            .where(
                Session.access_token_jti == access_jti,
                Session.revoked_at.is_(None),
            )
            .values(revoked_at=now)
            .returning(Session.id)
        )
        revoked = result.scalar_one_or_none() is not None
        if revoked:
            await self._cleanup_session_redis(access_jti)
        return revoked

    async def revoke_subscriber_sessions(self, subscriber_id: uuid.UUID) -> int:
        """
        Revoke all active IPTV sessions for a subscriber.
        Cleans Redis session keys + all associated playback tokens.
        Returns the number of sessions revoked.
        """
        db = self._require_db()
        now = datetime.now(timezone.utc)

        result = await db.execute(
            select(Session.access_token_jti).where(
                Session.subscriber_id == subscriber_id,
                Session.revoked_at.is_(None),
                Session.expires_at > now,
            )
        )
        jtis = list(result.scalars().all())

        if not jtis:
            return 0

        await db.execute(
            update(Session)
            .where(
                Session.subscriber_id == subscriber_id,
                Session.revoked_at.is_(None),
            )
            .values(revoked_at=now)
        )

        for jti in jtis:
            await self._cleanup_session_redis(jti)

        return len(jtis)

    async def _cleanup_session_redis(self, access_jti: str) -> None:
        """Remove Redis session key and all associated playback tokens."""
        await self.redis.delete(key_session(access_jti))
        playback_jtis: set[str] = await self.redis.smembers(
            key_session_playbacks(access_jti)
        )
        if playback_jtis:
            for pjti in playback_jtis:
                await self.redis.delete(key_playback(pjti))
            await self.redis.delete(key_session_playbacks(access_jti))

    # ── Legacy DB methods (kept for backward compat / admin panel) ────────────

    async def create_subscriber_session(
        self,
        subscriber_id: uuid.UUID,
        device_id: uuid.UUID | None,
        access_jti: str,
        refresh_jti: str | None,
        expires_at: datetime,
        ip: str | None = None,
        user_agent: str | None = None,
        device_fingerprint: str | None = None,
    ) -> Session:
        db = self._require_db()
        session = Session(
            subscriber_id=subscriber_id,
            device_id=device_id,
            device_fingerprint=device_fingerprint,
            access_token_jti=access_jti,
            refresh_token_jti=refresh_jti,
            ip_address=ip,
            user_agent=user_agent,
            expires_at=expires_at,
        )
        db.add(session)
        await db.flush()

        ttl = int((expires_at - datetime.now(timezone.utc)).total_seconds())
        if ttl > 0:
            payload = json.dumps({"subscriber_id": str(subscriber_id)})
            await self.redis.setex(key_session(access_jti), ttl, payload)

        return session

    async def list_subscriber_sessions(
        self, subscriber_id: uuid.UUID, only_active: bool = True
    ) -> list[Session]:
        db = self._require_db()
        stmt = select(Session).where(Session.subscriber_id == subscriber_id)
        if only_active:
            now = datetime.now(timezone.utc)
            stmt = stmt.where(
                Session.revoked_at.is_(None),
                Session.expires_at > now,
            )
        stmt = stmt.order_by(Session.created_at.desc())
        result = await db.execute(stmt)
        return list(result.scalars().all())

    async def revoke_session(self, access_jti: str) -> bool:
        """Revoca una sesión por JTI. Retorna True si existía y fue revocada."""
        db = self._require_db()
        now = datetime.now(timezone.utc)
        result = await db.execute(
            update(Session)
            .where(Session.access_token_jti == access_jti, Session.revoked_at.is_(None))
            .values(revoked_at=now)
            .returning(Session.id)
        )
        revoked = result.scalar_one_or_none() is not None
        if revoked:
            ttl = settings.access_token_expire_minutes * 60
            await self.redis.setex(key_blacklist(access_jti), ttl, "1")
            await self.redis.delete(key_session(access_jti))
        return revoked

    async def revoke_all_subscriber_sessions(self, subscriber_id: uuid.UUID) -> int:
        """Revoca todas las sesiones activas de un suscriptor. Retorna el conteo."""
        db = self._require_db()
        now = datetime.now(timezone.utc)

        result = await db.execute(
            select(Session.access_token_jti).where(
                Session.subscriber_id == subscriber_id,
                Session.revoked_at.is_(None),
                Session.expires_at > now,
            )
        )
        jtis = result.scalars().all()

        if not jtis:
            return 0

        await db.execute(
            update(Session)
            .where(Session.subscriber_id == subscriber_id, Session.revoked_at.is_(None))
            .values(revoked_at=now)
        )

        ttl = settings.access_token_expire_minutes * 60
        for jti in jtis:
            await self.redis.setex(key_blacklist(jti), ttl, "1")
            await self.redis.delete(key_session(jti))

        return len(jtis)

    async def update_heartbeat(self, access_jti: str) -> None:
        """Actualiza last_heartbeat_at para una sesión activa."""
        db = self._require_db()
        now = datetime.now(timezone.utc)
        await db.execute(
            update(Session)
            .where(Session.access_token_jti == access_jti, Session.revoked_at.is_(None))
            .values(last_heartbeat_at=now)
        )
