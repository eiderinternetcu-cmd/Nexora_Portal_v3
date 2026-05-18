# TODO_NEXT.md — Próximos Pasos
_Last updated: 2026-05-17_

---

## COMPLETADO — Fase 1 ✅

- Modelos SQLAlchemy 2.x (User, Subscriber, Plan, Subscription, Device, AuditLog, Session)
- Autenticación Argon2id + PyJWT con blacklist Redis
- Rate limiting sliding window + lockout por IP/usuario
- Migración 001: 6 tablas + triggers + índices
- Docker Compose: postgres:16 + redis:7

## COMPLETADO — Fase 2 ✅

## COMPLETADO — Fase 3a: StreamAuthService ✅
## COMPLETADO — Fase 3b: IPTV DB Sessions ✅
## COMPLETADO — Fase 3b.2: Subscription CRUD ✅

- `app/services/stream_auth_service.py` — authorize(), validate(), create_token(), revoke_token()
- `app/schemas/playback.py` — PlayRequest, PlaybackTokenOut, ValidateRequest/Response, TokenRequest
- `app/api/stb/playback.py` — POST /api/stb/auth/play, /validate, /token
- `app/redis_client.py` — key_playback()
- `app/config.py` — playback_token_expire_seconds (default 60s)
- `app/middleware/rate_limit.py` — /auth/play=20/min, /auth/token=30/min
- `.env` — PLAYBACK_TOKEN_EXPIRE_SECONDS=60

- `Session` importada en `app/models/__init__.py`
- Migración 002: tabla `sessions` + columnas fingerprint en `devices`
- `ConnectionService` — Redis ZSET para concurrencia IPTV (score=expire_unix, member=device_id)
- `SessionService` reescrito — Redis (admin) + PostgreSQL (subscriber IPTV)
- `device_service.py` heartbeat extiende ZSET + retorna `active_connections`
- Dominio `/api/admin/` con gestión de sesiones
- Dominio `/api/stb/` — heartbeat sin auth, register, connections
- Dominio `/api/subscriber/` — placeholder
- `scripts/dev_server.py` — SelectorEventLoop para Windows + Python 3.14
- Rate limits per-path: login=10, refresh=20, heartbeat=30, register=5
- MCP server (`mcp_server/server.py`) — 14 herramientas, registrado en claude CLI

---

## COMPLETADO — Fase 3c: Modern Client API ✅ (2026-05-17)

`/api/client/auth/login|refresh|logout` — JWT par (24h access / 90d refresh), lockout sub:*
`/api/client/profile` — perfil + suscripción, dispositivos, heartbeat autenticado
`/api/client/playback/authorize` — full auth via StreamAuthService (DB session + ZSET)
`/api/client/playback/{channel_id}?device_id=` — reissue token ligero

## COMPLETADO — Fase 3c.1: Catálogo real de canales ✅ (2026-05-17)

`app/models/channel.py` — Channel model (channel_key, stream_key, source_type, is_active…)
`app/schemas/channel.py` — ChannelPublic (cliente, sin stream_key), ChannelAdminOut (admin completo)
`app/services/channel_service.py` — list_active, get_by_key, get_active_by_key (READ ONLY)
`migrations/versions/003_channels.py` — tabla channels con índices
`scripts/seed_channels.py` — 21 canales seedeados (canal-1..canal-21, idempotente)
`/api/client/catalog/channels` — DB real: 21 canales activos
`/api/client/playback/authorize` — valida channel_key→stream_key antes de StreamAuthService
`/api/admin/channels` — GET lista y detalle (read-only, incluye stream_key para admin)
92 rutas totales

---

## FASE 3 — Bloques pendientes

### 3d Actualizar stream_keys reales

Cuando se conozcan los identificadores reales de Flussonic/Astra:

```sql
-- Ejemplo de actualización directa en DB (nunca via API):
UPDATE channels SET stream_key = 'real-stream-key', source_type = 'flussonic' WHERE channel_key = 'canal-1';
```

