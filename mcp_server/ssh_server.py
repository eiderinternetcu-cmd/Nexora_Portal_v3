"""
Nexora SSH Remote Server Inspector — MCP Server
Audita el servidor remoto de forma segura (solo lectura).
Nunca instala ni modifica nada en el servidor remoto.
Credenciales cargadas solo desde variables de entorno.
"""
import os
from pathlib import Path

import paramiko
import yaml
from mcp.server.fastmcp import FastMCP

# ── Config ────────────────────────────────────────────────────────────────────

_CONFIG_PATH = Path(__file__).parent / "ssh_config.yaml"

with open(_CONFIG_PATH, encoding="utf-8") as _f:
    _cfg = yaml.safe_load(_f)

# ── SSH connection params (env vars override config) ──────────────────────────

def _ssh_params() -> dict:
    return {
        "host": os.environ.get("SSH_HOST", _cfg["ssh"]["host"]),
        "port": int(os.environ.get("SSH_PORT", str(_cfg["ssh"]["port"]))),
        "user": os.environ.get("SSH_USER", _cfg["ssh"]["user"]),
        "password": os.environ.get("SSH_PASSWORD", ""),
        "key_path": os.environ.get("SSH_KEY_PATH", _cfg["ssh"].get("key_path", "")),
        "connect_timeout": int(_cfg["ssh"]["connect_timeout"]),
    }


def _connect() -> paramiko.SSHClient:
    p = _ssh_params()
    client = paramiko.SSHClient()
    # AutoAddPolicy is acceptable for a controlled dev audit tool targeting a known host.
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    kwargs: dict = {
        "hostname": p["host"],
        "port": p["port"],
        "username": p["user"],
        "timeout": p["connect_timeout"],
        "look_for_keys": False,
        "allow_agent": False,
    }

    if p["key_path"] and Path(p["key_path"]).expanduser().exists():
        kwargs["key_filename"] = str(Path(p["key_path"]).expanduser())
        kwargs["look_for_keys"] = False
    elif p["password"]:
        kwargs["password"] = p["password"]
    else:
        # Fallback: try default ~/.ssh keys
        kwargs["look_for_keys"] = True

    client.connect(**kwargs)
    return client


def _exec(client: paramiko.SSHClient, command: str, timeout: int = 15) -> str:
    _, stdout, stderr = client.exec_command(command, timeout=timeout)
    out = stdout.read().decode("utf-8", errors="replace").strip()
    err = stderr.read().decode("utf-8", errors="replace").strip()
    parts = [p for p in (out, f"[stderr]\n{err}" if err else "") if p]
    return "\n".join(parts) or "(sin salida)"


def _safe_exec(command: str, timeout: int = 15) -> str:
    """Abre conexión SSH, ejecuta un comando de solo lectura, cierra conexión."""
    p = _ssh_params()
    try:
        client = _connect()
        result = _exec(client, command, timeout)
        client.close()
        return result
    except paramiko.AuthenticationException:
        return (
            f"ERROR: Autenticación SSH fallida.\n"
            f"Host: {p['host']}:{p['port']}  User: {p['user']}\n"
            "Verificar que SSH_PASSWORD o SSH_KEY_PATH estén correctamente configurados."
        )
    except paramiko.SSHException as exc:
        return f"ERROR SSH: {exc}"
    except TimeoutError:
        return f"ERROR: Timeout ({p['connect_timeout']}s) al conectar a {p['host']}:{p['port']}"
    except Exception as exc:
        return f"ERROR: {type(exc).__name__}: {exc}"


# ── MCP Server ────────────────────────────────────────────────────────────────

mcp = FastMCP(
    "nexora-ssh",
    instructions=(
        "SSH remote server auditor for Nexora deployment planning. "
        "Runs read-only diagnostic commands on the remote server via SSH. "
        "Never installs, modifies, or deletes anything. "
        "Use these tools to generate a compatibility report before deployment."
    ),
)

# ── Tools ─────────────────────────────────────────────────────────────────────


@mcp.tool()
def ping_ssh() -> str:
    """
    Verifica la conectividad SSH al servidor remoto.
    Ejecutar primero para confirmar credenciales antes de auditar.
    """
    p = _ssh_params()
    try:
        client = _connect()
        out = _exec(client, "echo SSH_OK && whoami && hostname && uname -s", timeout=10)
        client.close()
        return f"SSH: CONECTADO\nHost: {p['host']}:{p['port']}  User: {p['user']}\n{out}"
    except paramiko.AuthenticationException:
        return (
            f"SSH: ERROR DE AUTENTICACIÓN\n"
            f"Host: {p['host']}:{p['port']}  User: {p['user']}\n"
            "Verificar SSH_PASSWORD o SSH_KEY_PATH."
        )
    except Exception as exc:
        return f"SSH: FALLO\nHost: {p['host']}:{p['port']}\n{type(exc).__name__}: {exc}"


