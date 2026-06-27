"""
AuthService — login, logout, refresh token, bloqueo por intentos fallidos.
Usa PyJWT + Argon2id via passlib.
"""
from datetime import datetime, timezone

import redis.asyncio as aioredis
from jwt.exceptions import InvalidTokenError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update

from app.config import get_settings
from app.models.user import User
from app.core.security import verify_password, create_access_token, create_refresh_token, decode_token
from app.core.exceptions import unauthorized, locked
from app.redis_client import key_login_attempts, key_lockout
from app.services.session_service import SessionService
from app.schemas.auth import TokenResponse

settings = get_settings()


class AuthService:
    def __init__(self, db: AsyncSession, redis: aioredis.Redis):
        self.db = db
        self.redis = redis
        self.sessions = SessionService(redis)

    async def _check_lockout(self, identifier: str) -> None:
        if await self.redis.exists(key_lockout(identifier)):
            ttl = await self.redis.ttl(key_lockout(identifier))
            raise locked(f"Account locked. Try again in {ttl}s.")

    async def _record_failed_attempt(self, identifier: str) -> None:
        key = key_login_attempts(identifier)
        attempts = await self.redis.incr(key)
        await self.redis.expire(key, settings.login_lockout_minutes * 60)
        if attempts >= settings.max_login_attempts:
            await self.redis.setex(
                key_lockout(identifier),
                settings.login_lockout_minutes * 60,
                "1",
            )
            await self.redis.delete(key)

    async def _clear_attempts(self, identifier: str) -> None:
        await self.redis.delete(key_login_attempts(identifier))
        await self.redis.delete(key_lockout(identifier))

    async def login(self, username: str, password: str, ip: str) -> TokenResponse:
        await self._check_lockout(username)
        await self._check_lockout(ip)

        result = await self.db.execute(select(User).where(User.username == username))
        user = result.scalar_one_or_none()

        if not user or not verify_password(password, user.password_hash):
            await self._record_failed_attempt(username)
            await self._record_failed_attempt(ip)
            raise unauthorized("Invalid credentials")

        if not user.is_active:
            raise unauthorized("Account is disabled")

        await self._clear_attempts(username)
        await self._clear_attempts(ip)

        user_id = str(user.id)
        role = user.role.value
        access_token, a_jti, expires_in = create_access_token(user_id, role)
        refresh_token, r_jti = create_refresh_token(user_id, role)

        await self.sessions.store_access(a_jti, user_id, role)
        await self.sessions.store_refresh(r_jti, user_id, role)

        await self.db.execute(
            update(User)
            .where(User.id == user.id)
            .values(last_login_at=datetime.now(timezone.utc), last_login_ip=ip)
        )
        await self.db.commit()

        return TokenResponse(
            access_token=access_token,
            refresh_token=refresh_token,
            expires_in=expires_in,
        )

    async def refresh(self, refresh_token: str) -> TokenResponse:
        try:
            payload = decode_token(refresh_token)
        except InvalidTokenError:
            raise unauthorized("Invalid refresh token")

        if payload.type not in ("admin_refresh", "refresh"):
            raise unauthorized("Expected refresh token")

        session = await self.sessions.get_refresh(payload.jti)
        if not session:
            raise unauthorized("Refresh token expired or revoked")

        # Rotate: revoke old, issue new pair
        await self.sessions.revoke_refresh(payload.jti)

        user_id = payload.sub
        role = payload.role.value
        access_token, a_jti, expires_in = create_access_token(user_id, role)
        new_refresh, r_jti = create_refresh_token(user_id, role)

        await self.sessions.store_access(a_jti, user_id, role)
        await self.sessions.store_refresh(r_jti, user_id, role)

        return TokenResponse(
            access_token=access_token,
            refresh_token=new_refresh,
            expires_in=expires_in,
        )

    async def logout(self, access_jti: str, refresh_token: str | None = None) -> None:
        await self.sessions.revoke_access(access_jti)
        if refresh_token:
            try:
                payload = decode_token(refresh_token)
                await self.sessions.revoke_refresh(payload.jti)
            except InvalidTokenError:
                pass
