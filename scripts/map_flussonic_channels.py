"""
Mapeo de canales DB -> streams Flussonic.

Uso:
    python scripts/map_flussonic_channels.py

1. Lista los streams disponibles en Flussonic.
2. Lista los canales actuales en DB.
3. Aplica el mapeo definido en CHANNEL_MAP (editar antes de ejecutar).

Editar CHANNEL_MAP con los nombres reales de tus streams en Flussonic.
El script es idempotente — puede ejecutarse múltiples veces.
Nunca modifica Flussonic. Solo actualiza stream_key y name en DB.
"""
import asyncio
import sys
from pathlib import Path

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import select
from app.database import AsyncSessionLocal
from app.models.channel import Channel
from app.integrations.flussonic_client import get_flussonic_client

# ── EDITAR ESTE MAPEO ────────────────────────────────────────────────────────
# Formato: "channel_key_en_DB": ("Nombre visible", "STREAM_NAME_EN_FLUSSONIC", "categoria")
# Dejar vacío ("") el stream_name para no sobrescribir los que no conoces aún.
# Categorías disponibles: general, news, sports, movies, music, kids
CHANNEL_MAP: dict[str, tuple[str, str, str]] = {
    "canal-1":  ("Ecuador TV",           "ECUADOR_TV",             "general"),
    "canal-2":  ("GamaTv",               "GAMATV",                 "general"),
    "canal-3":  ("Televicentro",         "TELEVICENTRO",           "general"),
    "canal-4":  ("RCN",                  "RCN",                    "general"),
    "canal-5":  ("Canal Uno",            "CANAL_UNO",              "general"),
    "canal-6":  ("Canal Uno Ecu",        "CANAL_UNO_ECU",          "general"),
    "canal-7":  ("Zaracay TV",           "ZARACAY_TV",             "general"),
    "canal-8":  ("Noticiero 24/7",       "Noticiero_24/7",         "news"),
    "canal-9":  ("Canal Local",          "CANAL_LOCAL",            "news"),
    "canal-10": ("Teleandina",           "TELEANDINA",             "news"),
    "canal-11": ("Telemar",              "TELEMAR",                "news"),
    "canal-12": ("Telemar ESM",          "TELEMAR_ESM",            "news"),
    "canal-13": ("Caracol Internacional","CARACOL_INTERNACIONAL",  "news"),
    "canal-14": ("UCSG TV",              "UCSG_TV",                "news"),
    "canal-15": ("ESPN",                 "ESPN-CO",                "sports"),
    "canal-16": ("ESPN 2",               "ESPN-2CO",               "sports"),
    "canal-17": ("ESPN 3",               "ESPN-3CO",               "sports"),
    "canal-18": ("ESPN 4",               "ESPN-4CO",               "sports"),
    "canal-19": ("TUDN",                 "TUDN",                   "sports"),
    "canal-20": ("Adrenalina",           "ADRENALINA",             "sports"),
    "canal-21": ("Oromar",               "OROMAR",                 "sports"),
}
# ─────────────────────────────────────────────────────────────────────────────


async def main() -> None:
    flussonic = get_flussonic_client()

    # Show available Flussonic streams
    if flussonic.is_configured:
        print("=== Streams en Flussonic ===")
        streams = await flussonic.list_streams()
        live = sum(1 for s in streams if s.get("alive"))
        print(f"Total: {len(streams)}  |  Live: {live}  |  Down: {len(streams) - live}")
        for s in sorted(streams, key=lambda x: x.get("name", "")):
            status = "LIVE" if s.get("alive") else "down"
            print(f"  [{status}] {s.get('name', '?')}")
        print()
    else:
        print("WARNING: Flussonic not configured — only updating DB names/categories.\n")

    # Apply mapping to DB
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(Channel).order_by(Channel.number))
        channels = list(result.scalars().all())

        print("=== Actualizando canales en DB ===")
        updated = 0
        for ch in channels:
            mapping = CHANNEL_MAP.get(ch.channel_key)
            if not mapping:
                print(f"  SKIP  {ch.channel_key} (sin mapeo)")
                continue

            name, stream_name, category = mapping
            changed = []

            if name and ch.name != name:
                ch.name = name
                changed.append(f"name={name}")
            if stream_name and ch.stream_key != stream_name:
                ch.stream_key = stream_name
                changed.append(f"stream_key={stream_name}")
            if category and ch.category != category:
                ch.category = category
                changed.append(f"category={category}")

            if changed:
                print(f"  UPDATE {ch.channel_key} -> {', '.join(changed)}")
                updated += 1
            else:
                print(f"  OK     {ch.channel_key} (sin cambios)")

        await db.commit()
        print(f"\n{updated} canales actualizados.")

    # Show final state
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(Channel).order_by(Channel.number))
        channels = list(result.scalars().all())
        print("\n=== Estado final de canales ===")
        for ch in channels:
            hls = flussonic.stream_hls_url(ch.stream_key) if flussonic.is_configured else "N/A"
            print(f"  {ch.number:2d}. {ch.name:<25s} stream={ch.stream_key:<30s}")
            print(f"      HLS: {hls}")


if __name__ == "__main__":
    asyncio.run(main())
