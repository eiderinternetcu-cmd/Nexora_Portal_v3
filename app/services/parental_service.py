"""
ParentalService — server-side parental control for censored channels (NX-PARENTAL).

WHERE THE CONTROL LIVES, AND WHY THERE
──────────────────────────────────────
At the two routes that MINT a playback token — POST /api/client/playback/authorize
and GET /api/client/playback/{channel_id} (reissue) — and BEFORE StreamAuthService
is called at all, so a denial creates no IPTV session, no connection slot, no
token and no URL. Exactly the discipline the entitlement gate already follows.

Not in the client. A PIN checked by the app is a suggestion, not a control: the
app is code the household runs, it can be patched or simply skipped, and the
token is minted by an HTTP call anyone can replay with curl and a valid client
token. The only place a check cannot be walked around is the server, at the
moment the credential for the stream is issued.

Not in the catalog either. Filtering rows out of a listing is UX, not a gate —
the same argument ChannelService already makes for CATALOG_ENTITLEMENT_FILTER.
A censored channel stays listed (flagged), and it is this gate that decides
whether it can actually be played.

The reissue route is gated too, not only /authorize. It mints a token bound to
whatever channel it is asked for, so gating only /authorize would leave the
adult tier one query parameter away for any client with an open session.

PROTECTING A 4-6 DIGIT SECRET
─────────────────────────────
The PIN is a credential, so it is stored as an Argon2id hash through the
project's single hashing path (app/core/security.hash_password) and never in
the clear — not in the DB, not in a log line, not in a response body.

But the hash is NOT what protects it, and pretending otherwise would be the
whole bug. The keyspace is 10^4..10^6. Argon2id at the configured cost
(64 MB, t=3) buys roughly a second per handful of guesses on a stolen dump —
hours of GPU-hostile work for a 6-digit PIN, minutes for a 4-digit one. Good
value for a dump, worthless against someone typing at the API.

What actually protects a PIN is that an attacker cannot make many ONLINE
guesses. So the load-bearing control here is the attempt limiter, lifted from
the hardened admin lockout in AuthService (same Redis helpers, same three
properties):

  * the window is anchored on the FIRST failure (expire set only when the
    counter is created) rather than refreshed on every failure, so the contract
    is the one we document — N failures within WINDOW — and a slow drip of
    guesses over hours cannot accumulate into a lockout;
  * once the block arms, the counter is DELETED, so an attacker who keeps
    hammering while blocked cannot extend the block indefinitely;
  * a success clears both keys — knowing the PIN ends the run of failures.

Counted per SUBSCRIBER, never per IP and never per device:
  * the IP arrives in a client-controlled header (see CLIENT_IP_SOURCE in
    config.py), so an IP-keyed counter is both evadable and abusable;
  * a per-device counter would be free attempts on demand — rotate the
    device_id and the counter resets, which is the same evasion in a different
    field. The subscriber is the axis a PIN brute force has to commit to.

Unlike the admin login, the caller here is ALREADY authenticated as the
subscriber whose PIN is being guessed. There is no account enumeration to
protect and no way to lock out a stranger, so the lockout is reported honestly
(423 with the remaining window) instead of being disguised as a wrong PIN: the
person most likely to hit it is the parent who fat-fingered it five times, and
telling them "wrong PIN" while refusing a correct one is a support ticket.

THE UNLOCK IS A SEPARATE STEP, NOT A FIELD ON /authorize
────────────────────────────────────────────────────────
POST /api/client/parental/unlock verifies the PIN once and leaves a short-lived
grant in Redis; playback then only checks that grant (one EXISTS). Carrying the
PIN in every /authorize would put the secret on the zapping path — one copy per
channel change, in every retry, proxy buffer, crash dump and client log — and
would spread the attempt limiter across the hot path instead of one small,
auditable surface. It would also drag a 64 MB Argon2id verification into every
channel change.

The grant is keyed by subscriber AND device (see key_parental_unlock), and its
TTL slides on each successful use, so a session on an adult channel does not die
mid-programme (the player reissues a token roughly every 45s) while leaving that
channel for longer than the window re-arms the prompt.
"""
import uuid

