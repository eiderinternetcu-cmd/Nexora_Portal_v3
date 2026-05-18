"""Seed initial 21 channels into the database.

Run AFTER migration 003:
    python scripts/seed_channels.py

Idempotent: skips channels that already exist (matched by channel_key).
"""
import asyncio
import sys
from pathlib import Path

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import select
from app.database import AsyncSessionLocal
from app.models.channel import Channel  # noqa: E402 (after sys.path fix)

_CATEGORIES = {
    range(1, 8):   "general",
    range(8, 15):  "news",
    range(15, 22): "sports",
}

def _category(n: int) -> str:
    for rng, cat in _CATEGORIES.items():
        if n in rng:
            return cat
    return "general"


CHANNELS = [
    {
        "channel_key": f"canal-{i}",
        "number": i,
        "name": f"Canal {i}",
        "category": _category(i),
        "stream_key": f"canal-{i}",   # update to real Flussonic/Astra key when known
        "source_type": "manual",
        "is_active": True,
        "requires_subscription": True,
    }
    for i in range(1, 22)
]


async def main() -> None:
    async with AsyncSessionLocal() as session:
        inserted = 0
        for data in CHANNELS:
            existing = (
                await session.execute(
                    select(Channel).where(Channel.channel_key == data["channel_key"])
                )
            ).scalar_one_or_none()
            if existing is None:
                session.add(Channel(**data))
                inserted += 1

        await session.commit()

        all_ch = (
            await session.execute(select(Channel).order_by(Channel.number))
        ).scalars().all()

        print(f"Inserted {inserted} new channels. Total in DB: {len(all_ch)}")
        print()
        for ch in all_ch:
            status = "OK" if ch.is_active else "--"
            print(
                f"  [{status}] {ch.number:2d}. {ch.channel_key:<12s} {ch.name:<12s}"
                f" ({ch.category:<8s}) stream={ch.stream_key}"
            )


if __name__ == "__main__":
    asyncio.run(main())