@mcp.tool()
def get_system_info() -> str:
    """
    Lee información básica del sistema remoto:
    OS, kernel, hostname, uptime, arquitectura, CPU, disponibilidad de Python y Docker.
    """
    cmd = " && ".join([
        "echo '=== HOSTNAME ==='",
        "hostname",
        "echo '=== OS RELEASE ==='",
        "cat /etc/os-release 2>/dev/null || cat /etc/issue 2>/dev/null || echo 'Desconocido'",
        "echo '=== KERNEL ==='",
        "uname -r",
        "echo '=== ARQUITECTURA ==='",
        "uname -m",
        "echo '=== UPTIME ==='",
        "uptime",
        "echo '=== CPU ==='",
        "grep -m4 'model name\\|cpu cores\\|siblings' /proc/cpuinfo 2>/dev/null | sort -u",
        "echo '=== PYTHON ==='",
        "python3 --version 2>&1 || python --version 2>&1 || echo 'Python no encontrado'",
        "echo '=== DOCKER ==='",
        "docker --version 2>&1 || echo 'Docker no instalado'",
        "echo '=== DOCKER COMPOSE ==='",
        "(docker compose version 2>&1 || docker-compose --version 2>&1) || echo 'Docker Compose no instalado'",
        "echo '=== SYSTEMD ==='",
        "systemctl --version 2>/dev/null | head -1 || echo 'systemd no disponible'",
        "echo '=== INIT SYSTEM ==='",
        "cat /proc/1/comm 2>/dev/null || ls -la /sbin/init 2>/dev/null | head -1",
    ])
    return _safe_exec(cmd, timeout=25)


@mcp.tool()
def get_disk_usage() -> str:
    """
    Muestra el uso de disco del servidor remoto.
    Incluye todos los filesystems montados con tamaño, usado, disponible y punto de montaje.
    """
    cmd = (
        "df -h --output=source,size,used,avail,pcent,target 2>/dev/null || df -h"
    )
    return _safe_exec(cmd, timeout=10)


@mcp.tool()
def get_memory_usage() -> str:
    """
    Muestra el uso de RAM y swap del servidor remoto.
    Incluye salida de 'free -h' y resumen de /proc/meminfo.
    """
    cmd = " && ".join([
        "echo '=== MEMORY (free -h) ==='",
        "free -h 2>/dev/null || echo 'free no disponible'",
        "echo '=== /proc/meminfo (resumen) ==='",
        "awk '/MemTotal|MemAvailable|MemFree|SwapTotal|SwapFree/{print}' /proc/meminfo 2>/dev/null",
    ])
    return _safe_exec(cmd, timeout=10)


@mcp.tool()
def get_running_services() -> str:
    """
    Lista los servicios activos en el servidor remoto.
    Usa systemctl si está disponible; fallback a ps aux para sistemas sin systemd.
    """
    cmd = (
        "systemctl list-units --type=service --state=running "
        "--no-pager --no-legend --plain 2>/dev/null | head -60 "
        "|| ps aux --sort=-%cpu 2>/dev/null | head -35 "
        "|| ps aux | head -35"
    )
    return _safe_exec(cmd, timeout=15)


@mcp.tool()
def get_open_ports() -> str:
    """
    Lista puertos TCP/UDP en escucha en el servidor remoto.
    Usa ss (preferido) o netstat como fallback. Sin proceso si no hay root.
    """
    cmd = (
        "echo '=== TCP LISTENING (ss) ===' && "
        "ss -tlnp 2>/dev/null || "
        "(echo '=== TCP LISTENING (netstat) ===' && netstat -tlnp 2>/dev/null) || "
        "echo 'Ni ss ni netstat disponibles'"
    )
    return _safe_exec(cmd, timeout=12)


@mcp.tool()
def get_failed_services() -> str:
    """
    Lista servicios en estado fallido y últimos errores del journal.
    Indica problemas de sistema previos a la instalación.
    """
    cmd = " && ".join([
        "echo '=== SERVICIOS FALLIDOS (systemctl) ==='",
        "systemctl list-units --type=service --state=failed --no-pager --no-legend --plain 2>/dev/null || echo 'systemctl no disponible'",
        "echo '=== ERRORES RECIENTES (journalctl) ==='",
        "journalctl -p err -n 20 --no-pager 2>/dev/null || echo 'journalctl no disponible'",
        "echo '=== DMESG ERRORES RECIENTES ==='",
        "dmesg --level=err,crit,alert,emerg 2>/dev/null | tail -20 || dmesg 2>/dev/null | grep -iE 'error|fail|crit' | tail -20 || echo 'dmesg no disponible'",
    ])
    return _safe_exec(cmd, timeout=20)


# ── Entrypoint ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    mcp.run()
