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
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta

import jwt
import redis.asyncio as aioredis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core.security import AUD_PLAYBACK, TYPE_PLAYBACK, hash_ip
from app.redis_client import key_stream_grant
from app.services.entitlement_service import EntitlementService

logger = logging.getLogger(__name__)

# Playback token types accepted by the validator (legacy "playback" tolerated;
# tokens are 60s-lived so there is no real legacy window).
_PLAYBACK_TYPES = {TYPE_PLAYBACK, "playback"}
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
            raise NexoraException(403, "DEVICE_NOT_REGISTERED")
        if device.is_blocked:
            raise NexoraException(403, "DEVICE_BLOCKED")
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

    # ── Entitlement gate (P0) ──────────────────────────────────────────────────

    async def _check_entitlement(
        self,
        subscriber_id: uuid.UUID,
        device_id_str: str,
        channel_key: str | None,
    ) -> None:
        """Consult EntitlementService BEFORE creating session/token/URL.

        - channel_key None  → skip (no catalog context, e.g. token reissue/STB).
        - allow             → return.
        - deny + enforce ON → raise 403 with reason_code (nothing created).
        - deny + enforce OFF→ log warning, continue (compat / observation mode).
        """
        if channel_key is None:
            return
        result = await EntitlementService(self.db).can_watch_channel(
            subscriber_id, device_id_str, channel_key
        )
        if result.allow:
            return
        if settings.entitlement_enforce:
            raise NexoraException(403, result.reason_code.value)
        logger.warning(
            "entitlement deny (enforce=off): subscriber=%s channel=%s reason=%s",
            subscriber_id, channel_key, result.code,
        )

    # ── Token lifecycle ───────────────────────────────────────────────────────

    def _issue_jwt(
        self,
        subscriber_id: uuid.UUID,
        device_id: uuid.UUID,
        session_jti: str,
        channel_key: str | None = None,
        stream_key: str | None = None,
        node: str | None = None,
        client_ip: str | None = None,
    ) -> tuple[str, str, int]:
        """Returns (encoded_token, playback_jti, ttl_seconds).

        Playback token (type=playback_token, aud=nexora-playback, iss=nexora-api).
        Bound to subscriber(sub) + device(dev) + session(ses) + channel(chn) +
        stream_key(sk) + node + client-IP hash(cip), so a token is only valid for
        its exact stream and (under IP binding) its issuing client.
        """
        jti = str(uuid.uuid4())
        ttl = settings.playback_token_expire_seconds
        now = datetime.now(timezone.utc)
        payload = {
            "sub": str(subscriber_id),
            "dev": str(device_id),
            "ses": session_jti,
            "chn": channel_key,
            "sk": stream_key,
            "node": node,
            "cip": hash_ip(client_ip) if client_ip else None,
            "type": TYPE_PLAYBACK,
            "aud": AUD_PLAYBACK,
            "iss": settings.jwt_issuer,
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
        """Decode and verify JWT. Raises NexoraException on failure.

        Audience is not auto-verified here (verify_aud=False); aud/iss are checked
        explicitly by callers (validate_stream_request). Algorithm is fixed.
        """
        try:
            payload = jwt.decode(
                token,
                settings.secret_key,
                algorithms=[settings.jwt_algorithm],
                options={"verify_aud": False},
            )
        except jwt.ExpiredSignatureError:
            raise NexoraException(401, "Playback token expired")
        except jwt.InvalidTokenError:
            raise NexoraException(401, "Invalid playback token")
        if payload.get("type") not in _PLAYBACK_TYPES:
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
        channel_key: str | None = None,
        node: str | None = None,
    ) -> PlaybackToken:
        """
        Full authorization flow:
          0. entitlement (can_watch_channel) — runs FIRST; if denied and
             ENTITLEMENT_ENFORCE is on, raises 403 BEFORE any session/token/URL.
          1. subscriber active
          2. subscription active + not expired → plan.max_connections
          3. device not blocked + belongs to subscriber
          4. concurrent slot available (Redis ZSET)
          5. IPTV session created / replaced in PostgreSQL
          6. playback JWT issued with 'ses' claim linking to DB session

        channel_key is the PUBLIC channel key (catalog). When provided, the
        EntitlementService is consulted. channel_id is the internal stream_key
        embedded in the token (unchanged).
        """
        await self._check_entitlement(subscriber_id, device_id_str, channel_key)

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

        token, jti, ttl = self._issue_jwt(
            sub.id, device.id, session_jti,
            channel_key=channel_key, stream_key=channel_id, node=node, client_ip=ip,
        )
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

    async def validate_stream_request(
        self,
        token: str | None,
        stream_key: str | None = None,
        node: str | None = None,
        client_ip: str | None = None,
    ) -> dict:
        """Validate a playback token for a concrete /stream/* request.

        Designed for the Nginx auth_request → FastAPI gate.
        Checks (fast → slow):
          1. token present, signature + exp + type=playback_token  (_decode_jwt)
          2. aud=nexora-playback, iss=nexora-api
          3. Redis playback token not revoked
          4. ZSET connection alive for this device
          5. IPTV session not revoked/expired
          6. stream_key / node bound to the token (no cross-stream reuse)
          7. client IP binding (C-PROD-2) per playback_ip_binding_mode
        Returns a SAFE payload dict (incl. session_id). Raises 401/403 on failure.
        """
        if not token:
            raise NexoraException(401, "Missing playback token")

        payload = await self._decode_jwt(token)

        if payload.get("aud") != AUD_PLAYBACK or payload.get("iss") != settings.jwt_issuer:
            raise NexoraException(401, "Invalid playback token audience/issuer")

        jti = payload.get("jti", "")
        subscriber_id_str = payload.get("sub", "")
        device_id_str = payload.get("dev", "")
        ses = payload.get("ses")

        if not await self.redis.exists(key_playback(jti)):
            raise NexoraException(401, "Playback token not found or revoked")

        if not await self._conn.is_connected(subscriber_id_str, device_id_str):
            raise NexoraException(403, "No active IPTV connection for this device")

        if ses and not await self._check_session_valid(ses):
            raise NexoraException(403, "IPTV session has been revoked or expired")

        if stream_key is not None and payload.get("sk") != stream_key:
            raise NexoraException(403, "Playback token not valid for this stream")

        if node is not None and payload.get("node") not in (None, node):
            raise NexoraException(403, "Playback token not valid for this node")

        # 7. IP binding (C-PROD-2). off → skip; soft → warn; strict → 403 on mismatch.
        mode = settings.playback_ip_binding_mode
        token_cip = payload.get("cip")
        if mode != "off" and token_cip and client_ip:
            if token_cip != hash_ip(client_ip):
                if mode == "strict":
                    raise NexoraException(403, "Playback token IP mismatch")
                logger.warning("playback IP mismatch (soft): jti=%s", jti)

        return {
            "subscriber_id": subscriber_id_str,
            "device_id": device_id_str,
            "session_id": ses,
            "channel_id": payload.get("chn"),
            "stream_key": payload.get("sk"),
            "node": payload.get("node"),
            "expires_at": payload.get("exp"),
        }

    # ── Segment grant cache (C-PROD-1) ─────────────────────────────────────────

    async def grant_stream_access(
        self, node: str, stream_key: str, ip_hash: str, session_id: str | None
    ) -> None:
        """Seed a short-lived grant after a token-validated manifest request, so
        tokenless HLS segments of the SAME node+stream+client IP can pass."""
        ttl = settings.stream_auth_cache_ttl_seconds
        await self.redis.setex(
            key_stream_grant(node, stream_key, ip_hash), ttl, session_id or "1"
        )

    async def check_stream_grant(self, node: str, stream_key: str, ip_hash: str) -> bool:
        """Return True if a valid grant exists for this node+stream+client IP,
        renewing its TTL (sliding window while segments keep flowing)."""
        key = key_stream_grant(node, stream_key, ip_hash)
        if not await self.redis.exists(key):
            return False
        await self.redis.expire(key, settings.stream_auth_cache_ttl_seconds)
        return True

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
            subscriber_id, device.id, session.access_token_jti, stream_key=channel_id
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
