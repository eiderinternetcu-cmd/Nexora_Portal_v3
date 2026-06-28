"""Controlled backfill: rewrite channels that point to a direct Flussonic/Astra
origin (http://IP:8002/<stream>/index.m3u8) so playback stays SAME-ORIGIN and the
Nginx auth_request gate applies.

SAFE BY DEFAULT: dry-run. It only writes to the DB when called with --apply.
It NEVER contacts Flussonic, NEVER prints secrets/tokens, and NEVER touches a
channel whose origin host is not a KNOWN node (those are reported as RISK).

For each channel whose source_url is a known-origin URL it proposes:
  - flussonic_node  ← mapped from the origin IP (38.210.187.13→co-main, 181.78.246.211→ec-main)
  - stream_key      ← extracted from the path, only if missing or if it matches safely
  - hls_path        ← extracted from the path (default index.m3u8)
  - source_url      ← per CHANNEL_SOURCE_URL_MODE (default: relative same-origin)

Usage:
  Dry-run (default, read-only):
    DATABASE_URL='postgresql://USER:***@HOST:5432/nexora_staging' \
    python scripts/backfill_channel_source_urls_same_origin.py

  Apply (writes DB — staging first, never prod without explicit authorization):
    DATABASE_URL='...' python scripts/backfill_channel_source_urls_same_origin.py --apply

Env:
  DATABASE_URL / TEST_DATABASE_URL   DB to read/(optionally)write.
  CHANNEL_SOURCE_URL_MODE            relative (default) | node | absolute
  STREAM_PUBLIC_BASE_URL             https://<domain> (required for absolute)
  IP_NODE_MAP_EXTRA                  CSV "ip=node,ip=node" to extend the known map.

Exit codes: 0 = no risks (dry-run or apply OK), 2 = unfixable risks remain, 1 = error.
"""
from __future__ import annotations

import argparse
import asyncio
import ipaddress
import os
import sys
from pathlib import Path
from urllib.parse import urlsplit

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

sys.path.insert(0, str(Path(__file__).parent.parent))

# Known Flussonic/Astra origins → node id. Only these are auto-fixable.
# NOTE: a node id here must also exist in config (base_url) + Nginx (/stream/<node>/)
# before the rewritten same-origin URL can actually serve and be gated.
IP_NODE_MAP = {
    "38.210.187.13": "co-main",   # Colombia
    "181.78.246.211": "ec-main",  # Esmeraldas (Astra)
    "45.70.202.171": "ec-quito",  # Quito (Astra)
}


# ── pure helpers (unit-testable, no DB) ──────────────────────────────────────

def load_ip_node_map() -> dict[str, str]:
    m = dict(IP_NODE_MAP)
    for pair in (os.environ.get("IP_NODE_MAP_EXTRA") or "").split(","):
        pair = pair.strip()
        if "=" in pair:
            ip, node = pair.split("=", 1)
            m[ip.strip()] = node.strip()
    return m


def sanitize_url(raw: str) -> str:
    """Display form with NO secrets: drops userinfo, query and fragment."""
    if not raw:
        return "(NULL)"
    u = urlsplit(raw)
    if not u.scheme and not u.netloc:
        return u.path or "(NULL)"
    host = u.hostname or ""
    port = f":{u.port}" if u.port else ""
    return f"{u.scheme}://{host}{port}{u.path}"


def _extract_stream_and_path(path: str) -> tuple[str | None, str]:
    parts = [p for p in path.split("/") if p]
    if not parts:
        return None, "index.m3u8"
    stream_key = parts[0]
    last = parts[-1]
    hls_path = last if (last.endswith(".m3u8") or last.endswith(".mpd")) else "index.m3u8"
    return stream_key, hls_path


def _target_source_url(node: str, stream_key: str, hls_path: str, mode: str, public_base: str) -> str | None:
    if mode == "relative":
        return f"/stream/{node}/{stream_key}/{hls_path}"
    if mode == "absolute":
        return f"{public_base.rstrip('/')}/stream/{node}/{stream_key}/{hls_path}"
    return None  # node mode → NULL


def plan_channel(ch: dict, mode: str = "relative", public_base: str = "",
                 ip_node_map: dict[str, str] | None = None) -> dict:
    """Decide what to do with one channel. Returns:
       {status: OK|FIX|RISK, reasons: [...], proposed: {field: new_value}}.
    Pure: no DB, no side effects."""
    ip_node_map = ip_node_map or IP_NODE_MAP
    src = (ch.get("source_url") or "").strip()
    node = (ch.get("flussonic_node") or "").strip()
    cur_key = (ch.get("stream_key") or "").strip()
    reasons: list[str] = []
    proposed: dict = {}

    if not src:
        return {"status": "OK", "reasons": ["source_url ya es NULL (resuelve por nodo)"], "proposed": {}}

    u = urlsplit(src)
    # already same-origin (relative /stream/ or absolute https to allowed base)?
    if not u.scheme and not u.netloc and u.path.startswith("/stream/"):
        return {"status": "OK", "reasons": [f"ya same-origin relativo: {u.path}"], "proposed": {}}
    if u.scheme == "https" and u.path.startswith("/stream/"):
        return {"status": "OK", "reasons": [f"ya same-origin: {sanitize_url(src)}"], "proposed": {}}

    host = u.hostname or ""
    # is it a KNOWN origin we can map?
    target_node = ip_node_map.get(host)
    if target_node is None:
        # unknown host/IP → do NOT touch
        kind = "IP" if _is_ip(host) else "host"
        return {"status": "RISK",
                "reasons": [f"origen {kind} desconocido (no auto-corregible): {sanitize_url(src)}"],
                "proposed": {}}

    # known origin → propose a same-origin fix
    ext_key, ext_hls = _extract_stream_and_path(u.path)
    if node != target_node:
        proposed["flussonic_node"] = target_node
        reasons.append(f"node {node or '∅'} → {target_node} (por IP {host})")
    # stream_key: set only if missing, or if it matches safely (same value)
    if not cur_key and ext_key:
        proposed["stream_key"] = ext_key
        reasons.append(f"stream_key faltante → {ext_key}")
    elif ext_key and cur_key != ext_key:
        reasons.append(f"stream_key actual {cur_key!r} ≠ path {ext_key!r} → se conserva el actual (no se sobrescribe)")
    if (ch.get("hls_path") or "index.m3u8") != ext_hls:
        proposed["hls_path"] = ext_hls
        reasons.append(f"hls_path → {ext_hls}")
    # always remove the direct-origin source_url (→ NULL or same-origin per mode)
    final_key = proposed.get("stream_key", cur_key) or ext_key or ""
    final_node = proposed.get("flussonic_node", node) or target_node
    proposed["source_url"] = _target_source_url(final_node, final_key, ext_hls, mode, public_base)
    reasons.append(f"source_url → {proposed['source_url'] if proposed['source_url'] is not None else 'NULL'}")
    return {"status": "FIX", "reasons": reasons, "proposed": proposed}


