"""
SubscriptionService — CRUD de suscripciones de suscriptores IPTV.

Responsabilidades:
  - Crear suscripción (desactiva la activa previa si existe)
  - Listar historial de suscripciones
  - Renovar (extiende desde expires_at o desde ahora si vencida)
  - Cancelar (is_active=False, no borra registro)

La revocación de sesiones IPTV al cancelar se coordina en el endpoint
para evitar dependencia de Redis en este servicio.
"""
import uuid
from datetime import datetime, timezone, timedelta

from sqlalchemy import select
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.plan import Plan
from app.models.subscriber import Subscriber
from app.models.subscription import Subscription
from app.core.exceptions import not_found, bad_request


class SubscriptionService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    # ── Internal loaders ─────────────────────────────────────────────────────

    async def _get_subscriber(self, sub_id: uuid.UUID) -> Subscriber:
        result = await self.db.execute(
            select(Subscriber).where(Subscriber.id == sub_id)
        )
        sub = result.scalar_one_or_none()
        if sub is None:
            raise not_found("Subscriber")
        return sub

    async def _get_plan(self, plan_id: uuid.UUID) -> Plan:
        result = await self.db.execute(
            select(Plan).where(Plan.id == plan_id)
        )
        plan = result.scalar_one_or_none()
        if plan is None:
            raise not_found("Plan")
        if not plan.is_active:
            raise bad_request(f"Plan '{plan.name}' is inactive and cannot be assigned")
        return plan

    async def _get_subscription(
        self, subscription_id: uuid.UUID, subscriber_id: uuid.UUID
    ) -> Subscription:
        """Load a subscription, verifying it belongs to the subscriber.
        Returns 404 for both 'not found' and 'wrong subscriber' to avoid info leakage.
        """
        result = await self.db.execute(
            select(Subscription)
            .options(selectinload(Subscription.plan))
            .where(Subscription.id == subscription_id)
        )
        sub = result.scalar_one_or_none()
        if sub is None or sub.subscriber_id != subscriber_id:
            raise not_found("Subscription")
        return sub

    async def _get_current_active(
        self, subscriber_id: uuid.UUID
    ) -> Subscription | None:
        now = datetime.now(timezone.utc)
        result = await self.db.execute(
            select(Subscription)
            .where(
                Subscription.subscriber_id == subscriber_id,
                Subscription.is_active.is_(True),
                Subscription.expires_at > now,
            )
            .order_by(Subscription.expires_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def _reload_with_plan(self, subscription_id: uuid.UUID) -> Subscription:
        result = await self.db.execute(
            select(Subscription)
            .options(selectinload(Subscription.plan))
            .where(Subscription.id == subscription_id)
        )
        return result.scalar_one()

    # ── Public API ────────────────────────────────────────────────────────────

    async def create(
        self,
        subscriber_id: uuid.UUID,
        plan_id: uuid.UUID,
        actor_id: uuid.UUID | None = None,
        renewal_note: str | None = None,
    ) -> Subscription:
        """
        Create a new subscription for a subscriber.
        If an active subscription already exists, it is deactivated first
        (soft-replaced — old record is kept for history with is_active=False).
        """
        await self._get_subscriber(subscriber_id)
        plan = await self._get_plan(plan_id)

        existing = await self._get_current_active(subscriber_id)
        if existing is not None:
            existing.is_active = False
            await self.db.flush()

        now = datetime.now(timezone.utc)
        subscription = Subscription(
            subscriber_id=subscriber_id,
            plan_id=plan.id,
            starts_at=now,
            expires_at=now + timedelta(days=plan.duration_days),
            is_active=True,
            created_by=actor_id,
            renewal_note=renewal_note,
        )
        self.db.add(subscription)
        await self.db.flush()
        return await self._reload_with_plan(subscription.id)

    async def list_for_subscriber(self, subscriber_id: uuid.UUID) -> list[Subscription]:
        """Return all subscriptions for a subscriber, newest first."""
        await self._get_subscriber(subscriber_id)
        result = await self.db.execute(
            select(Subscription)
            .options(selectinload(Subscription.plan))
            .where(Subscription.subscriber_id == subscriber_id)
            .order_by(Subscription.starts_at.desc())
        )
        return list(result.scalars().all())

    async def renew(
        self,
        subscriber_id: uuid.UUID,
        subscription_id: uuid.UUID,
        plan_id: uuid.UUID | None = None,
        renewal_note: str | None = None,
        actor_id: uuid.UUID | None = None,
    ) -> Subscription:
        """
        Renew a subscription:
          - If plan_id provided and different, switch plan.
          - Extends expires_at from the current expires_at (if not yet expired)
            or from now (if already expired).
          - Always sets is_active=True.
        """
        sub = await self._get_subscription(subscription_id, subscriber_id)

        if plan_id is not None and plan_id != sub.plan_id:
            plan = await self._get_plan(plan_id)
            sub.plan_id = plan.id
        else:
            plan = sub.plan  # loaded via selectinload

        now = datetime.now(timezone.utc)
        base = sub.expires_at
        if base.tzinfo is None:
            base = base.replace(tzinfo=timezone.utc)
        base = max(base, now)  # extend from now if already expired

        sub.expires_at = base + timedelta(days=plan.duration_days)
        sub.is_active = True
        if renewal_note is not None:
            sub.renewal_note = renewal_note
        if actor_id is not None:
            sub.created_by = actor_id

        await self.db.flush()
        return await self._reload_with_plan(sub.id)

    async def cancel(
        self,
        subscriber_id: uuid.UUID,
        subscription_id: uuid.UUID,
    ) -> Subscription:
        """
        Cancel a subscription: sets is_active=False. Record is kept for history.
        Raises 400 if already inactive.
        Session revocation is handled at the endpoint level.
        """
        sub = await self._get_subscription(subscription_id, subscriber_id)
        if not sub.is_active:
            raise bad_request("Subscription is already inactive")
        sub.is_active = False
        await self.db.flush()
        return sub
