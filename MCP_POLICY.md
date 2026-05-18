# MCP_POLICY.md — Política del MCP Server Nexora Dev
_Last updated: 2026-05-17_

---

## Propósito

Este MCP server existe para que agentes IA (Claude Code, Codex) puedan retomar el proyecto
leyendo estado, inspeccionando servicios y ejecutando verificaciones **sin riesgo de afectar producción**.

---

## LO QUE PUEDE HACER

| Herramienta | Acción |
|-------------|--------|
| `read_project_status` | Leer PROJECT_STATUS.md |
| `read_architecture` | Leer ARCHITECTURE.md |
| `read_todo_next` | Leer TODO_NEXT.md |
| `read_decisions` | Leer DECISIONS.md |
| `list_project_tree` | Listar árbol de archivos |
| `list_modified_files` | Ver qué archivos cambiaron (por mtime) |
| `inspect_fastapi_routes` | Parsear rutas de los archivos Python |
| `check_fastapi_health` | HTTP GET /health a localhost:8000 |
| `check_postgres_connection` | Conectar a PostgreSQL local, consulta de versión |
| `check_redis_connection` | Conectar a Redis local, info + ping |
| `run_alembic_current` | `alembic current` — solo lectura del estado de migraciones |
| `run_alembic_upgrade_head` | `alembic upgrade head` — solo en local dev |
| `run_pytest` | `pytest tests/` con path relativo controlado |
| `read_recent_logs` | `docker compose logs {api|postgres|redis}` |

---

## LO QUE ESTÁ PROHIBIDO

- **No ejecutar comandos arbitrarios** — no hay herramienta `exec` ni `shell`
- **No borrar archivos** — no hay herramienta de delete
- **No modificar .env** — el archivo se lee en memoria, nunca se escribe
- **No exponer secretos** — las credenciales se usan internamente; nunca aparecen en el output de herramientas
- **No operar contra producción** — todas las conexiones apuntan a localhost con los puertos locales (5433, 6380, 8000)
- **No aceptar paths con `..`** — `run_pytest` valida el path antes de ejecutar
- **No aceptar servicios no autorizados** en `read_recent_logs` — whitelist: api, postgres, redis

---

## CONTROL DE OPERACIONES DESTRUCTIVAS

`run_alembic_upgrade_head` puede modificar el esquema de la base de datos local.
Se puede deshabilitar en `mcp_server/config.yaml`:

```yaml
policy:
  allow_alembic_upgrade: false
  allow_pytest: false
```

---

## CREDENCIALES

Las credenciales (POSTGRES_PASSWORD, REDIS_PASSWORD) se leen del archivo `.env` local
**en memoria al arrancar el servidor**. Nunca se retornan en el output de ninguna herramienta.
El servidor MCP **no tiene acceso a credenciales de producción**.

---

## ALCANCE

**Solo entorno local de desarrollo.**
El servidor MCP está diseñado para correr en la misma máquina donde corre el stack de Docker Compose.
No se conecta a servidores remotos. No hace peticiones a internet (excepto GET /health a localhost).