import redis.asyncio as aioredis
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core.exceptions import NexoraException
from app.core.reason_codes import ReasonCode
from app.core.security import hash_password, verify_password
from app.models.channel import Channel
from app.models.subscriber import Subscriber
from app.redis_client import key_login_attempts, key_lockout, key_parental_unlock

settings = get_settings()


def _deny(code: ReasonCode, message: str, status_code: int = 403) -> NexoraException:
    """A refusal that carries a machine-readable reason_code.

    app/main.py's handler already flattens a {"message", "reason_code"} detail
    into `error` + `reason_code`, but nothing emitted that shape until now:
    every existing deny sends the bare code as `error`, so clients string-match
    a human-facing field. A brand-new surface is the cheap place to use the
    contract the handler documents instead of adding one more string to match.

    Never carries the PIN, the hash, or anything derived from either.
    """
    return NexoraException(
        status_code=status_code,
        detail={"message": message, "reason_code": code.value},
    )


class ParentalService:
    def __init__(self, db: AsyncSession, redis: aioredis.Redis) -> None:
        self.db = db
        self.redis = redis

    # ── Attempt limiting (the control that actually protects the PIN) ────────

    @staticmethod
    def _lockout_id(subscriber_id: uuid.UUID) -> str:
        """Redis identifier for the PIN counter: `parental:{subscriber_id}`.

        Follows the existing convention (AuthService uses `admin:{username}`,
        ClientAuthService `sub:{username}`) and keeps this namespace disjoint
        from every other counter, so a PIN lockout never blocks a login and a
        login lockout never blocks a PIN.

        The subscriber id is a UUID from a validated token, never client text,
        so there is no unbounded-key-name concern here.
        """
        return f"parental:{subscriber_id}"

    async def lockout_ttl(self, subscriber_id: uuid.UUID) -> int:
        """Seconds left on an active PIN lockout, or 0 when not blocked."""
        key = key_lockout(self._lockout_id(subscriber_id))
        if not await self.redis.exists(key):
            return 0
        return max(int(await self.redis.ttl(key)), 1)

    async def _record_failure(self, subscriber_id: uuid.UUID) -> None:
        """Count one consecutive wrong PIN; arm the block at max_attempts.

        See the module docstring for why the window is anchored on the first
        failure and why the counter is dropped once the block arms.
        """
        identifier = self._lockout_id(subscriber_id)
        key = key_login_attempts(identifier)
        attempts = await self.redis.incr(key)
        if attempts == 1:
            await self.redis.expire(key, settings.parental_pin_attempt_window_seconds)
        if attempts >= settings.parental_pin_max_attempts:
            await self.redis.setex(
                key_lockout(identifier), settings.parental_pin_lockout_seconds, "1"
            )
            await self.redis.delete(key)

    async def _clear_failures(self, subscriber_id: uuid.UUID) -> None:
        identifier = self._lockout_id(subscriber_id)
        await self.redis.delete(key_login_attempts(identifier))
        await self.redis.delete(key_lockout(identifier))

    async def _verify_pin_or_fail(self, subscriber: Subscriber, pin: str) -> None:
        """Check `pin` against the stored hash, counting the attempt.

        Every path that compares a candidate PIN goes through here — unlock AND
        change-PIN. If the change path had its own untracked comparison it would
        be an unthrottled oracle for exactly the secret the unlock path
        throttles, which is the more convenient of the two doors to attack.
        """
        ttl = await self.lockout_ttl(subscriber.id)
        if ttl:
            raise _deny(
                ReasonCode.PARENTAL_PIN_LOCKED,
                f"Too many incorrect PINs. Try again in {ttl}s.",
                status_code=423,
            )

        if not subscriber.parental_pin_hash or not verify_password(
            pin, subscriber.parental_pin_hash
        ):
            await self._record_failure(subscriber.id)
            raise _deny(ReasonCode.PARENTAL_PIN_INVALID, "Incorrect PIN")

        await self._clear_failures(subscriber.id)

    # ── PIN lifecycle ────────────────────────────────────────────────────────

    async def set_pin(
        self, subscriber: Subscriber, new_pin: str, current_pin: str | None = None
    ) -> None:
        """Set the PIN for the first time, or change an existing one.

        FIRST SET (no hash stored): `current_pin` is not required. The client
        access token already proves the account holder authenticated with the
        account credential; demanding a PIN they have never had would make the
        feature unreachable.

        CHANGE (a hash exists): `current_pin` is REQUIRED and verified. Without
        it, whoever holds an already-logged-in device could silently reset the
        PIN and the control would evaporate — and "someone else is holding a
        logged-in device" is not an edge case here, it is the entire threat
        model of parental control. A wrong `current_pin` counts toward the same
        limiter as a failed unlock.

        Every unlock grant of this subscriber is dropped afterwards: the PIN
        changed, so consent given under the old one no longer means anything.
        """
        if subscriber.parental_pin_hash:
            if not current_pin:
                raise _deny(
                    ReasonCode.PARENTAL_PIN_INVALID,
                    "Changing the PIN requires the current one",
                )
            await self._verify_pin_or_fail(subscriber, current_pin)

        subscriber.parental_pin_hash = hash_password(new_pin)
        self.db.add(subscriber)
        await self.revoke_unlocks(subscriber.id)

    # ── Unlock grants ────────────────────────────────────────────────────────

    async def unlock(
        self, subscriber: Subscriber, device_id: str, pin: str
    ) -> int:
        """Verify the PIN and open an unlock window for this device.

        Returns the grant TTL in seconds. Raises 403 on a wrong PIN (counted),
        423 while the limiter is blocking, 403 when no PIN is configured at all.
        """
        if not subscriber.parental_pin_hash:
            # Nothing to verify against. Reported as its own code so the client
            # sends the user to "set a PIN" instead of re-prompting forever.
            raise _deny(ReasonCode.PARENTAL_PIN_NOT_SET, "No parental PIN is configured")

        await self._verify_pin_or_fail(subscriber, pin)

        ttl = settings.parental_pin_unlock_ttl_seconds
        await self.redis.setex(
            key_parental_unlock(str(subscriber.id), device_id), ttl, "1"
        )
        return ttl

    async def is_unlocked(self, subscriber_id: uuid.UUID, device_id: str) -> bool:
        """True when this device holds a live unlock grant, renewing its TTL.

        Sliding, for the same reason the segment grant slides: the player asks
        for a fresh token every ~45s, so a fixed window would cut an adult
        programme off mid-scene. Renewing only here means the window measures
        time away from censored content, not time since the PIN was typed.
        """
        key = key_parental_unlock(str(subscriber_id), device_id)
        if not await self.redis.exists(key):
            return False
        await self.redis.expire(key, settings.parental_pin_unlock_ttl_seconds)
        return True

    async def revoke_unlocks(self, subscriber_id: uuid.UUID) -> None:
        """Drop every unlock grant of a subscriber (all devices)."""
        pattern = key_parental_unlock(str(subscriber_id), "*")
        async for key in self.redis.scan_iter(match=pattern):
            await self.redis.delete(key)

    # ── The gate ─────────────────────────────────────────────────────────────

    async def require_channel_access(
        self, subscriber: Subscriber, device_id: str, channel: Channel | None
    ) -> None:
        """Raise 403 unless this subscriber+device may play `channel` right now.

        Called by the client playback routes before any token is minted. A
        no-op when the flag is off or the channel is not censored, so the
        default deployment path is one boolean read.

        NO PIN CONFIGURED → BLOCKED, deliberately.
        Allowing it would mean the default state of every account is "parental
        control off", so the control would only exist for the households that
        already opted in — precisely the ones that did not need it. Failing
        closed also means a provisioning bug (a lost hash, a half-applied
        migration) degrades to "channel unavailable" rather than to "adult
        content for everyone", and the cost is one setup step that the
        PARENTAL_PIN_NOT_SET code lets the client walk the user through.
        The blast radius of that choice is bounded by the flag: on the day it
        is flipped, every household that has not set a PIN loses the adult tier
        until they do, which is a rollout decision to announce, not a surprise.
        """
        if not settings.parental_control_enforce:
            return
        if channel is None or not channel.censored:
            return
        if not subscriber.parental_pin_hash:
            raise _deny(
                ReasonCode.PARENTAL_PIN_NOT_SET,
                "This channel is restricted and no parental PIN is configured",
            )
        if not await self.is_unlocked(subscriber.id, device_id):
            raise _deny(
                ReasonCode.PARENTAL_PIN_REQUIRED,
                "This channel is restricted — enter the parental PIN",
            )