O desde el script seed actualizando el dict CHANNELS con los valores reales.

### 3e EPG real

Reemplazar `_MOCK_EPG` en `catalog.py` con:
- Tabla `epg_entries` (migración 004), o
- Integración externa (XMLTV, Gracenote, etc.)

### 3f Admin: Write para canales (cuando sea necesario)

Agregar POST/PATCH/DELETE en `/api/admin/channels` si se quiere gestión desde UI.
Por ahora solo lectura — las actualizaciones de stream_key se hacen directo en DB.

---

## COMANDOS PARA EL SIGUIENTE AGENTE

```bash
# Levantar entorno (si no corre)
docker-compose up -d

# Levantar servidor local (Windows)
python scripts/dev_server.py

# Health check
curl http://localhost:8000/health

# Admin login
curl -X POST http://localhost:8000/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"Admin1234!"}'

# --- Client API (Fase 3c + 3c.1) ---

# Subscriber login (auto-registra el dispositivo)
curl -X POST http://localhost:8000/api/client/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"<subscriber_username>","password":"<pass>","device_id":"my-android-tv-001","device_type":"android_tv","model":"Shield","brand":"Nvidia"}'
# Respuesta: { access_token, refresh_token, expires_in, subscriber_id }

# Perfil del suscriptor
curl http://localhost:8000/api/client/profile \
  -H "Authorization: Bearer <access_token>"

# Lista de canales mock
curl http://localhost:8000/api/client/catalog/channels \
  -H "Authorization: Bearer <access_token>"

# Autorizar reproducción (crea sesión IPTV en DB)
curl -X POST http://localhost:8000/api/client/playback/authorize \
  -H "Authorization: Bearer <access_token>" \
  -H "Content-Type: application/json" \
  -d '{"device_id":"my-android-tv-001","channel_id":"canal-1"}'
# Respuesta: { token (60s), expires_in, channel_id, subscriber_id }

# Reemitir token para dispositivo ya conectado
curl "http://localhost:8000/api/client/playback/canal-1?device_id=my-android-tv-001" \
  -H "Authorization: Bearer <access_token>"

# Heartbeat autenticado
curl -X POST http://localhost:8000/api/client/profile/devices/heartbeat \
  -H "Authorization: Bearer <access_token>" \
  -H "Content-Type: application/json" \
  -d '{"device_id":"my-android-tv-001"}'

# Refresh token
curl -X POST http://localhost:8000/api/client/auth/refresh \
  -H "Content-Type: application/json" \
  -d '{"refresh_token":"<refresh_token>"}'

# Logout
curl -X POST http://localhost:8000/api/client/auth/logout \
  -H "Authorization: Bearer <access_token>" \
  -H "Content-Type: application/json" \
  -d '{"refresh_token":"<refresh_token>"}'

# Admin: ver catálogo completo (con stream_key)
curl http://localhost:8000/api/admin/channels \
  -H "Authorization: Bearer <admin_access_token>"

# Migrar + seed (si DB es nueva)
# .venv\Scripts\python.exe -m alembic upgrade head
# .venv\Scripts\python.exe scripts/seed_channels.py
```

---

## NOTAS IMPORTANTES

- No usar PHP para módulos nuevos
- No empezar UI todavía (la UI está en `e:/WEBSITE/nexora_app` — proyecto separado)
- No usar MySQL en módulos nuevos
- No usar python-jose (requiere Rust), usar PyJWT[crypto]
- No usar asyncpg (compilación Rust), usar psycopg[binary]
- Primero clonar entorno, nunca migrar directo en producción
- El portal legacy PHP en `STB_PORTAL_URL` solo es referencia temporal
- `sessions` tabla existe con flujo completo (Fase 3b completada)
- No usar MAG/Stalker — no hay STBs MAG físicos en el proyecto
- No usar protocolo Stalker (Fase 3c reemplaza 3d original con Client API moderna)
