"""PlanChannelService — read and write the plan_channels whitelist.

plan_channels is a STRICT whitelist. EntitlementService.can_watch_channel denies
with CHANNEL_NOT_INCLUDED any channel that has no is_enabled=True row for the
subscriber's plan, so a plan with ZERO rows grants access to NOTHING (not to
everything). Every write in this module therefore changes, at once, what every
subscriber of that plan can watch — hence the audit trail at the endpoint layer.

Transaction model: this service only flushes. The request-scoped session opened
by `get_db` owns the COMMIT (and the ROLLBACK on any exception), so the
delete + upsert pair of `replace()` lands as one atomic unit — the whitelist is
never observable in a half-replaced state.

Idempotency: inserts use PostgreSQL ON CONFLICT on the
uq_plan_channels_plan_channel unique constraint, like scripts/seed_plan_channels.py.
DO UPDATE (not DO NOTHING) is used deliberately — see `_upsert`.
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NexoraException, not_found
from app.models.channel import Channel
from app.models.plan import Plan
from app.models.plan_channel import PlanChannel
from app.schemas.plan_channel import PlanChannelOut, PlanChannelsSummary


def _unprocessable(detail: str) -> NexoraException:
    """422 — the body is well-formed but references rows that do not exist.
    Raised BEFORE touching plan_channels so a bad channel_id can never reach the
    database as a foreign-key violation (which would surface as a 500)."""
    return NexoraException(status_code=422, detail=detail)


class PlanChannelService:
    def __init__(self, db: AsyncSession):
        self.db = db

    # ── helpers ──────────────────────────────────────────────────────────────

    async def _get_plan(self, plan_id: uuid.UUID) -> Plan:
        plan = (
            await self.db.execute(select(Plan).where(Plan.id == plan_id))
        ).scalar_one_or_none()
        if plan is None:
            raise not_found("Plan")
        return plan

    async def _get_channel(self, channel_id: uuid.UUID) -> Channel:
        channel = (
            await self.db.execute(select(Channel).where(Channel.id == channel_id))
        ).scalar_one_or_none()
        if channel is None:
            raise not_found("Channel")
        return channel

    async def _rows(self, plan_id: uuid.UUID) -> dict[uuid.UUID, bool]:
        """channel_id → is_enabled for every existing plan_channels row."""
        result = await self.db.execute(
            select(PlanChannel.channel_id, PlanChannel.is_enabled).where(
                PlanChannel.plan_id == plan_id
            )
        )
        return {channel_id: is_enabled for channel_id, is_enabled in result.all()}

    @staticmethod
    def _enabled(rows: dict[uuid.UUID, bool]) -> set[uuid.UUID]:
        return {cid for cid, is_enabled in rows.items() if is_enabled}

    async def _assert_channels_exist(self, channel_ids: list[uuid.UUID]) -> None:
        if not channel_ids:
            return
        found = set(
            (
                await self.db.execute(
                    select(Channel.id).where(Channel.id.in_(channel_ids))
                )
            ).scalars().all()
        )
        missing = [str(cid) for cid in channel_ids if cid not in found]
        if missing:
            raise _unprocessable(f"Unknown channel_ids: {', '.join(missing)}")

    async def _upsert(
        self,
        plan_id: uuid.UUID,
        channel_ids: list[uuid.UUID],
        actor_id: uuid.UUID | None,
    ) -> None:
        """Insert the missing memberships, idempotently.

        ON CONFLICT DO **UPDATE** SET is_enabled = TRUE rather than DO NOTHING:
        is_enabled=False is a soft exclusion (EntitlementService and the filtered
        catalog both ignore such rows), so DO NOTHING would answer 200 while the
        channel stayed unwatchable — a silent lie to the panel. Re-running with
        an already-enabled row is still a no-op in effect.
        """
        if not channel_ids:
            return
        now = datetime.now(timezone.utc)
        stmt = pg_insert(PlanChannel).values(
            [
                {
                    "id": uuid.uuid4(),
                    "plan_id": plan_id,
                    "channel_id": channel_id,
                    "is_enabled": True,
                    "created_at": now,
                    "created_by_admin_id": actor_id,
                }
                for channel_id in channel_ids
            ]
        )
        await self.db.execute(
            stmt.on_conflict_do_update(
                constraint="uq_plan_channels_plan_channel",
                set_={"is_enabled": True},
            )
        )

    async def _delete_rows(
        self, plan_id: uuid.UUID, channel_ids: set[uuid.UUID] | list[uuid.UUID]
    ) -> int:
        result = await self.db.execute(
            delete(PlanChannel)
            .where(
                PlanChannel.plan_id == plan_id,
                PlanChannel.channel_id.in_(list(channel_ids)),
            )
            .execution_options(synchronize_session="fetch"),
        )
        return result.rowcount or 0

    async def _summary(
        self, plan_id: uuid.UUID, added: int, removed: int
    ) -> PlanChannelsSummary:
        await self.db.flush()
        total = len(self._enabled(await self._rows(plan_id)))
        return PlanChannelsSummary(
            plan_id=plan_id, added=added, removed=removed, total=total
        )

    # ── read ─────────────────────────────────────────────────────────────────

    async def list_for_plan(
        self, plan_id: uuid.UUID, include_excluded: bool = False
    ) -> list[PlanChannelOut]:
        """The plan's channels, ordered by channel number.

        include_excluded=True returns the FULL catalog (inactive channels
        included, so the panel can show why a channel is unusable) with
        `included` marking membership — the edit screen resolves in one call.
        """
        await self._get_plan(plan_id)
        enabled = self._enabled(await self._rows(plan_id))

        if include_excluded:
            query = select(Channel).order_by(Channel.number)
        elif enabled:
            query = (
                select(Channel)
                .where(Channel.id.in_(enabled))
                .order_by(Channel.number)
            )
        else:
            return []

        channels = (await self.db.execute(query)).scalars().all()
        return [
            PlanChannelOut(
                id=channel.id,
                channel_key=channel.channel_key,
                number=channel.number,
                name=channel.name,
                category=channel.category,
                logo_url=channel.logo_url,
                is_active=channel.is_active,
                included=channel.id in enabled,
            )
            for channel in channels
        ]

    # ── write ────────────────────────────────────────────────────────────────

    async def replace(
        self,
        plan_id: uuid.UUID,
        channel_ids: list[uuid.UUID],
        actor_id: uuid.UUID | None = None,
    ) -> PlanChannelsSummary:
        """Atomic full replacement of the plan's whitelist.

        Order inside the single request transaction: validate → delete the
        surplus → upsert the requested set. Repeating the same call is a no-op
        (added=0, removed=0).
        """
        await self._get_plan(plan_id)
        target = list(dict.fromkeys(channel_ids))  # de-duplicate, keep order
        await self._assert_channels_exist(target)

        rows = await self._rows(plan_id)
        target_set = set(target)
        to_delete = set(rows) - target_set

        removed = await self._delete_rows(plan_id, to_delete) if to_delete else 0
        await self._upsert(plan_id, target, actor_id)

        added = len(target_set - self._enabled(rows))
        return await self._summary(plan_id, added=added, removed=removed)

    async def add(
        self,
        plan_id: uuid.UUID,
        channel_id: uuid.UUID,
        actor_id: uuid.UUID | None = None,
    ) -> PlanChannelsSummary:
        """Include one channel. Repeating the call succeeds with added=0."""
        await self._get_plan(plan_id)
        await self._get_channel(channel_id)

        was_included = channel_id in self._enabled(await self._rows(plan_id))
        await self._upsert(plan_id, [channel_id], actor_id)
        return await self._summary(plan_id, added=0 if was_included else 1, removed=0)

    async def remove(
        self, plan_id: uuid.UUID, channel_id: uuid.UUID
    ) -> PlanChannelsSummary:
        """Exclude one channel by deleting its row (no soft-disable, so the
        table keeps exactly one meaning). Removing a channel that was not in
        the plan succeeds with removed=0."""
        await self._get_plan(plan_id)
        await self._get_channel(channel_id)

        removed = await self._delete_rows(plan_id, [channel_id])
        return await self._summary(plan_id, added=0, removed=removed)
