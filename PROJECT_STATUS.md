# PROJECT_STATUS.md — Nexora API
_Last updated: 2026-05-17_

---

## FASE 1 — COMPLETADA ✅

## FASE 2 — COMPLETADA ✅

## FASE 3 — EN PROGRESO 🔧 (3a ✅ · 3b ✅ · 3b.2 ✅ · 3c ✅ · 3c.1 Canal Catalog ✅)

---

## ESTRUCTURA ACTUAL DE ARCHIVOS

```
nexora_api/
├── app/
│   ├── config.py                          ✅
│   ├── database.py                        ✅  psycopg3, async_sessionmaker
│   ├── main.py                            ✅  FastAPI 2.0.0, 5 dominios, CORS, RateLimit (92 rutas)
│   ├── redis_client.py                    ✅  key helpers incl. key_client(), key_client_refresh()
│   ├── models/
│   │   ├── __init__.py                    ✅  Session importada y en __all__
│   │   ├── user.py                        ✅  User, UserRole (admin|reseller)
│   │   ├── subscriber.py                  ✅  Subscriber, SubscriberStatus
│   │   ├── plan.py                        ✅  Plan
│   │   ├── subscription.py               ✅  Subscription
│   │   ├── device.py                      ✅  Device + android_id, device_fingerprint, serial_hash
│   │   ├── audit.py                       ✅  AuditLog (JSONB details)
│   │   └── session.py                     ✅  Session (importada, migración aplicada)
│   ├── schemas/
│   │   ├── auth.py                        ✅
│   │   ├── common.py                      ✅
│   │   ├── user.py                        ✅
│   │   ├── subscriber.py                  ✅
│   │   ├── device.py                      ✅
│   │   ├── plan.py                        ✅
│   │   ├── subscription.py               ✅  +SubscriptionAdminCreate (Fase 3b.2)
│   │   ├── session.py                     ✅  SessionOut, SessionRevoke
│   │   ├── playback.py                    ✅  PlayRequest, PlaybackTokenOut, ValidateRequest/Response, TokenRequest
│   │   ├── client.py                      ✅  NUEVO Fase 3c: login, token, profile, EPG, playback schemas
│   │   └── channel.py                     ✅  NUEVO Fase 3c.1: ChannelPublic, ChannelAdminOut
│   ├── core/
│   │   ├── security.py                    ✅  Argon2id, PyJWT + create_client_access/refresh_token (Fase 3c)
│   │   ├── dependencies.py               ✅  get_current_user, require_admin, get_current_subscriber (Fase 3c)
│   │   └── exceptions.py                  ✅  NexoraException, unauthorized, forbidden, locked
│   ├── services/
│   │   ├── auth_service.py                ✅  login (lockout x username+IP), refresh, logout
│   │   ├── session_service.py             ✅  REESCRITO Fase 3b: +create_iptv_session, get_active_iptv_session, touch, revoke, revoke_subscriber_sessions
│   │   ├── connection_service.py          ✅  Fase 2: Redis ZSET concurrencia IPTV
│   │   ├── stream_auth_service.py         ✅  REESCRITO Fase 3b: ses claim, DB session, validate con DB check
│   │   ├── subscription_service.py        ✅  NUEVO Fase 3b.2: create, list, renew, cancel
│   │   ├── client_auth_service.py         ✅  NUEVO Fase 3c: login+device auto-register, token rotation, logout
│   │   ├── channel_service.py             ✅  NUEVO Fase 3c.1: list_active, get_by_key, get_active_by_key (READ ONLY)
│   │   ├── user_service.py                ✅
│   │   ├── subscriber_service.py         ✅
│   │   ├── device_service.py              ✅  heartbeat actualizado: extiende ZSET + retorna active_connections
│   │   ├── plan_service.py                ✅
│   │   ├── audit_service.py               ✅
│   │   └── stb_service.py                 ✅  authenticate_subscriber, validate_active, validate_device
│   ├── api/
│   │   ├── v1/                            ✅  /api/v1/ — legacy compat, todos los endpoints
│   │   │   ├── router.py
│   │   │   ├── auth.py
│   │   │   ├── users.py
│   │   │   ├── subscribers.py
│   │   │   ├── devices.py
│   │   │   └── plans.py
│   │   ├── stb/
│   │   │   ├── __init__.py
│   │   │   ├── router.py                  monta devices + playback
│   │   │   ├── devices.py                 heartbeat, register, connections
│   │   │   └── playback.py                ✅  NUEVO Fase 3: /auth/play, /auth/validate, /auth/token
│   │   ├── admin/                         ✅  /api/admin/
│   │   │   ├── __init__.py
│   │   │   ├── router.py                  monta auth, users, subscribers, devices, plans, sessions, subscriptions
│   │   │   ├── sessions.py                GET/DELETE por subscriber o JTI (Fase 2 + 3b)
│   │   │   └── subscriptions.py           ✅  NUEVO Fase 3b.2: create, list, renew, cancel
│   │   ├── stb/                           ✅  /api/stb/ — NUEVO Fase 2
│   │   │   ├── __init__.py
│   │   │   ├── router.py
│   │   │   └── devices.py                 heartbeat sin auth, register, connections
│   │   ├── subscriber/                    ✅  /api/subscriber/ — placeholder
│   │   │   ├── __init__.py
│   │   │   └── router.py                  /ping — en construcción
│   │   └── client/                        ✅  NUEVO Fase 3c: /api/client/ — Android TV, mobile, iOS, web
│   │       ├── __init__.py
│   │       ├── router.py
│   │       ├── auth.py                    login, refresh, logout (lockout separado sub:*)
│   │       ├── profile.py                 profile, devices list/register/heartbeat
│   │       ├── catalog.py                 DB channels (21 canales) + mock EPG
│   │       └── playback.py                POST /authorize (channel_key→stream_key), GET /{channel_id}
│   └── middleware/
│       └── rate_limit.py                  ✅  per-path limits incl. /api/client/*
├── migrations/
│   ├── env.py                             ✅  async Alembic + psycopg3 + WindowsSelectorEventLoopPolicy
│   └── versions/
│       ├── 001_initial_schema.py          ✅  6 tablas + triggers + índices
│       ├── 002_sessions_device_fingerprint.py ✅  NUEVO Fase 2: tabla sessions + cols fingerprint devices
│       └── 003_channels.py               ✅  NUEVO Fase 3c.1: tabla channels (21 filas seedeadas)
├── scripts/
│   ├── create_admin.py                    ✅
│   └── dev_server.py                      ✅  NUEVO: SelectorEventLoop para Windows + Python 3.14
├── mcp_server/
│   └── server.py                          ✅  14 herramientas, FastMCP, registrado en claude CLI
├── requirements.txt                       ✅
├── Dockerfile                             ✅  python:3.12-slim
├── docker-compose.yml                     ✅  postgres:16, redis:7 (fix empty requirepass)
└── alembic.ini                            ✅
```

