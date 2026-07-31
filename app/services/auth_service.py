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
from app.core.security import (
    verify_password,
    create_access_token,
    create_refresh_token,
    decode_claims,
    enforce_surface,
    TYPE_ADMIN_REFRESH,
    AUD_ADMIN,
    LEGACY_ADMIN_REFRESH,
)
from app.core.exceptions import unauthorized, locked
from app.redis_client import key_login_attempts, key_lockout
from app.services.session_service import SessionService
from app.services.audit_service import AuditService
from app.schemas.auth import TokenResponse

settings = get_settings()


class AuthService:
    def __init__(self, db: AsyncSession, redis: aioredis.Redis):
        self.db = db
        self.redis = redis
        self.sessions = SessionService(redis)

    # ── Legacy lockout (LOGIN_LOCKOUT_ENABLED=False) ────────────────────────
    # Untouched on purpose: this is what production runs today (username AND IP
    # counters, 423 with the remaining TTL). Kept verbatim so deploying this
    # branch with the flag at its default is a behavioral no-op.

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

    # ── Hardened per-username lockout (LOGIN_LOCKOUT_ENABLED=True) ───────────

    @staticmethod
    def _lockout_id(username: str) -> str:
        """Redis identifier for the hardened counter: `admin:{username}`.

        Follows the existing convention (ClientAuthService uses `sub:{username}`)
        and keeps the namespace disjoint from the legacy bare-username keys.

        The username is NOT lowercased: the DB lookup is case sensitive, so
        `ADMIN` can never authenticate as `admin` and folding case would only
        hand an attacker a way to lock an account they cannot even target. It IS
        truncated to the schema's max_length so a caller bypassing LoginRequest
        cannot mint unbounded Redis keys.
        """
        return f"admin:{username.strip()[:64]}"

    async def _is_locked_out(self, username: str) -> bool:
        return bool(await self.redis.exists(key_lockout(self._lockout_id(username))))

    async def _record_user_failure(self, username: str) -> None:
        """Count one consecutive failure; block once max_attempts is reached.

        The window is anchored on the FIRST failure (expire set only when the
        counter is created) rather than refreshed on every failure, so the
        contract is the one we document — "N failures within WINDOW" — and a
        slow drip of wrong passwords over hours cannot accumulate into a lockout.
        """
        identifier = self._lockout_id(username)
        key = key_login_attempts(identifier)
        attempts = await self.redis.incr(key)
        if attempts == 1:
            await self.redis.expire(key, settings.login_lockout_window_seconds)
        if attempts >= settings.login_lockout_max_attempts:
            await self.redis.setex(
                key_lockout(identifier),
                settings.login_lockout_duration_seconds,
                "1",
            )
            # Drop the counter: the block window is fixed from the Nth failure and
            # must not be extended by the attempts an attacker keeps making while
            # blocked, otherwise the account could be held locked indefinitely.
            await self.redis.delete(key)

    async def _clear_user_failures(self, username: str) -> None:
        identifier = self._lockout_id(username)
        await self.redis.delete(key_login_attempts(identifier))
        await self.redis.delete(key_lockout(identifier))

    # ── Login audit (NX-AUTH) ───────────────────────────────────────────────

    async def _audit_login(
        self,
        action: str,
        username: str,
        ip: str | None,
        user_agent: str | None,
        reason: str,
        actor: User | None = None,
    ) -> None:
        """Write one login-attempt row and COMMIT it on its own.

        Failed attempts abort the request with a 401, which would roll the row
        back if it rode the request transaction — so it is committed here. Any
        error is swallowed: the audit trail must never turn a 401 into a 500, and
        must never be the reason a login is refused.

        `details` is an explicit whitelist built here. The password and the
        issued tokens are never passed to this method, let alone stored.
        """
        try:
            await AuditService(self.db).log(
                action=action,
                actor=actor,
                target_type="user",
                target_id=str(actor.id) if actor else None,
                details={
                    "outcome": "failure",
                    "reason": reason,
                    "username": username[:64],
                    "lockout_enabled": settings.login_lockout_enabled,
                },
                # ip_address is String(45); X-Forwarded-For can carry anything.
                ip_address=(ip or "")[:45] or None,
                user_agent=(user_agent or "")[:512] or None,
            )
            await self.db.commit()
        except Exception:
            await self.db.rollback()

    async def login(
        self,
        username: str,
        password: str,
        ip: str,
        user_agent: str | None = None,
    ) -> TokenResponse:
        if settings.login_lockout_enabled:
            if await self._is_locked_out(username):
                await self._audit_login(
                    "auth.login_blocked", username, ip, user_agent, reason="locked_out"
                )
                # Deliberately the SAME 401 body as a wrong password: a 423/429 or
                # any "try again in Ns" hint would confirm the account is in a
                # lockable state and tell the attacker exactly when to resume.
                # The lockout arms for unknown usernames too, so this response
                # never distinguishes an existing account from a made-up one.
                raise unauthorized("Invalid credentials")
        else:
            await self._check_lockout(username)
            await self._check_lockout(ip)

        result = await self.db.execute(select(User).where(User.username == username))
        user = result.scalar_one_or_none()

        if not user or not verify_password(password, user.password_hash):
            if settings.login_lockout_enabled:
                await self._record_user_failure(username)
            else:
                await self._record_failed_attempt(username)
                await self._record_failed_attempt(ip)
            await self._audit_login(
                "auth.login_failed", username, ip, user_agent,
                reason="unknown_user" if not user else "invalid_credentials",
                actor=user,
            )
            raise unauthorized("Invalid credentials")

        if not user.is_active:
            # Not counted as a lockout failure: the credential was CORRECT, and
            # counting it would only let someone lock an account that is already
            # disabled. Still audited — a valid password on a disabled account is
            # exactly the event an operator wants to see.
            await self._audit_login(
                "auth.login_failed", username, ip, user_agent,
                reason="account_disabled", actor=user,
            )
            raise unauthorized("Account is disabled")

        if settings.login_lockout_enabled:
            # A successful login proves knowledge of the password, so the run of
            # consecutive failures is over and the counter resets.
            await self._clear_user_failures(username)
        else:
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
        # Immutable audit trail of admin/reseller logins (M2). Rides the login
        # transaction (unlike the failure path, which has no transaction to ride):
        # if the trail cannot be written, the login does not silently succeed
        # unaudited. Never carries the password or the tokens just issued.
        await AuditService(self.db).log(
            action="auth.login",
            actor=user,
            target_type="user",
            target_id=user_id,
            details={"outcome": "success", "role": role},
            ip_address=(ip or "")[:45] or None,
            user_agent=(user_agent or "")[:512] or None,
        )
        await self.db.commit()

        return TokenResponse(
            access_token=access_token,
            refresh_token=refresh_token,
            expires_in=expires_in,
        )

    async def refresh(self, refresh_token: str) -> TokenResponse:
        # Validate as raw claims (no TokenPayload → no ValidationError/500 on a
        # cross/incomplete token). enforce_surface honors JWT_REQUIRE_AUD:
        # strict → require admin_refresh + aud=nexora-admin + iss; compat →
        # accept {admin_refresh, refresh}. A client/playback token is rejected.
        try:
            claims = decode_claims(refresh_token)
            enforce_surface(claims, TYPE_ADMIN_REFRESH, AUD_ADMIN, LEGACY_ADMIN_REFRESH)
        except InvalidTokenError:
            raise unauthorized("Invalid refresh token")

        jti = claims.get("jti")
        user_id = claims.get("sub")
        role = claims.get("role")
        if not jti or not user_id or not role:
            raise unauthorized("Malformed refresh token")

        session = await self.sessions.get_refresh(jti)
        if not session:
            raise unauthorized("Refresh token expired or revoked")

        # Rotate: revoke old, issue new pair
        await self.sessions.revoke_refresh(jti)

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
                claims = decode_claims(refresh_token)  # raw → no role requirement
                jti = claims.get("jti")
                if jti:
                    await self.sessions.revoke_refresh(jti)
            except InvalidTokenError:
                pass
