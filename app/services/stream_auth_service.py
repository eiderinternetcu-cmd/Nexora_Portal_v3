"""
StreamAuthService — autorización de reproducción IPTV para HLS/Flussonic.

Completamente separado de auth_service, subscriber_service y device_service.

Flujo authorize():
  1. subscriber.status == active
  2. subscription activa y vigente → obtiene max_connections del plan
  3. device no bloqueado + pertenece al subscriber
  4. ConnectionService.open_connection() → slot en ZSET Redis
  5. SessionService.create_iptv_session() → sesión persistida en PostgreSQL
  6. Emite JWT playback con claim 'ses' que enlaza a la sesión DB

Flujo validate() — backend-auth callback de Flussonic:
  - Verifica JWT (firma + expiración)
  - Redis nexora:playback:{jti} existe (no revocado)
  - ZSET conexión activa
  - Sesión DB no revocada y no expirada (Redis cache → DB fallback)

Flujo create_token():
  - Para dispositivos ya conectados (ZSET activo + sesión DB activa)
  - No recarga subscription/plan — más ligero que authorize()

Redis keys usados:
  nexora:playback:{jti}             → token corto (TTL: 60s)
  nexora:session_playbacks:{ses}    → SET de playback JTIs de la sesión (para revocación masiva)
  nexora:session:{ses}              → cache de sesión DB (TTL: 4h)
  nexora:active_conns:{sub_id}      → ZSET de conexiones activas
"""
import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta

import jwt
import redis.asyncio as aioredis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models.device import Device
from app.models.plan import Plan
from app.models.session import Session
from app.models.subscriber import Subscriber, SubscriberStatus
from app.models.subscription import Subscription
from app.redis_client import key_playback, key_session, key_session_playbacks
from app.services.connection_service import ConnectionService
from app.services.session_service import SessionService, _IPTV_SESSION_TTL
from app.core.exceptions import NexoraException

settings = get_settings()


@dataclass
class PlaybackToken:
    token: str
    jti: str
    session_jti: str           # links to sessions.access_token_jti
    expires_in: int            # playback token TTL (seconds)
    subscriber_id: uuid.UUID
    device_id: uuid.UUID       # Device.id — internal PK
    channel_id: str | None


