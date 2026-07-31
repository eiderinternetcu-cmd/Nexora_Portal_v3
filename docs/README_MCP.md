# README_MCP.md — Nexora Dev MCP Server

MCP server local para que agentes IA puedan inspeccionar y verificar el estado del proyecto
Nexora API sin riesgo de afectar producción.

---

## Instalación

### 1. Instalar dependencias del MCP server

```bash
# Activar el venv del proyecto
.\.venv\Scripts\Activate.ps1   # Windows PowerShell
# source .venv/bin/activate     # Linux/Mac

# Instalar paquetes MCP adicionales
pip install "mcp[cli]" pyyaml
# El resto (httpx, psycopg[binary], redis) ya están en requirements.txt
```

### 2. Verificar que el servidor arranca

```bash
# Desde el directorio nexora_api/
python mcp_server/server.py
```

Si arranca sin error, muestra algo como:
```
Starting Nexora Dev MCP server on stdio...
```

---

## Registrar en Claude Code (CLI)

```bash
# Desde el directorio nexora_api/
claude mcp add nexora-dev -- python mcp_server/server.py
```

Verificar que está registrado:
```bash
claude mcp list
```

---

## Registrar en Claude Desktop

Editar el archivo de configuración de Claude Desktop:

**Windows:** `%APPDATA%\Claude\claude_desktop_config.json`
**Mac:** `~/Library/Application Support/Claude/claude_desktop_config.json`

```json
{
  "mcpServers": {
    "nexora-dev": {
      "command": "E:\\WEBSITE\\nexora_api\\.venv\\Scripts\\python.exe",
      "args": ["E:\\WEBSITE\\nexora_api\\mcp_server\\server.py"],
      "env": {}
    }
  }
}
```

Reiniciar Claude Desktop después de editar.

---

## Herramientas disponibles

| Herramienta | Descripción |
|-------------|-------------|
| `read_project_status` | Estado actual, archivos, endpoints, pendientes |
| `read_architecture` | Stack, flujos, Redis keys, DB schema |
| `read_todo_next` | Próximos pasos exactos para Fase 2 |
| `read_decisions` | Decisiones técnicas y restricciones |
| `list_project_tree` | Árbol de archivos (excluye .venv, __pycache__) |
| `list_modified_files` | Archivos modificados en últimas N horas |
| `inspect_fastapi_routes` | Endpoints FastAPI parseados de los archivos |
| `check_fastapi_health` | GET /health → localhost:8000 |
| `check_postgres_connection` | Conexión + versión PostgreSQL local |
| `check_redis_connection` | Ping + info Redis local |
| `run_alembic_current` | `alembic current` — revisión activa en DB |
| `run_alembic_upgrade_head` | `alembic upgrade head` — solo dev local |
| `run_pytest` | `pytest tests/` con path controlado |
| `read_recent_logs` | `docker compose logs {api\|postgres\|redis}` |

---

## Ejemplo de uso con Claude Code

Una vez registrado, Claude Code puede usar las herramientas directamente:

```
# Claude Code automáticamente llama a read_project_status, read_todo_next, etc.
# cuando retoma el proyecto Nexora.
```

O explícitamente puedes pedirle:
> "Usa read_todo_next para ver qué sigue en el proyecto Nexora"
> "Usa check_fastapi_health y check_postgres_connection para verificar el stack"
> "Usa run_alembic_current para ver el estado de las migraciones"

---

## Configuración

Editar `mcp_server/config.yaml` para ajustar:
- Puertos de PostgreSQL y Redis
- URL base de la API
- Deshabilitar operaciones (`allow_alembic_upgrade`, `allow_pytest`)
- Límite de líneas de logs

---

## Política de seguridad

Ver `MCP_POLICY.md` para las reglas completas.

**Resumen:**
- Solo lee archivos del proyecto y se conecta a servicios locales
- No ejecuta comandos arbitrarios
- No expone credenciales en ningún output
- No opera contra producción

---

## Solución de problemas

**`ModuleNotFoundError: mcp`**
```bash
pip install "mcp[cli]"
```

**`ModuleNotFoundError: yaml`**
```bash
pip install pyyaml
```

**PostgreSQL: FAILED — connection refused**
```bash
docker-compose up -d postgres
docker-compose ps
```

**check_fastapi_health: UNREACHABLE**
```bash
docker-compose up -d api
# o en local:
uvicorn app.main:app --reload --port 8000
```

**alembic current: no .env found**
- Crear `.env` desde `.env.example` y configurar credenciales
