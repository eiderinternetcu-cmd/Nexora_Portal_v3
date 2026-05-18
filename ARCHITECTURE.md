# ARCHITECTURE.md — Nexora API
_Last updated: 2026-05-18_

---

## Stack

| Capa | Tecnología | Versión |
|------|-----------|---------|
| Framework | FastAPI | >=0.115.5 |
| Runtime | Python | 3.12 (Docker), 3.14 (local dev) |
| ORM | SQLAlchemy async | >=2.0.36 |
| DB driver | psycopg3 (psycopg[binary]) | >=3.2.10 |
| Base de datos | PostgreSQL | 16-alpine |
| Cache/Sessions | Redis | 7-alpine |
| Migraciones | Alembic | >=1.14.0 |
| JWT | PyJWT[crypto] | >=2.10.0 |
| Password | passlib[argon2] + argon2-cffi | Argon2id |
| Validación | Pydantic v2 | >=2.10.0 |
| Servidor | uvicorn[standard] | >=0.32.0 |
| HTTP client | httpx | >=0.28.0 |
| Streaming media | Flussonic Media Server | 8002 (externo) |

---

## Estructura de Carpetas

```
nexora_api/
├── app/
│   ├── main.py              # FastAPI app, lifespan, middleware, routers
│   ├── config.py            # Settings pydantic-settings — incluye vars Flussonic
│   ├── database.py          # create_async_engine, async_sessionmaker, get_db
│   ├── redis_client.py      # get_redis, close_redis, key_* helpers
│   ├── models/              # SQLAlchemy ORM models
│   │   ├── user.py          # User (admin|reseller)
│   │   ├── subscriber.py    # Subscriber — end user IPTV
│   │   ├── plan.py          # Plan — duración, max_connections, max_devices
│   │   ├── subscription.py  # Subscription — subscriber ↔ plan + fechas
│   │   ├── device.py        # Device — fingerprint, MAC, android_id, blocked
│   │   ├── audit.py         # AuditLog — JSONB details
│   │   ├── session.py       # Session — JTI, device_id, expires_at, revoked_at
│   │   └── channel.py       # Channel — channel_key, stream_key, source_type, category
│   ├── schemas/             # Pydantic v2 request/response models
│   ├── core/
│   │   ├── security.py      # Argon2id, PyJWT, client tokens
│   │   ├── dependencies.py  # FastAPI Depends: get_current_user, get_current_subscriber
│   │   └── exceptions.py    # NexoraException, unauthorized, forbidden, locked
│   ├── integrations/
│   │   └── flussonic_client.py  # READ-ONLY Flussonic HTTP client
│   ├── services/            # Business logic
│   ├── api/
│   │   ├── v1/              # /api/v1/ — legacy admin compat
│   │   ├── admin/           # /api/admin/ — admin panel
│   │   │   ├── channels.py  # Canal catalog + stream-status Flussonic
│   │   │   └── flussonic.py # Flussonic inspection endpoints
│   │   ├── stb/             # /api/stb/ — STB auth (Flussonic callback)
│   │   └── client/          # /api/client/ — Modern Client API
│   │       └── playback.py  # Genera playback_url desde Flussonic
│   └── middleware/
│       └── rate_limit.py    # Redis sliding window
├── migrations/
│   └── versions/
│       ├── 001_initial_schema.py
│       ├── 002_sessions_device_fingerprint.py
│       └── 003_channels.py
├── scripts/
│   ├── dev_server.py            # SelectorEventLoop Windows + Python 3.14
│   ├── create_admin.py
│   ├── seed_channels.py
│   └── map_flussonic_channels.py  # Mapeo DB channels -> Flussonic stream names
├── web_player/              # Vite + React + hls.js
│   ├── vite.config.ts       # proxy /api/* -> http://localhost:8000
│   └── src/
├── docker-compose.yml
├── Dockerfile
└── alembic.ini
```

---

## PostgreSQL — Tablas

