"""Import/sync 24 real M3U channels into the Nexora channel catalog.

Performs an UPSERT keyed on channel_key (canal-1 … canal-24).
Existing generic channels (canal-1 … canal-21 from seed_channels.py) are
replaced with real data. canal-22/23/24 are inserted as new.

Run inside Docker after migration 004:
    docker exec nexora_api python scripts/import_m3u_channels.py

Idempotent: safe to run multiple times.
"""
import asyncio
import sys
from pathlib import Path

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.database import AsyncSessionLocal
from app.models.channel import Channel  # noqa: E402

# ── Node base URLs — used to build source_url fallback ────────────────────────

_EC_MAIN = "http://181.78.246.211:8002"
_CO_MAIN = "http://38.210.187.13:8002"

# ── Channel catalog ───────────────────────────────────────────────────────────

CHANNELS = [
    # ── Co-main node (Colombia — 38.210.187.13:8002) ─────────────────────────
    {
        "number": 1,
        "name": "TELENOSTALGIA",
        "category": "peliculas",
        "stream_key": "TeleNostalgia",
        "flussonic_node": "co-main",
    },
    {
        "number": 2,
        "name": "SON CORAZON",
        "category": "peliculas",
        "stream_key": "Son_Corazon",
        "flussonic_node": "co-main",
    },
    {
        "number": 3,
        "name": "SON LATINO",
        "category": "peliculas",
        "stream_key": "Son_Latino",
        "flussonic_node": "co-main",
    },
    {
        "number": 4,
        "name": "SON POPULAR",
        "category": "peliculas",
        "stream_key": "Son_Popular",
        "flussonic_node": "co-main",
    },
    # ── Ec-main node (Ecuador — 181.78.246.211:8002) ─────────────────────────
    {
        "number": 5,
        "name": "GOLDEN PLUS",
        "category": "peliculas",
        "stream_key": "GOLDEN_PLUS",
        "flussonic_node": "ec-main",
    },
    {
        "number": 6,
        "name": "GOLDEN PREMIER 2H",
        "category": "peliculas",
        "stream_key": "GoldenPremier_2H",
        "flussonic_node": "ec-main",
    },
    {
        "number": 7,
        "name": "ESTRELLAS",
        "category": "peliculas",
        "stream_key": "ESTRELLAS_CA",
        "flussonic_node": "ec-main",
    },
    {
        "number": 8,
        "name": "TLNOVELAS",
        "category": "peliculas",
        "stream_key": "TLNOVELAS_CA",
        "flussonic_node": "ec-main",
    },
    {
        "number": 9,
        "name": "CINE HISPANO",
        "category": "peliculas",
        "stream_key": "Cine_Hispano",
        "flussonic_node": "ec-main",
    },
    {
        "number": 10,
        "name": "CINE INFANTIL",
        "category": "peliculas",
        "stream_key": "Cine_Infantil",
        "flussonic_node": "ec-main",
    },
    {
        "number": 11,
        "name": "CINE FAMILIAR",
        "category": "peliculas",
        "stream_key": "Cine_Familiar",
        "flussonic_node": "ec-main",
    },
    {
        "number": 12,
        "name": "CINE PREMIUM",
        "category": "peliculas",
        "stream_key": "Cine_Premium",
        "flussonic_node": "ec-main",
    },
    {
        "number": 13,
        "name": "DPELICULAS PLUS",
        "category": "peliculas",
        "stream_key": "DPELICULA_PLUS",
        "flussonic_node": "ec-main",
    },
    {
        "number": 14,
        "name": "TELEHIT",
        "category": "musica",
        "stream_key": "TELEHIT",
        "flussonic_node": "ec-main",
    },
    {
        "number": 15,
        "name": "TELEHIT MUSICA",
        "category": "musica",
        "stream_key": "TELEHIT_MUSICA",
        "flussonic_node": "ec-main",
    },
    {
        "number": 16,
        "name": "TELEHIT MUSICA PLUS",
        "category": "musica",
        "stream_key": "TELEHIT_MUSICA_PLUS",
        "flussonic_node": "ec-main",
    },
    {
        "number": 17,
        "name": "FOOD",
        "category": "entretenimiento",
        "stream_key": "FOOD",
        "flussonic_node": "ec-main",
    },
    {
        "number": 18,
        "name": "DISTRITO COMEDIA",
        "category": "entretenimiento",
        "stream_key": "DistritoComedia",
        "flussonic_node": "ec-main",
    },
    {
        "number": 19,
        "name": "TUDN",
        "category": "entretenimiento",
        "stream_key": "TUDN",
        "flussonic_node": "ec-main",
    },
    {
        "number": 20,
        "name": "ESPN2",
        "category": "entretenimiento",
        "stream_key": "ESPN-2CO",
        "flussonic_node": "ec-main",
    },
    {
        "number": 21,
        "name": "MI MUSICA REGUETON",
        "category": "musica",
        "stream_key": "MiMusica_Reggaeton",
        "flussonic_node": "ec-main",
    },
    {
        "number": 22,
        "name": "MI MUSICA POPULAR",
        "category": "musica",
        "stream_key": "MiMusica_Popular",
        "flussonic_node": "ec-main",
    },
    {
        "number": 23,
        "name": "MI MUSICA SALSA",
        "category": "musica",
        "stream_key": "MI_MUSICA_SALSA",
        "flussonic_node": "ec-main",
    },
    {
        "number": 24,
        "name": "MI MUSICA ROMANTICA",
        "category": "musica",
        "stream_key": "MI_MUSICA_ROMANTICA",
        "flussonic_node": "ec-main",
    },
]