---

## ENDPOINTS DISPONIBLES

### /api/v1/ — Legacy compat

| Método | Ruta | Auth | Descripción |
|--------|------|------|-------------|
| GET | /health | — | Health check |
| POST | /api/v1/auth/login | — | Login admin/reseller |
| POST | /api/v1/auth/refresh | — | Refresh token (rotation) |
| POST | /api/v1/auth/logout | Bearer | Logout + blacklist JTI |
| GET | /api/v1/auth/me | Bearer | Usuario actual |
| GET | /api/v1/users | admin | Listar usuarios |
| POST | /api/v1/users | admin | Crear usuario |
| GET | /api/v1/users/{id} | admin | Ver usuario |
| PATCH | /api/v1/users/{id} | admin | Editar usuario |
| DELETE | /api/v1/users/{id} | admin | Eliminar usuario |
| GET | /api/v1/subscribers | admin/reseller | Listar suscriptores |
| POST | /api/v1/subscribers | admin/reseller | Crear suscriptor |
| GET | /api/v1/subscribers/{id} | admin/reseller | Ver suscriptor |
| PATCH | /api/v1/subscribers/{id} | admin/reseller | Editar suscriptor |
| POST | /api/v1/subscribers/{id}/set-password | admin/reseller | Cambiar password |
| GET | /api/v1/subscribers/{id}/status | admin/reseller | Estado + suscripción activa |
| POST | /api/v1/subscribers/{id}/suspend | admin/reseller | Suspender |
| POST | /api/v1/subscribers/{id}/activate | admin/reseller | Activar |
| DELETE | /api/v1/subscribers/{id} | admin/reseller | Eliminar |
| GET | /api/v1/devices/subscriber/{id} | admin/reseller | Dispositivos de suscriptor |
| POST | /api/v1/devices/register/{sub_id} | admin/reseller | Registrar dispositivo |
| POST | /api/v1/devices/heartbeat | — | Heartbeat dispositivo |
| POST | /api/v1/devices/{id}/block | admin/reseller | Bloquear dispositivo |
| POST | /api/v1/devices/{id}/unblock | admin/reseller | Desbloquear dispositivo |
| DELETE | /api/v1/devices/{id} | admin/reseller | Eliminar dispositivo |
| GET | /api/v1/plans | admin/reseller | Listar planes |
| POST | /api/v1/plans | admin | Crear plan |
| GET | /api/v1/plans/{id} | admin/reseller | Ver plan |
| PATCH | /api/v1/plans/{id} | admin | Editar plan |
| DELETE | /api/v1/plans/{id} | admin | Eliminar plan |