| Tabla | Propósito |
|-------|-----------|
| `users` | Admins y resellers del portal |
| `subscribers` | Clientes IPTV finales |
| `plans` | Planes (duración, límites) |
| `subscriptions` | Relación suscriptor ↔ plan con fechas |
| `devices` | Dispositivos registrados (MAC, android_id, fingerprint) |
| `sessions` | Sesiones IPTV activas con JTI |
| `audit_logs` | Auditoría de todas las acciones |
| `channels` | Catálogo de canales (channel_key, stream_key, source_type) |

### Relaciones clave
```
subscribers ──< subscriptions (subscriber_id)
plans       ──< subscriptions (plan_id)
subscribers ──< devices (subscriber_id, CASCADE)
subscribers ──< sessions (subscriber_id, CASCADE)
devices     ──< sessions (device_id, SET NULL)
```

### Enums PostgreSQL
- `userrole`: admin, reseller
- `subscriberstatus`: active, expired, suspended, banned

---

## Redis — Claves

| Clave | TTL | Descripción |
|-------|-----|-------------|
| `nexora:session:{jti}` | 30 min | Access token admin activo |
| `nexora:refresh:{jti}` | 30 días | Refresh token admin activo |
| `nexora:blacklist:{jti}` | = token | Token admin revocado |
| `nexora:client:{jti}` | 24 h | Access token cliente activo |
| `nexora:client_refresh:{jti}` | 90 d | Refresh token cliente (rotación) |
| `nexora:login_attempts:{id}` | 15 min | Intentos fallidos de login |
| `nexora:lockout:{id}` | 15 min | Lockout activo |
| `nexora:heartbeat:{device_id}` | 180 s | Heartbeat dispositivo |
| `nexora:rate:{ip}:{path}` | 60 s | Rate limit sliding window |
| `nexora:active_conns:{sub_id}` | ZSET | Conexiones IPTV concurrentes (score=expire_unix) |
| `nexora:session:{session_jti}` | 4 h | Cache sesión IPTV (DB-backed) |
| `nexora:playback:{playback_jti}` | 60 s | Token de reproducción corto |
| `nexora:session_playbacks:{s_jti}` | SET | JTIs de playback emitidos bajo sesión |

---

## Flujo Auth — Cliente (Modern Client API)

```
POST /api/client/auth/login { username, password, device_id }
  -> STBService.authenticate_subscriber() — Argon2id verify
  -> DeviceService.register() — upsert device
  -> create_client_access_token() (24h) + create_client_refresh_token() (90d)
  -> Redis: nexora:client:{jti} + nexora:client_refresh:{jti}
  -> return { access_token, refresh_token, expires_in, subscriber_id }

POST /api/client/auth/refresh { refresh_token }
  -> decode_client_token() — verifica firma y tipo "client_refresh"
  -> Redis GETDEL nexora:client_refresh:{jti} — consume token (rotación)
  -> emite nuevo par de tokens
  -> return nuevo { access_token, refresh_token, ... }
```

---

## Flujo Playback — Flussonic Integration

```
POST /api/client/playback/authorize { channel_id, device_id }
  -> get_current_subscriber() — Bearer client_access
  -> ChannelService.get_active_by_key(channel_id) — obtiene stream_key (ECUADOR_TV)
  -> STBService.validate_active() — suscripción vigente
  -> STBService.validate_device() — dispositivo no bloqueado
  -> ConnectionService.open_connection() — ZSET slot concurrencia
  -> SessionService.create_iptv_session() — INSERT sessions DB
  -> _issue_jwt(ses, dev, chn=stream_key) — playback JWT 60s
  -> _resolve_playback_url(stream_key):
       if flussonic.is_configured:
           return flussonic.stream_hls_url(stream_key)
           # -> "http://181.78.246.211:8002/ECUADOR_TV/index.m3u8"
       else:
           return channel.source_url  # fallback
  -> return {
       token: "<JWT 60s>",
       expires_in: 60,
       channel_id: "canal-1",         <- channel_key, NUNCA stream_key
       subscriber_id: "...",
       playback_url: "http://181.78.246.211:8002/ECUADOR_TV/index.m3u8"
     }

Cliente -> hls.js carga playback_url directamente desde Flussonic
Nexora NO hace proxy de video.
```