_NODE_BASE = {
    "ec-main": _EC_MAIN,
    "co-main": _CO_MAIN,
}


def _build_source_url(stream_key: str, node: str, hls_path: str = "index.m3u8") -> str:
    base = _NODE_BASE.get(node, _EC_MAIN)
    return f"{base}/{stream_key}/{hls_path}"


def _enrich(ch: dict) -> dict:
    """Add channel_key, source_url and defaults to a raw channel dict."""
    node = ch["flussonic_node"]
    return {
        "channel_key": f"canal-{ch['number']}",
        "number": ch["number"],
        "name": ch["name"],
        "category": ch["category"],
        "stream_key": ch["stream_key"],
        "flussonic_node": node,
        "hls_path": "index.m3u8",
        "source_type": "flussonic",
        "source_url": _build_source_url(ch["stream_key"], node),
        "is_active": True,
        "requires_subscription": True,
    }


async def main() -> None:
    rows = [_enrich(ch) for ch in CHANNELS]

    async with AsyncSessionLocal() as session:
        # PostgreSQL UPSERT — conflict on unique channel_key
        stmt = (
            pg_insert(Channel)
            .values(rows)
            .on_conflict_do_update(
                index_elements=["channel_key"],
                set_={
                    "number": pg_insert(Channel).excluded.number,
                    "name": pg_insert(Channel).excluded.name,
                    "category": pg_insert(Channel).excluded.category,
                    "stream_key": pg_insert(Channel).excluded.stream_key,
                    "flussonic_node": pg_insert(Channel).excluded.flussonic_node,
                    "hls_path": pg_insert(Channel).excluded.hls_path,
                    "source_type": pg_insert(Channel).excluded.source_type,
                    "source_url": pg_insert(Channel).excluded.source_url,
                    "is_active": pg_insert(Channel).excluded.is_active,
                },
            )
        )
        await session.execute(stmt)
        await session.commit()

        # Report final state
        all_ch = (
            await session.execute(select(Channel).order_by(Channel.number))
        ).scalars().all()

        print(f"\n{'─'*72}")
        print(f"  Channel catalog — {len(all_ch)} total in DB")
        print(f"{'─'*72}")
        for ch in all_ch:
            status = "✓" if ch.is_active else "✗"
            print(
                f"  [{status}] {ch.number:2d}. {ch.channel_key:<10s} "
                f"{ch.name:<25s} [{ch.category:<15s}] "
                f"node={ch.flussonic_node:<8s} key={ch.stream_key}"
            )
        print(f"{'─'*72}\n")


if __name__ == "__main__":
    asyncio.run(main())
