# Nexora API

FastAPI backend para IPTV — gestión de suscriptores, dispositivos, planes, autenticación y playback con Flussonic Media Server.

## Stack
- **FastAPI** 0.115+ / Python 3.12+
- **PostgreSQL** 16 (SQLAlchemy 2.x async + psycopg3)
- **Redis** 7 (sesiones, rate limit, concurrencia IPTV)
- **Argon2id** (passwords) + **PyJWT** (tokens)
- **Alembic** (migraciones async)
- **httpx** (cliente HTTP para Flussonic)
- **Flussonic Media Server** (streaming — externo, read-only)
- **hls.js** (reproducción HLS en web player)

## Inicio rápido

```bash
cp .env.example .env
# Configurar DATABASE_URL, REDIS_URL, SECRET_KEY, FLUSSONIC_* en .env

docker-compose up -d
python -m alembic upgrade head
python scripts/create_admin.py
python scripts/seed_channels.py

# Dev server (Windows)
python scripts/dev_server.py
```

API: http://localhost:8000  
Docs interactivos: http://localhost:8000/docs

## Web Player

```bash
cd web_player
npm install
npm run dev   # http://localhost:5173 (proxy /api/* -> :8000)
```

## Fase actual: 3d Flussonic Integration — COMPLETADA ✅

### Flujo de playback validado

```
login -> GET canales -> POST /playback/authorize -> hls.js reproduce URL Flussonic
```

`POST /api/client/playback/authorize` retorna `playback_url` directa de Flussonic:
```
http://181.78.246.211:8002/ECUADOR_TV/index.m3u8
```

Nexora **no hace proxy de video** — el cliente reproduce directo desde Flussonic.  
Credenciales Flussonic **nunca** salen del backend.

## Seguridad

- Credenciales Flussonic solo en `.env` (backend) — en `.gitignore`
- `FlussonicClient` es READ ONLY — operaciones write lanzan `RuntimeError`
- `stream_key` nunca se expone al cliente (solo `channel_key`)
- HLS URL sin usuario/password embebido
- Argon2id para passwords de suscriptores
- JWT con rotación de refresh token (90d → nuevo par en cada uso)
- Rate limiting + lockout por IP/usuario

## Documentación

| Archivo | Contenido |
|---------|-----------|
| PROJECT_STATUS.md | Estado actual, archivos, endpoints, mapeo de canales |
| ARCHITECTURE.md | Stack, flujos, Redis keys, modelo de seguridad Flussonic |
| TODO_NEXT.md | Próximos pasos (Fase 4: hls.js browser, signed URLs, multi-node) |
| SETUP.md | Variables .env, comandos, curl tests |
| DECISIONS.md | Decisiones técnicas y reglas del proyecto |
| web_player/README.md | Integración web player con Flussonic |

## Estructura de dominios

| Dominio | Propósito |
|---------|-----------|
| `/api/client/*` | App moderna (web, Android TV, iOS) |
| `/api/admin/*` | Panel de administración + inspección Flussonic |
| `/api/stb/*` | Autenticación STB + callback Flussonic backend-auth |
| `/api/v1/*` | Legacy compat (admin/reseller) |
