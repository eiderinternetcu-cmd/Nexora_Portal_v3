# ARCHITECTURE.md — Nexora API
_Last updated: 2026-05-17_

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

---

## Estructura de Carpetas

```
nexora_api/
├── app/
│   ├── main.py              # FastAPI app, lifespan, middleware, routers
│   ├── config.py            # Settings con pydantic-settings, .env
│   ├── database.py          # create_async_engine, async_sessionmaker, get_db
│   ├── redis_client.py      # get_redis, close_redis, key_* helpers
│   ├── models/              # SQLAlchemy ORM models
│   │   ├── user.py          # User (admin|reseller) — portal staff
│   │   ├── subscriber.py    # Subscriber — end user IPTV
│   │   ├── plan.py          # Plan — duración, max_connections, max_devices
│   │   ├── subscription.py  # Subscription — link subscriber ↔ plan + fechas
│   │   ├── device.py        # Device — fingerprint, MAC, android_id, blocked
│   │   ├── audit.py         # AuditLog — JSONB details, actor, action, target
│   │   └── session.py       # Session — JTI, device_id, expires_at, revoked_at
│   ├── schemas/             # Pydantic v2 request/response models
│   ├── core/
│   │   ├── security.py      # Argon2id, PyJWT, create_access/refresh_token
│   │   ├── dependencies.py  # FastAPI Depends: get_current_user, require_admin
│   │   └── exceptions.py    # NexoraException, unauthorized, forbidden, locked
│   ├── services/            # Business logic
│   │   ├── auth_service.py
│   │   ├── session_service.py
│   │   ├── user_service.py
│   │   ├── subscriber_service.py
│   │   ├── device_service.py
│   │   ├── plan_service.py
│   │   ├── audit_service.py
│   │   └── stb_service.py
│   ├── api/v1/              # Routers FastAPI
│   └── middleware/
│       └── rate_limit.py    # Redis sliding window
├── migrations/
│   ├── env.py               # Alembic async + psycopg3
│   └── versions/
│       ├── 001_initial_schema.py
│       └── 002_sessions_device_fingerprint.py  ← PENDIENTE
├── scripts/
│   └── create_admin.py
├── docker-compose.yml
├── Dockerfile
├── requirements.txt
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
| `sessions` | Sesiones activas con JTI ← PENDIENTE en migraciones |
| `audit_logs` | Auditoría de todas las acciones |

### Relaciones clave
```
users ──< subscriptions (created_by)
subscribers ──< subscriptions (subscriber_id)
plans ──< subscriptions (plan_id)
subscribers ──< devices (subscriber_id, CASCADE)
subscribers ──< sessions (subscriber_id, CASCADE)
devices ──< sessions (device_id, SET NULL)
users ──< audit_logs (actor_id, SET NULL)
```

### Enums PostgreSQL
- `userrole`: admin, reseller
- `subscriberstatus`: active, expired, suspended, banned

---

## Redis — Claves

| Clave | TTL | Descripción |
|-------|-----|-------------|
| `nexora:session:{jti}` | 30 min | Access token activo |
| `nexora:refresh:{jti}` | 30 días | Refresh token activo |
| `nexora:blacklist:{jti}` | igual al token | Token revocado |
| `nexora:login_attempts:{id}` | 15 min | Contador de intentos fallidos |
| `nexora:lockout:{id}` | 15 min | Lockout activo |
| `nexora:heartbeat:{device_id}` | 180s | Heartbeat de dispositivo |
| `nexora:rate:{ip}:{path}` | 60s | Rate limit sliding window |
| `nexora:active_conns:{sub_id}` | ZSET | Conexiones IPTV concurrentes ← PENDIENTE |

---

## Flujo Auth (Admin/Reseller)

```
POST /api/v1/auth/login
  → Check lockout (Redis)
  → Query User por username
  → verify_password Argon2id
  → create_access_token (PyJWT, HS256, 30min)
  → create_refresh_token (PyJWT, HS256, 30d)
  → store_access(jti) + store_refresh(jti) en Redis
  → UPDATE users SET last_login_at, last_login_ip
  → return { access_token, refresh_token, expires_in }

POST /api/v1/auth/refresh
  → decode refresh_token (PyJWT)
  → verify type == "refresh"
  → get_refresh(jti) en Redis (existe?)
  → revoke_refresh(old_jti) → blacklist
  → create new access + refresh tokens
  → store nuevos en Redis
  → return new pair

POST /api/v1/auth/logout
  → Bearer access_token → _get_token_payload
  → revoke_access(jti) → blacklist + delete session
  → si se pasa refresh_token: revoke_refresh también
```

---

## Flujo Devices (STB/App)

```
POST /api/v1/devices/register/{sub_id}
  → DeviceRegister { device_id, mac_address, android_id, device_fingerprint,
                     serial_hash, model, brand, device_type, app_version, os_version }
  → DeviceService.register():
      - SELECT device por device_id
      - Si existe: UPDATE (upsert por device_id)
      - Si no: INSERT nuevo
      - UPDATE last_ip, last_seen_at
  → AuditLog

POST /api/v1/devices/heartbeat
  → DeviceHeartbeat { device_id, subscriber_id }
  → DeviceService.heartbeat():
      - Validate subscriber activo (STBService)
      - UPDATE device.last_seen_at
      - SET nexora:heartbeat:{device_id} TTL 180s
  → return { ok, subscriber_status, last_seen }
```

---

## Flujo Subscribers

```
POST /api/v1/subscribers  (admin/reseller)
  → SubscriberCreate { username, password, email, phone, full_name, id_cedula }
  → SubscriberService.create():
      - Hash password Argon2id
      - Generar activation_code UUID4 corto
      - INSERT subscriber
  → AuditLog

GET /api/v1/subscribers/{id}/status
  → STBService.validate_active():
      - SELECT subscriber
      - SELECT subscription activa (is_active=True, expires_at > NOW())
      - COUNT devices
      - return { is_active, expires_at, max_connections, max_devices, device_count, days_remaining }
```

---

## Flujo STB (MAG/Android TV)

Actualmente `STBService` implementa:
- `authenticate_subscriber(username, password|activation_code)` — valida credenciales
- `validate_active(subscriber_id)` — verifica suscripción vigente
- `validate_device(device_id, subscriber_id)` — verifica dispositivo registrado y no bloqueado

**Pendiente Fase 3**: adaptador completo protocolo Stalker Middleware (endpoints `/portal/server/load.php` equivalentes)

---

## Middleware

### RateLimitMiddleware
- Paths estrictos (`/api/v1/auth/login`, `/api/v1/auth/refresh`): **10 req/min**
- General: **60 req/min** (configurable `RATE_LIMIT_PER_MINUTE`)
- Headers respuesta: `X-RateLimit-Limit`, `X-RateLimit-Remaining`
- Respuesta 429: `{"success": false, "error": "Too many requests"}`

### CORSMiddleware
- `DEBUG=true`: allow_origins=["*"]
- Producción: origins vacío (configurar explícitamente)

---

## Alembic (async)

```python
# migrations/env.py usa:
from sqlalchemy.ext.asyncio import async_engine_from_config
# URL: postgresql+psycopg://  (psycopg3)
# poolclass=NullPool en migraciones
```

Migraciones:
- `001` — todas las tablas base, índices, trigger updated_at
- `002` — sessions table, device fingerprint columns ← PENDIENTE
