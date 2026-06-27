"""Audit channel playback targets that could BYPASS the Nginx auth_request gate.

READ-ONLY. Lists channels whose `source_url` / `hls_path` / `flussonic_node`
point directly at an origin (raw IP, Flussonic/Astra port, external host, non-HTTPS,
or a path that is not same-origin `/stream/<node>/...`). Those channels would be
served WITHOUT passing through the token gate.

It NEVER writes to the DB, NEVER touches Flussonic, and NEVER prints secrets or
tokens (userinfo and query strings are stripped from any URL it shows).

Usage (read-only):
  Production:
    DATABASE_URL=postgresql://USER:***@HOST:5432/nexora \
    ALLOWED_STREAM_ORIGINS=https://nexoraplay.net \
    python scripts/audit_channel_source_urls.py

  Staging:
    DATABASE_URL=postgresql://USER:***@HOST:5432/nexora_staging \
    ALLOWED_STREAM_ORIGINS=https://staging.nexoraplay.net \
    python scripts/audit_channel_source_urls.py

  Inspect config without connecting:
    python scripts/audit_channel_source_urls.py --dry-run

Env:
  DATABASE_URL / TEST_DATABASE_URL  DB to read (else falls back to app.config).
  ALLOWED_STREAM_ORIGINS  CSV of allowed same-origin bases (e.g.
                          https://nexoraplay.net,https://staging.nexoraplay.net).
  ALLOWED_STREAM_PREFIXES CSV of allowed path prefixes (default: /stream/).
  ALLOWED_STREAM_NODES    CSV of allowed node ids (default: ec-main,co-main).

Exit codes: 0 = all OK, 2 = at least one risky channel, 1 = error.
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

DEFAULT_PREFIXES = "/stream/"
DEFAULT_NODES = "ec-main,co-main"


# ── pure helpers (unit-testable, no DB) ──────────────────────────────────────

def _csv(value: str | None) -> list[str]:
    return [v.strip() for v in (value or "").split(",") if v.strip()]


def _is_ip(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return False


def sanitize_url(raw: str) -> str:
    """Display form with NO secrets: drops userinfo, query and fragment."""
    if not raw:
        return "(vacío)"
    u = urlsplit(raw)
    if not u.scheme and not u.netloc:
        return u.path or "(vacío)"
    host = u.hostname or ""
    port = f":{u.port}" if u.port else ""
    return f"{u.scheme}://{host}{port}{u.path}"


def _norm_origin(scheme: str, host: str, port: int | None) -> str:
    """scheme://host[:port], dropping default ports (443/80)."""
    if port and not ((scheme == "https" and port == 443) or (scheme == "http" and port == 80)):
        return f"{scheme}://{host}:{port}"
    return f"{scheme}://{host}"


def classify(ch: dict, allowed_origins: list[str], allowed_prefixes: list[str],
             allowed_nodes: list[str]) -> tuple[str, list[str]]:
    """Return ("OK"|"RISK", reasons). `ch` has source_url/hls_path/flussonic_node."""
    src = (ch.get("source_url") or "").strip()
    hls = (ch.get("hls_path") or "").strip()
    node = (ch.get("flussonic_node") or "").strip()
    ok: list[str] = []
    risk: list[str] = []

    # node must be a known/allowed node
    if allowed_nodes and node and node not in allowed_nodes:
        risk.append(f"node no permitido: {node!r}")

    # hls_path must be relative (never an absolute URL)
    if "://" in hls or hls.lower().startswith("http"):
        risk.append(f"hls_path absoluto: {sanitize_url(hls)}")

    allowed_full = {
        _norm_origin(o.scheme, o.hostname or "", o.port)
        for o in (urlsplit(x) for x in allowed_origins)
    }

    if not src:
        ok.append(f"sin source_url → resuelve por /stream/{node or '<node>'}/…")
    else:
        u = urlsplit(src)
        if not u.scheme and not u.netloc:  # relative path
            if any(u.path.startswith(p) for p in allowed_prefixes):
                ok.append(f"path relativo same-origin: {u.path}")
            else:
                risk.append(f"path relativo no permitido (no {'/'.join(allowed_prefixes)}): {u.path}")
        else:  # absolute URL
            host = u.hostname or ""
            ch_origin = _norm_origin(u.scheme, host, u.port)
            if u.scheme != "https":
                risk.append(f"no HTTPS ({u.scheme or 'sin esquema'}): {sanitize_url(src)}")
            if ch_origin in allowed_full:
                if any(u.path.startswith(p) for p in allowed_prefixes):
                    ok.append(f"same-origin permitido: {sanitize_url(src)}")
                else:
                    risk.append(f"host permitido pero path no /stream/<node>: {u.path or '/'}")
            elif _is_ip(host):
                risk.append(f"IP directa de origen: {host}")
            elif u.port and u.port not in (443, 80):
                risk.append(f"puerto directo de origen: {host}:{u.port}")
            else:
                risk.append(f"host externo no permitido: {host or '(?)'}")

    return ("RISK", risk) if risk else ("OK", ok)