### /api/admin/ — NUEVO Fase 2

| Método | Ruta | Auth | Descripción |
|--------|------|------|-------------|
| POST | /api/admin/auth/login | — | Login admin (mismo handler v1) |
| POST | /api/admin/auth/refresh | — | Refresh token |
| POST | /api/admin/auth/logout | Bearer | Logout |
| GET | /api/admin/auth/me | Bearer | Usuario actual |
| GET | /api/admin/users | admin | Listar usuarios |
| GET | /api/admin/subscribers | admin/reseller | Listar suscriptores |
| GET | /api/admin/sessions/subscriber/{sub_id} | admin | Listar sesiones IPTV DB |
| DELETE | /api/admin/sessions/subscriber/{sub_id} | admin | Revocar todas las sesiones |
| DELETE | /api/admin/sessions/{jti} | admin | Revocar sesión por JTI |

### /api/stb/ — Fase 2 + Fase 3

| Método | Ruta | Auth | Descripción |
|--------|------|------|-------------|
| POST | /api/stb/heartbeat | — | Heartbeat STB (extiende ZSET Redis) |
| POST | /api/stb/register/{sub_id} | admin/reseller | Registrar dispositivo STB |
| GET | /api/stb/connections/{sub_id} | admin/reseller | Conexiones activas desde ZSET |
| POST | /api/stb/auth/play | — | Autorización completa + emite playback token |
| POST | /api/stb/auth/validate | — | Valida token (callback Flussonic backend-auth) |
| POST | /api/stb/auth/token | — | Reemite playback token para dispositivo activo |

### /api/admin/subscribers/{sub_id}/subscriptions — NUEVO Fase 3b.2

| Método | Ruta | Auth | Descripción |
|--------|------|------|-------------|
| POST | /api/admin/subscribers/{sub_id}/subscriptions | admin/reseller | Crear suscripción (desactiva activa previa) |
| GET | /api/admin/subscribers/{sub_id}/subscriptions | admin/reseller | Historial de suscripciones |
| POST | /api/admin/subscribers/{sub_id}/subscriptions/{id}/renew | admin/reseller | Renovar (extiende expires_at) |
| POST | /api/admin/subscribers/{sub_id}/subscriptions/{id}/cancel | admin/reseller | Cancelar + revocar sesiones IPTV |

### /api/subscriber/ — Placeholder Fase 3

| Método | Ruta | Auth | Descripción |
|--------|------|------|-------------|
| GET | /api/subscriber/ping | — | Estado (en construcción) |

### /api/client/ — NUEVO Fase 3c (Modern Client API)

| Método | Ruta | Auth | Descripción |
|--------|------|------|-------------|
| POST | /api/client/auth/login | — | Login suscriptor + auto-register dispositivo |
| POST | /api/client/auth/refresh | — | Refresh token (rotación) |
| POST | /api/client/auth/logout | client_access | Logout + revoca tokens |
| GET | /api/client/profile | client_access | Perfil + estado suscripción |
| GET | /api/client/profile/devices | client_access | Lista de dispositivos |
| POST | /api/client/profile/devices/register | client_access | Registrar dispositivo adicional |
| POST | /api/client/profile/devices/heartbeat | client_access | Heartbeat autenticado |
| GET | /api/client/catalog/channels | client_access | Lista de canales activos (DB — 21 canales) |
| GET | /api/client/catalog/channels/{channel_key}/epg | client_access | EPG del canal (mock temporal) |
| POST | /api/client/playback/authorize | client_access | Autorización completa + IPTV session |
| GET | /api/client/playback/{channel_id}?device_id= | client_access | Reemite playback token (ligero) |

**Tokens:** `client_access` (24h, Redis nexora:client:{jti}) · `client_refresh` (90d, nexora:client_refresh:{jti})
**Lockout:** prefijo `sub:{username}` — separado del lockout admin

### /api/admin/channels — NUEVO Fase 3c.1 (read-only)

