"""
Nexora Dev MCP Server
Permite a agentes IA (Claude Code, Codex) inspeccionar el estado del proyecto
local, verificar servicios y ejecutar operaciones seguras sin tocar producción.
"""
import json
import os
import platform
import re
import shutil
import socket
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import httpx
import yaml
from mcp.server.fastmcp import FastMCP

# ── Config ────────────────────────────────────────────────────────────────────

_CONFIG_PATH = Path(__file__).parent / "config.yaml"

with open(_CONFIG_PATH, encoding="utf-8") as _f:
    _cfg = yaml.safe_load(_f)

PROJECT_ROOT = Path(_cfg["project"]["root"])

_IGNORE = {".venv", "__pycache__", ".git", ".mypy_cache", "node_modules", ".pytest_cache"}
_ALLOWED_SERVICES: set[str] = set(_cfg["docker"]["allowed_services"])

# ── Credentials (loaded from .env, never exposed in output) ───────────────────

def _load_env_file() -> dict[str, str]:
    env: dict[str, str] = {}
    env_path = PROJECT_ROOT / ".env"
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                env[k.strip()] = v.strip()
    return env

_env = _load_env_file()


def _pg_connstr() -> str:
    host = _env.get("POSTGRES_HOST", _cfg["postgres"]["host"])
    port = _env.get("POSTGRES_PORT", str(_cfg["postgres"]["port"]))
    db = _env.get("POSTGRES_DB", _cfg["postgres"]["database"])
    user = _env.get("POSTGRES_USER", _cfg["postgres"]["user"])
    password = _env.get("POSTGRES_PASSWORD", "")
    # In docker-compose the host is the service name; map to localhost for MCP
    if host in ("postgres", "db"):
        host = "localhost"
    return f"host={host} port={port} dbname={db} user={user} password={password} connect_timeout=5"


def _redis_url() -> str:
    host = _env.get("REDIS_HOST", _cfg["redis"]["host"])
    port = _env.get("REDIS_PORT", str(_cfg["redis"]["port"]))
    db = _env.get("REDIS_DB", str(_cfg["redis"]["db"]))
    password = _env.get("REDIS_PASSWORD", "")
    if host in ("redis",):
        host = "localhost"
    if password:
        return f"redis://:{password}@{host}:{port}/{db}"
    return f"redis://{host}:{port}/{db}"

# ── MCP Server ────────────────────────────────────────────────────────────────

mcp = FastMCP(
    "nexora-dev",
    instructions=(
        "Nexora API local dev inspector. "
        "Use these tools to read project state, inspect routes, and verify "
        "local services (PostgreSQL, Redis, FastAPI). "
        "All operations are read-only or safe dev-local writes. "
        "Never operates against production."
    ),
)

# ─────────────────────────────────────────────────────────────────────────────
# Document readers
# ─────────────────────────────────────────────────────────────────────────────

@mcp.tool()
def read_project_status() -> str:
    """Lee PROJECT_STATUS.md — implementación actual, archivos, endpoints y pendientes."""
    p = PROJECT_ROOT / "PROJECT_STATUS.md"
    return p.read_text(encoding="utf-8") if p.exists() else "PROJECT_STATUS.md not found."


@mcp.tool()
def read_architecture() -> str:
    """Lee ARCHITECTURE.md — stack, estructura de carpetas, flujos, Redis keys, Alembic."""
    p = PROJECT_ROOT / "ARCHITECTURE.md"
    return p.read_text(encoding="utf-8") if p.exists() else "ARCHITECTURE.md not found."


@mcp.tool()
def read_todo_next() -> str:
    """Lee TODO_NEXT.md — próximos pasos exactos, comandos y pendientes de Fase 2."""
    p = PROJECT_ROOT / "TODO_NEXT.md"
    return p.read_text(encoding="utf-8") if p.exists() else "TODO_NEXT.md not found."


@mcp.tool()
def read_decisions() -> str:
    """Lee DECISIONS.md — decisiones técnicas, reglas del proyecto y restricciones."""
    p = PROJECT_ROOT / "DECISIONS.md"
    return p.read_text(encoding="utf-8") if p.exists() else "DECISIONS.md not found."

# ─────────────────────────────────────────────────────────────────────────────
# File system
# ─────────────────────────────────────────────────────────────────────────────

@mcp.tool()
def list_project_tree() -> str:
    """Lista el árbol de archivos del proyecto (excluye .venv, __pycache__, .git)."""
    lines: list[str] = [PROJECT_ROOT.name + "/"]

    def _walk(path: Path, prefix: str = "") -> None:
        try:
            entries = sorted(path.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))
        except PermissionError:
            return
        entries = [e for e in entries if e.name not in _IGNORE and not e.name.endswith(".pyc")]
        for i, entry in enumerate(entries):
            last = i == len(entries) - 1
            connector = "└── " if last else "├── "
            lines.append(f"{prefix}{connector}{entry.name}")
            if entry.is_dir():
                _walk(entry, prefix + ("    " if last else "│   "))

    _walk(PROJECT_ROOT)
    return "\n".join(lines)