# ── DB access (read-only) ────────────────────────────────────────────────────

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
    host = u.hostname or ""
    port = f":{u.port}" if u.port else ""
    db = u.path
    # never show the password
    creds = f"{u.username}:***@" if u.username else ""
    return f"{u.scheme}://{creds}{host}{port}{db}"


async def fetch_channels(url: str, include_inactive: bool) -> list[dict]:
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    eng = create_async_engine(url, future=True)
    where = "" if include_inactive else "WHERE is_active = true"
    sql = text(
        f"SELECT channel_key, number, name, source_url, hls_path, flussonic_node, is_active "
        f"FROM channels {where} ORDER BY number"
    )
    try:
        async with eng.connect() as conn:  # connect() = no implicit txn writes
            rows = (await conn.execute(sql)).mappings().all()
            return [dict(r) for r in rows]
    finally:
        await eng.dispose()


# ── CLI ──────────────────────────────────────────────────────────────────────

def _config() -> dict:
    return {
        "origins": _csv(os.environ.get("ALLOWED_STREAM_ORIGINS")),
        "prefixes": _csv(os.environ.get("ALLOWED_STREAM_PREFIXES") or DEFAULT_PREFIXES),
        "nodes": _csv(os.environ.get("ALLOWED_STREAM_NODES") or DEFAULT_NODES),
    }


def _print_config(cfg: dict, db: str | None) -> None:
    print("Config:")
    if db is not None:
        print(f"  DB (masked):           {db}")
    print(f"  ALLOWED_STREAM_ORIGINS:  {cfg['origins'] or '(ninguno — toda URL absoluta será RISK)'}")
    print(f"  ALLOWED_STREAM_PREFIXES: {cfg['prefixes']}")
    print(f"  ALLOWED_STREAM_NODES:    {cfg['nodes']}")


async def run(args) -> int:
    cfg = _config()
    if args.dry_run:
        _print_config(cfg, None)
        print("\n--dry-run: sin conexión a la DB. OK.")
        return 0

    url = resolve_db_url()
    _print_config(cfg, mask_db_url(url))
    try:
        channels = await fetch_channels(url, args.include_inactive)
    except Exception as e:  # noqa: BLE001 — surface a clean message, no stack/secrets
        print(f"\nERROR conectando/leyendo la DB: {type(e).__name__}: {e}", file=sys.stderr)
        return 1

    ok_list, risk_list = [], []
    for ch in channels:
        status, reasons = classify(ch, cfg["origins"], cfg["prefixes"], cfg["nodes"])
        (risk_list if status == "RISK" else ok_list).append((ch, reasons))

    print(f"\n{'═'*72}")
    print(f"  Canales auditados: {len(channels)}   OK: {len(ok_list)}   RISK: {len(risk_list)}")
    print(f"{'═'*72}")

    if ok_list:
        print(f"\n✅ OK ({len(ok_list)}):")
        for ch, reasons in ok_list:
            print(f"  #{ch['number']:<4} {ch['channel_key']:<18} {reasons[0] if reasons else ''}")

    if risk_list:
        print(f"\n⚠️  RISK ({len(risk_list)}) — podrían saltarse el gate:")
        for ch, reasons in risk_list:
            print(f"  #{ch['number']:<4} {ch['channel_key']:<18} {ch['name']}")
            for r in reasons:
                print(f"        ↳ {r}")

    print()
    if risk_list:
        print(f"RESULTADO: {len(risk_list)} canal(es) RIESGOSO(s). Revisar antes de habilitar el gate.")
        return 2
    print("RESULTADO: todos los canales resuelven same-origin / por nodo. OK.")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description="Auditor read-only de source_url same-origin de canales.")
    p.add_argument("--dry-run", action="store_true", help="Muestra la config y sale (sin conectar a la DB).")
    p.add_argument("--include-inactive", action="store_true", help="Incluye canales inactivos (por defecto solo activos).")
    args = p.parse_args()
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