| Método | Ruta | Auth | Descripción |
|--------|------|------|-------------|
| GET | /api/admin/channels | admin/reseller | Lista todos los canales (activos e inactivos) |
| GET | /api/admin/channels/{id} | admin/reseller | Detalle completo (incluye stream_key, source_type) |

**Nota:** Flussonic/Astra son READ ONLY. Nunca se modifican desde la API.

---

## ESTADO DEL ENTORNO LOCAL

| Componente | Estado | Detalle |
|------------|--------|---------|
| Docker postgres:16 | healthy | puerto 5433 |
| Docker redis:7 | healthy | puerto 6380 |
| FastAPI | running | puerto 8000, 65+ rutas |
| Alembic | head (002) | ambas migraciones aplicadas |
| Admin user | creado | `admin / Admin1234!` |

**Test subscriber:** `23486171-e6ec-4667-9008-4b207077617f`
**Test device:** `933ed347-9e87-48c0-8537-dc301b849af1` (MAC: AA:BB:CC:DD:EE:FF)

---

## STREAM AUTH — Arquitectura de sesiones IPTV

```
PostgreSQL (nexora:sessions):
  access_token_jti = session_jti (UUID v4, identificador de sesión IPTV)
  device_id        = Device.id (UUID interno)
  ip_address       = IP del dispositivo en el momento de /auth/play
  user_agent       = User-Agent del request
  expires_at       = now + 4h (14400s)
  last_heartbeat_at → actualizado en cada heartbeat

Redis keys:
  nexora:session:{session_jti}          → cache de sesión DB (TTL: 4h)
  nexora:session_playbacks:{session_jti}→ SET de playback JTIs emitidos bajo esta sesión
  nexora:playback:{playback_jti}        → token corto (TTL: 60s)
  nexora:active_conns:{sub_id}          → ZSET conexiones activas (score=expire_unix)

JWT playback claims:
  sub  = subscriber_id (UUID string)
  dev  = device.id     (UUID interno — usado en ZSET)
  ses  = session_jti   (enlaza al registro en sessions DB)
  chn  = channel_id    (opcional)
  type = "playback"
  jti  = playback_jti  (único por token)
  iat, exp

Flujo /auth/play:
  1. subscriber active + subscription vigente
  2. device no bloqueado + belongs to subscriber
  3. ConnectionService.open_connection() → ZSET slot
  4. SessionService.create_iptv_session() → INSERT sessions (revoca sesión anterior del device)
  5. _issue_jwt(ses=session_jti) → playback JWT
  6. _store_jwt() → Redis playback key + SADD session_playbacks set

Flujo /auth/validate (Flussonic callback):
  1. JWT válido (firma + expiración)
  2. nexora:playback:{jti} existe en Redis (no revocado)
  3. nexora:active_conns:{sub} ZSET activo
  4. nexora:session:{ses} existe (fast) ó DB query (fallback) → sesión no revocada

Revocación admin:
  DELETE /api/admin/sessions/{jti}:
    → revoked_at en DB
    → DEL nexora:session:{jti}
    → SMEMBERS nexora:session_playbacks:{jti} → DEL cada nexora:playback:{pjti}
    → ConnectionService.close_connection() → ZREM del ZSET
```

---

## QUÉ FUNCIONA (VALIDADO)

- FastAPI arranca en Windows con SelectorEventLoop (scripts/dev_server.py)
- Redis ZSET: `nexora:active_conns:{sub_id}` — score=expire_unix, member=device_uuid
- Heartbeat extiende conexión en ZSET y retorna `active_connections` + `max_connections`
- `GET /api/stb/connections/{sub_id}` → count + lista de device_ids activos
- `GET /api/admin/sessions/subscriber/{sub_id}` → [] (vacío correcto; sesiones DB son Fase 3)
- Rate limiting per-path: 429 tras 10 intentos en `/api/v1/auth/login`
- Lockout: 423 tras 5 intentos fallidos (15 min)
- Alembic: migración 002 aplicada (tabla sessions + cols fingerprint en devices)
- Login funciona en `/api/v1/auth/login` y `/api/admin/auth/login`
- Token blacklist en Redis por JTI
- Refresh token rotation

---

## ERRORES CONOCIDOS / PENDIENTES

| Issue | Estado |
|-------|--------|
| `scripts/dev_server.py` — sintaxis walrus en sys.path | Menor, funciona igual |
| Sin STB legacy adapter (Stalker Middleware compat) | Fase 3c |
| Sin Android TV endpoints | Fase 3d |
| `secret_key` por defecto inseguro | Configurar en .env producción |