---

## Flussonic Client — Modelo de Seguridad

```python
class _WriteBlocker:
    """Bloquea explícitamente todas las operaciones de escritura."""
    def create_stream(self): raise RuntimeError("READ-ONLY")
    def update_stream(self): raise RuntimeError("READ-ONLY")
    def delete_stream(self): raise RuntimeError("READ-ONLY")
    def restart_stream(self): raise RuntimeError("READ-ONLY")
    def reload_config(self):  raise RuntimeError("READ-ONLY")

class FlussonicClient(_WriteBlocker):
    def __init__(self, base_url, user, password):
        self._base = base_url
        self.__auth = (user, password)  # doble guion: truly private, no serializable

    def stream_hls_url(self, stream_name) -> str:
        return f"{self._base}/{stream_name}/index.m3u8"
        # Sin usuario/password en la URL

    def _client(self):
        return httpx.AsyncClient(auth=self.__auth, ...)
        # Credenciales solo en cabeceras HTTP internas — nunca en respuestas
```

**Garantías:**
- Credenciales solo en `.env` (backend) — `.gitignore`
- Ninguna respuesta de API devuelve usuario, password, ni cabecera Authorization Flussonic
- Operaciones write imposibles en runtime
- Frontend recibe solo: `playback_url`, `token`, `expires_in`

---

## Flujo Validate (Flussonic backend-auth callback)

```
POST /api/stb/auth/validate { token }  <- Flussonic llama a esto
  -> decode playback JWT
  -> Redis: nexora:playback:{jti} existe?
  -> Redis: nexora:active_conns:{sub} ZSET activo?
  -> Redis/DB: nexora:session:{ses} no revocada?
  -> 200 OK si todo válido — Flussonic sirve el stream
  -> 401 si token inválido/expirado/revocado
```

---

## Middleware

### RateLimitMiddleware
- `/api/client/auth/login`: **5 req/min**
- `/api/*/auth/login`, `/api/*/auth/refresh`: **10 req/min**
- `/api/*/auth/play`, `/api/*/playback/authorize`: **20 req/min**
- General: **60 req/min**
- Headers: `X-RateLimit-Limit`, `X-RateLimit-Remaining`
- Respuesta 429: `{"success": false, "error": "Too many requests"}`

### CORSMiddleware
- `DEBUG=true`: `allow_origins=["*"]`, `allow_credentials=False`
- Producción: origins explícitos, `allow_credentials=True`
- Vite dev proxy (`/api/* -> localhost:8000`) elimina CORS en desarrollo

---

## Web Player — Integración

```
web_player/vite.config.ts:
  proxy: { "/api": { target: "http://localhost:8000", changeOrigin: true } }

web_player/.env:
  VITE_NEXORA_API_BASE_URL=        <- vacío: usa proxy
  VITE_NEXORA_HEARTBEAT_INTERVAL_MS=45000
  VITE_NEXORA_PLAYBACK_RENEW_SKEW_SECONDS=15
  # SIN credenciales Flussonic

Flujo frontend:
  1. POST /api/client/auth/login -> guarda tokens en localStorage
  2. GET /api/client/catalog/channels -> 21 canales
  3. POST /api/client/playback/authorize -> recibe playback_url
  4. hls.js.loadSource(playback_url) -> reproduce directamente desde Flussonic
  5. POST /api/client/profile/devices/heartbeat cada 45s
```

---

## Alembic (async)

```python
# migrations/env.py usa:
from sqlalchemy.ext.asyncio import async_engine_from_config
# URL: postgresql+psycopg://  (psycopg3)
# poolclass=NullPool en migraciones
# WindowsSelectorEventLoopPolicy en Windows
```

Migraciones aplicadas:
- `001` — tablas base, índices, triggers updated_at
- `002` — tabla sessions + columnas fingerprint en devices
- `003` — tabla channels (21 canales seedeados)