class StreamAuthService:
    def __init__(self, db: AsyncSession, redis: aioredis.Redis) -> None:
        self.db = db
        self.redis = redis
        self._conn = ConnectionService(redis)
        self._sessions = SessionService(redis, db)

    # ── DB loaders ────────────────────────────────────────────────────────────

    async def _load_subscriber(self, subscriber_id: uuid.UUID) -> Subscriber:
        result = await self.db.execute(
            select(Subscriber).where(Subscriber.id == subscriber_id)
        )
        sub = result.scalar_one_or_none()
        if sub is None:
            raise NexoraException(404, "Subscriber not found")
        if sub.status != SubscriberStatus.active:
            raise NexoraException(403, f"Subscriber account is {sub.status.value}")
        return sub

    async def _load_device(self, device_id_str: str) -> Device:
        """Load device by external string identifier (Device.device_id column)."""
        result = await self.db.execute(
            select(Device).where(Device.device_id == device_id_str)
        )
        device = result.scalar_one_or_none()
        if device is None:
            raise NexoraException(404, "Device not registered")
        if device.is_blocked:
            raise NexoraException(
                403, f"Device is blocked: {device.block_reason or 'no reason given'}"
            )
        return device

    async def _load_active_subscription(
        self, subscriber_id: uuid.UUID
    ) -> tuple[Subscription, Plan]:
        now = datetime.now(timezone.utc)
        result = await self.db.execute(
            select(Subscription, Plan)
            .join(Plan, Subscription.plan_id == Plan.id)
            .where(
                Subscription.subscriber_id == subscriber_id,
                Subscription.is_active.is_(True),
                Subscription.expires_at > now,
            )
            .order_by(Subscription.expires_at.desc())
            .limit(1)
        )
        row = result.first()
        if row is None:
            raise NexoraException(403, "No active subscription found")
        return row

    # ── Token lifecycle ───────────────────────────────────────────────────────

    def _issue_jwt(
        self,
        subscriber_id: uuid.UUID,
        device_id: uuid.UUID,
        session_jti: str,
        channel_id: str | None,
    ) -> tuple[str, str, int]:
        """Returns (encoded_token, playback_jti, ttl_seconds).

        Includes 'ses' claim linking the playback token to its IPTV session in DB.
        """
        jti = str(uuid.uuid4())
        ttl = settings.playback_token_expire_seconds
        now = datetime.now(timezone.utc)
        payload = {
            "sub": str(subscriber_id),
            "dev": str(device_id),
            "ses": session_jti,
            "chn": channel_id,
            "type": "playback",
            "jti": jti,
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(seconds=ttl)).timestamp()),
        }
        token = jwt.encode(payload, settings.secret_key, algorithm=settings.jwt_algorithm)
        return token, jti, ttl

    async def _store_jwt(
        self,
        jti: str,
        subscriber_id: uuid.UUID,
        device_id: uuid.UUID,
        session_jti: str,
        channel_id: str | None,
        ttl: int,
    ) -> None:
        """Store playback token in Redis + register it under the session SET."""
        data = json.dumps({
            "subscriber_id": str(subscriber_id),
            "device_id": str(device_id),
            "session_jti": session_jti,
            "channel_id": channel_id,
        })
        await self.redis.setex(key_playback(jti), ttl, data)

        # Track under session for bulk revocation (TTL = IPTV session duration)
        await self.redis.sadd(key_session_playbacks(session_jti), jti)
        await self.redis.expire(key_session_playbacks(session_jti), _IPTV_SESSION_TTL)

    async def _decode_jwt(self, token: str) -> dict:
        """Decode and verify JWT. Raises NexoraException on failure."""
        try:
            payload = jwt.decode(
                token,
                settings.secret_key,
                algorithms=[settings.jwt_algorithm],
            )
        except jwt.ExpiredSignatureError:
            raise NexoraException(401, "Playback token expired")
        except jwt.InvalidTokenError:
            raise NexoraException(401, "Invalid playback token")
        if payload.get("type") != "playback":
            raise NexoraException(401, "Invalid token type")
        return payload

    async def _check_session_valid(self, session_jti: str) -> bool:
        """
        Verify that the linked IPTV session is still active.
        Fast path: Redis session key exists → valid.
        Fallback: DB query if Redis key is missing (session may have been revoked).
        """
        if await self.redis.exists(key_session(session_jti)):
            return True
        now = datetime.now(timezone.utc)
        result = await self.db.execute(
            select(Session.id).where(
                Session.access_token_jti == session_jti,
                Session.revoked_at.is_(None),
                Session.expires_at > now,
            )
        )
        return result.scalar_one_or_none() is not None

    def _build_result(
        self,
        token: str,
        jti: str,
        session_jti: str,
        ttl: int,
        subscriber_id: uuid.UUID,
        device_id: uuid.UUID,
        channel_id: str | None,
    ) -> PlaybackToken:
        return PlaybackToken(
            token=token,
            jti=jti,
            session_jti=session_jti,
            expires_in=ttl,
            subscriber_id=subscriber_id,
            device_id=device_id,
            channel_id=channel_id,
        )

    # ── Public API ────────────────────────────────────────────────────────────

    async def authorize(
        self,
        subscriber_id: uuid.UUID,
        device_id_str: str,
        channel_id: str | None = None,
        ip: str | None = None,
        user_agent: str | None = None,
    ) -> PlaybackToken:
        """
        Full authorization flow:
          1. subscriber active
          2. subscription active + not expired → plan.max_connections
          3. device not blocked + belongs to subscriber
          4. concurrent slot available (Redis ZSET)
          5. IPTV session created / replaced in PostgreSQL
          6. playback JWT issued with 'ses' claim linking to DB session
        """
        sub = await self._load_subscriber(subscriber_id)
        _, plan = await self._load_active_subscription(sub.id)
        device = await self._load_device(device_id_str)

        if device.subscriber_id != sub.id:
            raise NexoraException(403, "Device does not belong to this subscriber")

        opened = await self._conn.open_connection(sub.id, device.id, plan.max_connections)
        if not opened:
            raise NexoraException(
                409,
                f"Max concurrent connections reached ({plan.max_connections})",
            )

        # Create IPTV session in DB (revokes existing session for this device)
        session_jti = str(uuid.uuid4())
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=_IPTV_SESSION_TTL)
        await self._sessions.create_iptv_session(
            subscriber_id=sub.id,
            device_id=device.id,
            access_jti=session_jti,
            expires_at=expires_at,
            ip=ip,
            user_agent=user_agent,
            device_fingerprint=device.device_fingerprint,
        )

        token, jti, ttl = self._issue_jwt(sub.id, device.id, session_jti, channel_id)
        await self._store_jwt(jti, sub.id, device.id, session_jti, channel_id, ttl)
        return self._build_result(token, jti, session_jti, ttl, sub.id, device.id, channel_id)

    async def validate(self, token: str) -> dict:
        """
        Validate an existing playback token.
        Designed as the Flussonic Media Server backend-auth callback.

        Checks (in order, fast to slow):
          1. JWT signature + expiry
          2. Redis nexora:playback:{jti} exists (not revoked)
          3. Redis ZSET connection active
          4. IPTV session not revoked (Redis cache → DB fallback)

        Returns payload dict. Raises NexoraException on any failure.
        """
        payload = await self._decode_jwt(token)
        jti: str = payload.get("jti", "")
        ses: str | None = payload.get("ses")
        subscriber_id_str: str = payload["sub"]
        device_id_str: str = payload["dev"]

        # 1. Playback token not revoked
        if not await self.redis.exists(key_playback(jti)):
            raise NexoraException(401, "Playback token not found or revoked")

        # 2. ZSET connection still alive
        if not await self._conn.is_connected(subscriber_id_str, device_id_str):
            raise NexoraException(403, "No active IPTV connection for this device")

        # 3. IPTV session still valid (skip for old tokens without 'ses' claim)
        if ses and not await self._check_session_valid(ses):
            raise NexoraException(403, "IPTV session has been revoked or expired")

        return {
            "subscriber_id": subscriber_id_str,
            "device_id": device_id_str,
            "channel_id": payload.get("chn"),
            "expires_at": payload.get("exp"),
        }

    async def create_token(
        self,
        subscriber_id: uuid.UUID,
        device_id_str: str,
        channel_id: str | None = None,
    ) -> PlaybackToken:
        """
        Reissue a playback token for a device already connected.
        Requires:
          - Device in active ZSET
          - Active IPTV session in DB

        Lighter than authorize() — skips subscriber, subscription, and plan reload.
        """
        device = await self._load_device(device_id_str)

        if device.subscriber_id != subscriber_id:
            raise NexoraException(403, "Device does not belong to this subscriber")

        if not await self._conn.is_connected(subscriber_id, device.id):
            raise NexoraException(
                403,
                "Device has no active IPTV connection — call /auth/play first",
            )

        # Get active session to link the new playback token
        session = await self._sessions.get_active_iptv_session(subscriber_id, device.id)
        if session is None:
            raise NexoraException(
                403,
                "No active IPTV session in DB — call /auth/play to start a session",
            )

        token, jti, ttl = self._issue_jwt(
            subscriber_id, device.id, session.access_token_jti, channel_id
        )
        await self._store_jwt(
            jti, subscriber_id, device.id, session.access_token_jti, channel_id, ttl
        )
        return self._build_result(
            token, jti, session.access_token_jti, ttl, subscriber_id, device.id, channel_id
        )

    async def revoke_token(self, token: str) -> bool:
        """
        Revoke a playback token by deleting its Redis key.
        Does NOT revoke the underlying IPTV session — use admin sessions API for that.
        Returns True if the token existed and was deleted.
        """
        try:
            payload = await self._decode_jwt(token)
        except NexoraException:
            return False
        jti: str = payload.get("jti", "")
        ses: str | None = payload.get("ses")
        deleted = bool(await self.redis.delete(key_playback(jti)))
        if deleted and ses:
            await self.redis.srem(key_session_playbacks(ses), jti)
        return deleted