def _is_ip(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return False


# ── DB access ─────────────────────────────────────────────────────────────────

def resolve_db_url() -> str:
    url = os.environ.get("DATABASE_URL") or os.environ.get("TEST_DATABASE_URL")
    if not url:
        from app.config import get_settings
        url = get_settings().database_url
    if url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+psycopg://", 1)
    elif url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql+psycopg://", 1)
    return url


def mask_db_url(url: str) -> str:
    u = urlsplit(url)
    creds = f"{u.username}:***@" if u.username else ""
    port = f":{u.port}" if u.port else ""
    return f"{u.scheme}://{creds}{u.hostname or ''}{port}{u.path}"


async def run(args) -> int:
    mode = (os.environ.get("CHANNEL_SOURCE_URL_MODE") or "relative").lower()
    public_base = (os.environ.get("STREAM_PUBLIC_BASE_URL") or "").rstrip("/")
    if mode == "absolute" and not public_base.startswith("https://"):
        print("ERROR: CHANNEL_SOURCE_URL_MODE=absolute requiere STREAM_PUBLIC_BASE_URL=https://<domain>", file=sys.stderr)
        return 1
    ip_map = load_ip_node_map()

    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine
    url = resolve_db_url()
    print(f"DB (masked): {mask_db_url(url)}")
    print(f"Modo source_url destino: {mode}   IPs conocidas: {', '.join(ip_map)}")
    print(f"{'APLICAR (escribe DB)' if args.apply else 'DRY-RUN (solo lectura)'}\n")

    eng = create_async_engine(url, future=True)
    try:
        async with eng.connect() as conn:
            rows = (await conn.execute(text(
                "SELECT id, channel_key, number, name, source_url, hls_path, stream_key, flussonic_node "
                "FROM channels ORDER BY number"
            ))).mappings().all()
            channels = [dict(r) for r in rows]

            ok, fixes, risks = [], [], []
            for ch in channels:
                plan = plan_channel(ch, mode, public_base, ip_map)
                {"OK": ok, "FIX": fixes, "RISK": risks}[plan["status"]].append((ch, plan))

            print(f"{'═'*72}")
            print(f"  Total: {len(channels)}   OK: {len(ok)}   candidatos (FIX): {len(fixes)}   RISK: {len(risks)}")
            print(f"{'═'*72}")

            if fixes:
                print(f"\n🔧 Cambios propuestos ({len(fixes)}):")
                for ch, plan in fixes:
                    print(f"  #{ch['number']:<4} {ch['channel_key']:<18} {ch['name']}")
                    for r in plan["reasons"]:
                        print(f"        ↳ {r}")

            if risks:
                print(f"\n⚠️  NO modificables ({len(risks)}) — requieren revisión manual:")
                for ch, plan in risks:
                    print(f"  #{ch['number']:<4} {ch['channel_key']:<18} {plan['reasons'][0]}")

            if ok:
                print(f"\n✅ Ya correctos ({len(ok)}).")

            if args.apply and fixes:
                print(f"\nAplicando {len(fixes)} cambio(s)…")
                for ch, plan in fixes:
                    sets, params = [], {"id": ch["id"]}
                    for field, val in plan["proposed"].items():
                        sets.append(f"{field} = :{field}")
                        params[field] = val
                    if sets:
                        await conn.execute(text(f"UPDATE channels SET {', '.join(sets)} WHERE id = :id"), params)
                await conn.commit()
                print("Cambios aplicados.")
            elif args.apply:
                print("\nNada que aplicar.")
            else:
                print("\n(DRY-RUN: no se modificó la DB. Usa --apply para escribir.)")
    except Exception as e:  # noqa: BLE001
        print(f"\nERROR DB: {type(e).__name__}: {e}", file=sys.stderr)
        return 1
    finally:
        await eng.dispose()

    if risks:
        print(f"\nRESULTADO: {len(risks)} canal(es) NO auto-corregible(s). Revisión manual requerida.")
        return 2
    print("\nRESULTADO: sin riesgos pendientes." + ("" if args.apply else " (dry-run)"))
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description="Backfill controlado de source_url a same-origin (default dry-run).")
    p.add_argument("--apply", action="store_true", help="Escribe los cambios en la DB (por defecto: dry-run).")
    return asyncio.run(run(p.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