@mcp.tool()
def list_modified_files(hours: int = 24) -> str:
    """
    Lista archivos del proyecto modificados en las últimas N horas.
    hours: 1–168 (default: 24)
    """
    hours = max(1, min(hours, 168))
    cutoff = time.time() - hours * 3600
    results: list[tuple[float, str, str]] = []

    for root, dirs, files in os.walk(PROJECT_ROOT):
        dirs[:] = [d for d in dirs if d not in _IGNORE]
        for fname in files:
            if fname.endswith(".pyc"):
                continue
            fpath = Path(root) / fname
            try:
                mtime = fpath.stat().st_mtime
                if mtime >= cutoff:
                    rel = str(fpath.relative_to(PROJECT_ROOT))
                    ts = datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M")
                    results.append((mtime, ts, rel))
            except OSError:
                continue

    if not results:
        return f"No files modified in the last {hours}h."

    results.sort(reverse=True)
    lines = [f"Modified files — last {hours}h ({len(results)} total):\n"]
    for _, ts, rel in results:
        lines.append(f"  {ts}  {rel}")
    return "\n".join(lines)

# ─────────────────────────────────────────────────────────────────────────────
# FastAPI inspection
# ─────────────────────────────────────────────────────────────────────────────

@mcp.tool()
def inspect_fastapi_routes() -> str:
    """
    Parsea los archivos de rutas FastAPI y lista todos los endpoints definidos
    con método HTTP, prefijo y path completo.
    """
    api_dir = PROJECT_ROOT / "app" / "api"
    if not api_dir.exists():
        return "app/api/ not found."

    output: list[str] = ["FastAPI Routes:\n"]

    for py_file in sorted(api_dir.rglob("*.py")):
        if py_file.name == "__init__.py":
            continue

        content = py_file.read_text(encoding="utf-8")
        rel = str(py_file.relative_to(PROJECT_ROOT))

        # Extract router prefix
        prefix = ""
        for line in content.splitlines():
            if "APIRouter" in line and "prefix=" in line:
                m = re.search(r'prefix=["\']([^"\']+)["\']', line)
                if m:
                    prefix = m.group(1)
                    break

        # Extract @router.<method>("path")
        found: list[str] = []
        for m in re.finditer(
            r'@router\.(get|post|put|patch|delete|head|options)\s*\(\s*["\']([^"\']*)["\']',
            content,
        ):
            method = m.group(1).upper()
            path = m.group(2)
            full = f"{prefix}{path}"
            found.append(f"  {method:<7} {full}")

        if found:
            output.append(f"[{rel}]")
            output.extend(found)
            output.append("")

    return "\n".join(output) if len(output) > 1 else "No routes found."

# ─────────────────────────────────────────────────────────────────────────────
# Service health checks
# ─────────────────────────────────────────────────────────────────────────────

@mcp.tool()
async def check_fastapi_health() -> str:
    """Llama a GET /health de la API local y devuelve el resultado."""
    url = _cfg["api"]["base_url"] + _cfg["api"]["health_endpoint"]
    timeout = float(_cfg["api"]["timeout_seconds"])
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(url)
        return f"HTTP {resp.status_code}\n{json.dumps(resp.json(), indent=2)}"
    except httpx.ConnectError:
        return (
            f"UNREACHABLE — {url}\n"
            "Verificar: docker-compose ps (¿está corriendo el servicio 'api'?)"
        )
    except Exception as exc:
        return f"Error: {type(exc).__name__}: {exc}"


@mcp.tool()
def check_postgres_connection() -> str:
    """Verifica la conexión a PostgreSQL local. No expone credenciales en la respuesta."""
    try:
        import psycopg  # type: ignore
    except ImportError:
        return "ERROR: psycopg no instalado. Ejecutar: pip install psycopg[binary]"
    try:
        with psycopg.connect(_pg_connstr()) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT version(), current_database(), NOW()")
                row = cur.fetchone()
        version = row[0].split(",")[0] if row else "unknown"
        db = row[1] if row else "unknown"
        ts = row[2].isoformat() if row else "unknown"
        return f"PostgreSQL: OK\nVersion : {version}\nDatabase: {db}\nTime    : {ts}"
    except Exception as exc:
        return f"PostgreSQL: FAILED\n{type(exc).__name__}: {str(exc)[:300]}"


