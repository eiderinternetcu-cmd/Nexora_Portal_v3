"""Seed plan_channels: include the current active channels in a plan.

Goal (P0-009): the annual test plan must include the 24 active channels so
that enabling entitlement enforcement does NOT break testuser1.

Idempotent: ON CONFLICT (plan_id, channel_id) DO NOTHING.

Run inside the api container AFTER migration 005:
    docker exec nexora_api python scripts/seed_plan_channels.py
Options (env):
    SEED_PLAN_NAME   target plan by name (default: "Plan Anual 365 dias")
    SEED_ALL_PLANS=1 seed every active plan with every active channel
"""
import asyncio
import os
import sys
from pathlib import Path

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.database import AsyncSessionLocal
from app.models.plan import Plan  # noqa: E402
from app.models.channel import Channel  # noqa: E402
from app.models.plan_channel import PlanChannel  # noqa: E402

_TARGET_PLAN = os.environ.get("SEED_PLAN_NAME", "Plan Anual 365 dias")
_ALL_PLANS = os.environ.get("SEED_ALL_PLANS") == "1"


async def main() -> None:
    async with AsyncSessionLocal() as session:
        channels = (
            await session.execute(
                select(Channel.id, Channel.channel_key).where(Channel.is_active.is_(True))
            )
        ).all()
        if not channels:
            print("No active channels found — nothing to seed.")
            return

        if _ALL_PLANS:
            plans = (await session.execute(select(Plan).where(Plan.is_active.is_(True)))).scalars().all()
        else:
            plans = (
                await session.execute(select(Plan).where(Plan.name == _TARGET_PLAN))
            ).scalars().all()

        if not plans:
            existing = (await session.execute(select(Plan.name))).scalars().all()
            print(f"Target plan '{_TARGET_PLAN}' not found. Existing plans: {existing}")
            print("Set SEED_PLAN_NAME or SEED_ALL_PLANS=1.")
            return

        total_inserted = 0
        for plan in plans:
            rows = [
                {"plan_id": plan.id, "channel_id": ch_id, "is_enabled": True}
                for (ch_id, _key) in channels
            ]
            stmt = (
                pg_insert(PlanChannel)
                .values(rows)
                .on_conflict_do_nothing(index_elements=["plan_id", "channel_id"])
            )
            result = await session.execute(stmt)
            total_inserted += result.rowcount or 0
            print(f"  plan '{plan.name}': {len(rows)} channels ensured")

        await session.commit()

        print(f"\n{'─'*64}")
        print(f"  Seeded plan_channels — {total_inserted} new row(s)")
        for plan in plans:
            cnt = (
                await session.execute(
                    select(PlanChannel).where(PlanChannel.plan_id == plan.id)
                )
            ).scalars().all()
            print(f"  [{plan.name}] now includes {len(cnt)} channels")
        print(f"{'─'*64}\n")


if __name__ == "__main__":
    asyncio.run(main())
