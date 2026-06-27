"""
ClientAuthService — subscriber-facing authentication for modern client apps.
Handles login with device auto-registration, JWT rotation, and logout.
"""
from sqlalchemy.ext.asyncio import AsyncSession
import redis.asyncio as aioredis
from jwt.exceptions import InvalidTokenError

from app.config import get_settings
from app.core.security import (
    create_client_access_token,
    create_client_refresh_token,
    decode_client_token,
)
from app.core.exceptions import unauthorized
from app.redis_client import key_client, key_client_refresh, key_login_attempts, key_lockout
from app.schemas.client import ClientLoginRequest
from app.schemas.device import DeviceRegister
from app.services.stb_service import STBService
from app.services.device_service import DeviceService

settings = get_settings()


class ClientAuthService:
    def __init__(self, db: AsyncSession, redis: aioredis.Redis):
        self.db = db
        self.redis = redis

    async def _check_lockout(self, username: str) -> None:
        if await self.redis.exists(key_lockout(f"sub:{username}")):
            raise unauthorized("Account temporarily locked due to too many failed attempts")

    async def _record_failed_attempt(self, username: str) -> None:
        ttl = settings.login_lockout_minutes * 60
        attempts_key = key_login_attempts(f"sub:{username}")
        lockout_key = key_lockout(f"sub:{username}")
        attempts = await self.redis.incr(attempts_key)
        await self.redis.expire(attempts_key, ttl)
        if attempts >= settings.max_login_attempts:
            await self.redis.setex(lockout_key, ttl, "1")

    async def _clear_failed_attempts(self, username: str) -> None:
        await self.redis.delete(key_login_attempts(f"sub:{username}"))
        await self.redis.delete(key_lockout(f"sub:{username}"))

    async def login(
        self,
        data: ClientLoginRequest,
        ip: str,
        user_agent: str | None,
    ) -> tuple[str, str, str, int, str]:
        """Returns (access_token, refresh_token, subscriber_id_str, expires_in_seconds,
        device_registration). device_registration is 'registered' or 'limit_reached'.

        Login NEVER fails because of the device cap: identity/status/credentials
        are validated, tokens are issued, and the device is registered only if
        there is room (raise_on_limit=False).
        """
        await self._check_lockout(data.username)

        stb = STBService(self.db, self.redis)
        try:
            subscriber = await stb.authenticate_subscriber(
                data.username,
                password=data.password,
                activation_code=data.activation_code,
            )
        except Exception:
            await self._record_failed_attempt(data.username)
            raise

        await self._clear_failed_attempts(data.username)

        dev_svc = DeviceService(self.db, self.redis)
        device = await dev_svc.register(
            subscriber.id,
            DeviceRegister(
                device_id=data.device_id,
                model=data.model,
                brand=data.brand,
                device_type=data.device_type,
                app_version=data.app_version,
                user_agent=user_agent,
            ),
            ip,
            raise_on_limit=False,  # login must not fail on device cap
        )
        device_registration = "registered" if device is not None else "limit_reached"

        sub_id_str = str(subscriber.id)
        access_token, access_jti, expires_in = create_client_access_token(sub_id_str)
        refresh_token, refresh_jti = create_client_refresh_token(sub_id_str)

        await self.redis.setex(
            key_client(access_jti),
            settings.client_access_token_expire_hours * 3600,
            sub_id_str,
        )
        await self.redis.setex(
            key_client_refresh(refresh_jti),
            settings.client_refresh_token_expire_days * 86400,
            sub_id_str,
        )
        return access_token, refresh_token, sub_id_str, expires_in, device_registration

    async def refresh(self, refresh_token_str: str) -> tuple[str, str, str, int]:
        """Returns (access_token, new_refresh_token, subscriber_id_str, expires_in_seconds).
        Rotates the refresh token — consumes old, issues new.
        """
        try:
            payload = decode_client_token(refresh_token_str)
        except InvalidTokenError:
            raise unauthorized("Invalid or expired refresh token")

        if payload.get("type") != "client_refresh":
            raise unauthorized("Expected client refresh token")

        jti = payload.get("jti")
        sub_id_str = payload.get("sub")
        if not jti or not sub_id_str:
            raise unauthorized("Malformed token")

        stored = await self.redis.getdel(key_client_refresh(jti))
        if not stored:
            raise unauthorized("Refresh token has been revoked or already used")

        access_token, access_jti, expires_in = create_client_access_token(sub_id_str)
        new_refresh, new_refresh_jti = create_client_refresh_token(sub_id_str)

        await self.redis.setex(
            key_client(access_jti),
            settings.client_access_token_expire_hours * 3600,
            sub_id_str,
        )
        await self.redis.setex(
            key_client_refresh(new_refresh_jti),
            settings.client_refresh_token_expire_days * 86400,
            sub_id_str,
        )
        return access_token, new_refresh, sub_id_str, expires_in

    async def logout(self, access_jti: str, refresh_token_str: str | None = None) -> None:
        await self.redis.delete(key_client(access_jti))
        if refresh_token_str:
            try:
                payload = decode_client_token(refresh_token_str)
                if payload.get("type") == "client_refresh":
                    await self.redis.delete(key_client_refresh(payload["jti"]))
            except Exception:
                pass