@mcp.tool()
def check_redis_connection() -> str:
    """Verifica la conexión a Redis local. No expone credenciales en la respuesta."""
    try:
        import redis  # type: ignore
    except ImportError:
        return "ERROR: redis no instalado. Ejecutar: pip install redis"
    try:
        r = redis.from_url(_redis_url(), socket_connect_timeout=5, decode_responses=True)
        pong = r.ping()
        info = r.info("server")
        keys = r.dbsize()
        r.close()
        return (
            f"Redis: OK\n"
            f"Version : {info.get('redis_version', '?')}\n"
            f"Memory  : {info.get('used_memory_human', '?')}\n"
            f"Keys    : {keys}\n"
            f"Ping    : {'PONG' if pong else 'no response'}"
        )
    except Exception as exc:
        return f"Redis: FAILED\n{type(exc).__name__}: {str(exc)[:300]}"

# ─────────────────────────────────────────────────────────────────────────────
# Alembic
# ─────────────────────────────────────────────────────────────────────────────

def _run(cmd: list[str], timeout: int = 30) -> str:
    """Ejecuta un comando en el directorio del proyecto usando el Python del entorno actual."""
    try:
        result = subprocess.run(
            cmd,
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        parts: list[str] = []
        if result.stdout.strip():
            parts.append(result.stdout.strip())
        if result.stderr.strip():
            parts.append(f"[stderr]\n{result.stderr.strip()}")
        if not parts:
            parts.append(f"(exit code {result.returncode})")
        return "\n".join(parts)
    except subprocess.TimeoutExpired:
        return f"TIMEOUT after {timeout}s"
    except FileNotFoundError as exc:
        return f"Command not found: {exc}"
    except Exception as exc:
        return f"Error: {type(exc).__name__}: {exc}"


@mcp.tool()
def run_alembic_current() -> str:
    """Ejecuta 'alembic current' para ver la revisión activa en la base de datos local."""
    return _run([sys.executable, "-m", "alembic", "current"])


@mcp.tool()
def run_alembic_upgrade_head() -> str:
    """
    Ejecuta 'alembic upgrade head' en el entorno LOCAL de desarrollo.
    SOLO aplica migraciones a la base de datos local definida en .env.
    NO opera contra producción.
    """
    if not _cfg["policy"]["allow_alembic_upgrade"]:
        return "ERROR: run_alembic_upgrade_head está deshabilitado en config.yaml (policy.allow_alembic_upgrade: false)"
    return _run([sys.executable, "-m", "alembic", "upgrade", "head"], timeout=60)

# ─────────────────────────────────────────────────────────────────────────────
# Tests
# ─────────────────────────────────────────────────────────────────────────────

@mcp.tool()
def run_pytest(path: str = "tests/", verbose: bool = False) -> str:
    """
    Ejecuta pytest en el directorio indicado (default: tests/).
    path debe ser relativo al proyecto. No acepta flags arbitrarios.
    verbose: incluye -v en la salida.
    """
    if not _cfg["policy"]["allow_pytest"]:
        return "ERROR: run_pytest está deshabilitado en config.yaml (policy.allow_pytest: false)"

    safe = Path(path).as_posix().lstrip("/")
    if ".." in safe:
        return "ERROR: path no puede contener '..'"
    if not (PROJECT_ROOT / safe).exists():
        return f"Path not found: {safe}"

    cmd = [sys.executable, "-m", "pytest", safe, "--tb=short", "-q"]
    if verbose:
        cmd.append("-v")
    return _run(cmd, timeout=120)

# ─────────────────────────────────────────────────────────────────────────────
# Docker logs
# ─────────────────────────────────────────────────────────────────────────────

@mcp.tool()
def read_recent_logs(service: str = "api", lines: int = 50) -> str:
    """
    Lee los logs recientes de un servicio Docker Compose.
    service: api | postgres | redis  (default: api)
    lines: 10–200  (default: 50)
    """
    if service not in _ALLOWED_SERVICES:
        return f"Servicio no permitido. Opciones: {', '.join(sorted(_ALLOWED_SERVICES))}"
    lines = max(10, min(lines, _cfg["policy"]["max_log_lines"]))
    return _run(
        ["docker", "compose", "logs", service, f"--tail={lines}", "--no-color"],
        timeout=15,
    )

# ─────────────────────────────────────────────────────────────────────────────
# Web Player (Vite dev server)
# ─────────────────────────────────────────────────────────────────────────────

_WEB_PLAYER_DIR = PROJECT_ROOT / "web_player"
_WEB_PLAYER_PORT: int = _cfg.get("web_player", {}).get("port", 5173)
_WEB_PLAYER_PID_FILE = Path(__file__).parent / ".web_player.pid"


def _find_npm() -> str | None:
    npm = shutil.which("npm")
    if npm:
        return npm
    if platform.system() == "Windows":
        for candidate in [
            Path("C:/Program Files/nodejs/npm.cmd"),
            Path(os.environ.get("APPDATA", "X")) / "../Local/Programs/nodejs/npm.cmd",
        ]:
            if candidate.exists():
                return str(candidate)
    return None


def _port_listening(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1):
            return True
    except OSError:
        return False


def _pid_on_port(port: int) -> int | None:
    try:
        if platform.system() == "Windows":
            result = subprocess.run(
                ["netstat", "-ano"], capture_output=True, text=True, timeout=10
            )
            for line in result.stdout.splitlines():
                if f":{port}" in line and "LISTENING" in line:
                    parts = line.split()
                    if parts:
                        try:
                            return int(parts[-1])
                        except ValueError:
                            pass
        else:
            result = subprocess.run(
                ["lsof", "-ti", f":{port}"], capture_output=True, text=True, timeout=10
            )
            pid_str = result.stdout.strip()
            if pid_str:
                return int(pid_str.split()[0])
    except Exception:
        pass
    return None


@mcp.tool()
def check_web_player() -> str:
    """Verifica si el Vite dev server está corriendo en localhost:5173."""
    running = _port_listening(_WEB_PLAYER_PORT)
    pid = _pid_on_port(_WEB_PLAYER_PORT) if running else None
    lines = [f"Web player: {'RUNNING' if running else 'STOPPED'}"]
    if running:
        lines.append(f"URL: http://localhost:{_WEB_PLAYER_PORT}")
    if pid:
        lines.append(f"PID: {pid}")
    if not running:
        lines.append("Usa start_web_player para iniciarlo.")
    return "\n".join(lines)


@mcp.tool()
def start_web_player() -> str:
    """
    Inicia el Vite dev server (web_player/) como proceso en segundo plano.
    URL: http://localhost:5173
    Usa stop_web_player para detenerlo.
    """
    if not _cfg["policy"].get("allow_web_player", True):
        return "ERROR: start_web_player deshabilitado en config.yaml (policy.allow_web_player: false)."

    if _port_listening(_WEB_PLAYER_PORT):
        pid = _pid_on_port(_WEB_PLAYER_PORT)
        return (
            f"Web player ya está corriendo en http://localhost:{_WEB_PLAYER_PORT}"
            + (f" (PID {pid})" if pid else "")
        )

    if not _WEB_PLAYER_DIR.exists():
        return f"ERROR: {_WEB_PLAYER_DIR} no existe."

    npm = _find_npm()
    if not npm:
        return (
            "ERROR: npm no encontrado en PATH.\n"
            "Instala Node.js y asegúrate de que npm esté disponible en el PATH del sistema."
        )

    try:
        kwargs: dict = {
            "cwd": str(_WEB_PLAYER_DIR),
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL,
            "stdin": subprocess.DEVNULL,
        }
        if platform.system() == "Windows":
            kwargs["creationflags"] = (
                subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
            )
        else:
            kwargs["start_new_session"] = True

        proc = subprocess.Popen([npm, "run", "dev"], **kwargs)
        _WEB_PLAYER_PID_FILE.write_text(str(proc.pid))

        # Poll up to 15s for port to come up
        for _ in range(15):
            time.sleep(1)
            if _port_listening(_WEB_PLAYER_PORT):
                return (
                    f"Web player iniciado (PID {proc.pid})\n"
                    f"URL: http://localhost:{_WEB_PLAYER_PORT}"
                )

        alive = proc.poll() is None
        return (
            f"Web player lanzado (PID {proc.pid}, {'proceso activo' if alive else 'proceso terminó'})\n"
            f"Puerto {_WEB_PLAYER_PORT} aún no responde. Espera unos segundos y usa check_web_player."
        )
    except Exception as exc:
        return f"Error al iniciar web player: {type(exc).__name__}: {exc}"


@mcp.tool()
def stop_web_player() -> str:
    """Detiene el Vite dev server que corre en localhost:5173."""
    pid = _pid_on_port(_WEB_PLAYER_PORT)

    if not pid and _WEB_PLAYER_PID_FILE.exists():
        try:
            pid = int(_WEB_PLAYER_PID_FILE.read_text().strip())
        except Exception:
            pass

    if not pid:
        _WEB_PLAYER_PID_FILE.unlink(missing_ok=True)
        return "Web player no está corriendo."

    try:
        if platform.system() == "Windows":
            result = subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(pid)],
                capture_output=True, text=True, timeout=10,
            )
            msg = (result.stdout.strip() or result.stderr.strip() or "OK").splitlines()[0]
        else:
            import signal
            os.kill(pid, signal.SIGTERM)
            msg = f"SIGTERM -> PID {pid}"

        _WEB_PLAYER_PID_FILE.unlink(missing_ok=True)
        return f"Web player detenido (PID {pid}).\n{msg}"
    except Exception as exc:
        return f"Error al detener PID {pid}: {type(exc).__name__}: {exc}"

# ─────────────────────────────────────────────────────────────────────────────
# Entrypoint
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    mcp.run()
