"""
ChannelService — local channel catalog.
READ-ONLY with respect to Flussonic/Astra — no stream modification ever.

Catalog entitlements (CATALOG_ENTITLEMENT_FILTER, default OFF)
--------------------------------------------------------------
Historically the client catalog returned EVERY active channel, while the plan
filter (plan_channels) was applied only at playback time. With
ENTITLEMENT_ENFORCE=True that guarantees a bad experience: the client renders a
channel it cannot play and the user finds out by pressing Play and getting a
403 CHANNEL_NOT_INCLUDED.

With CATALOG_ENTITLEMENT_FILTER=True the catalog is narrowed to the channels of
the plan currently in force for the subscriber, so "what I can see" == "what I
can play". With the flag OFF the behaviour is byte-for-byte the previous one.

Fallback when there is NO plan in force (no subscription, not started, expired,
or plan deactivated): return the FULL active catalog rather than an empty list.
  * A user whose subscription lapsed must still see what they get back when they
    renew — an empty grid is a dead end and kills the renewal path.
  * An empty catalog is indistinguishable, from the client's point of view, from
    a broken backend → support tickets and bad reviews.
  * Hiding is NOT a security control. The actual gate is EntitlementService at
    playback (ENTITLEMENT_ENFORCE); an expired user who sees the grid still gets
    denied on Play. Catalog filtering is pure UX.
  * It is also the least surprising failure mode: it degrades to today's exact
    behaviour instead of to a blank screen.

Cost: at most TWO queries regardless of catalog size (one to resolve the plan in
force, one JOIN against plan_channels). No per-channel lookups, no N+1.
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models.channel import Channel
from app.models.plan import Plan
from app.models.plan_channel import PlanChannel
from app.models.subscription import Subscription
from app.core.exceptions import not_found

settings = get_settings()


class ChannelService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def list_active(self) -> list[Channel]:
        result = await self.db.execute(
            select(Channel)
            .where(Channel.is_active.is_(True))
            .order_by(Channel.number)
        )
        return list(result.scalars().all())

    async def list_all(self) -> list[Channel]:
        result = await self.db.execute(select(Channel).order_by(Channel.number))
        return list(result.scalars().all())

    async def get_by_key(self, channel_key: str) -> Channel | None:
        result = await self.db.execute(
            select(Channel).where(Channel.channel_key == channel_key)
        )
        return result.scalar_one_or_none()

    async def get_by_id(self, channel_id: uuid.UUID) -> Channel | None:
        result = await self.db.execute(
            select(Channel).where(Channel.id == channel_id)
        )
        return result.scalar_one_or_none()

    async def get_active_by_key(self, channel_key: str) -> Channel:
        """404 if channel doesn't exist or is inactive (same error — no info leak)."""
        ch = await self.get_by_key(channel_key)
        if ch is None or not ch.is_active:
            raise not_found("Channel")
        return ch

    # ── Entitlement-aware catalog ────────────────────────────────────────────

    async def resolve_plan_in_force(self, subscriber_id: uuid.UUID) -> Plan | None:
        """The plan currently in force for a subscriber, or None.

        Deliberately mirrors steps 3-7 of
        EntitlementService.can_watch_channel — which stays the single source of
        truth for playback: newest ACTIVE subscription, inside its date window,
        on an ACTIVE plan. The rules are not re-invented here; if they change
        there, they must change here, otherwise the catalog would again drift
        from what playback allows.

        One query.
        """
        now = datetime.now(timezone.utc)
        row = (
            await self.db.execute(
                select(Subscription, Plan)
                .join(Plan, Subscription.plan_id == Plan.id)
                .where(
                    Subscription.subscriber_id == subscriber_id,
                    Subscription.is_active.is_(True),
                )
                .order_by(Subscription.expires_at.desc())
                .limit(1)
            )
        ).first()
        if row is None:
            return None
        subscription, plan = row
        if _aware(subscription.starts_at) > now:      # not started
            return None
        if _aware(subscription.expires_at) <= now:    # expired
            return None
        if not plan.is_active:                        # plan deactivated
            return None
        return plan

    async def list_active_for_plan(self, plan_id: uuid.UUID) -> list[Channel]:
        """Active channels included (and enabled) in a plan.

        Single JOIN against plan_channels — constant query count whatever the
        catalog size. plan_channels has a UNIQUE(plan_id, channel_id), so no
        DISTINCT is needed and no row can be duplicated.
        """
        result = await self.db.execute(
            select(Channel)
            .join(PlanChannel, PlanChannel.channel_id == Channel.id)
            .where(
                Channel.is_active.is_(True),
                PlanChannel.plan_id == plan_id,
                PlanChannel.is_enabled.is_(True),
            )
            .order_by(Channel.number)
        )
        return list(result.scalars().all())

    async def list_visible_for_subscriber(
        self, subscriber_id: uuid.UUID
    ) -> list[Channel]:
        """The catalog as this subscriber should see it. See module docstring.

        Flag OFF  → identical to list_active() (1 query, current behaviour).
        Flag ON   → plan channels only (2 queries), or the full active catalog
                    when no plan is in force (2 queries).
        """
        if not settings.catalog_entitlement_filter:
            return await self.list_active()

        plan = await self.resolve_plan_in_force(subscriber_id)
        if plan is None:
            # No plan in force → show everything (deliberate: see module docstring).
            return await self.list_active()
        return await self.list_active_for_plan(plan.id)


def _aware(dt: datetime) -> datetime:
    """Treat naive datetimes (from DB) as UTC for safe comparison."""
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)
