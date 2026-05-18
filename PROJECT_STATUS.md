# PROJECT_STATUS.md — Nexora API
_Last updated: 2026-05-18_

---

## FASE 1 — COMPLETADA ✅
## FASE 2 — COMPLETADA ✅
## FASE 3 — EN PROGRESO 🔧
- 3a ✅ · 3b ✅ · 3b.2 ✅ · 3c ✅ · 3c.1 Canal Catalog ✅ · **3d Flussonic Integration ✅**

---

## ESTRUCTURA ACTUAL DE ARCHIVOS

```
nexora_api/
├── app/
│   ├── config.py                          ✅  +flussonic_base_url, flussonic_readonly_user/password, flussonic_readonly
│   ├── database.py                        ✅  psycopg3, async_sessionmaker
│   ├── main.py                            ✅  FastAPI, CORS, RateLimit (96 rutas)
│   ├── redis_client.py                    ✅  key helpers incl. key_client(), key_client_refresh()
│   ├── models/
│   │   ├── __init__.py                    ✅
│   │   ├── user.py                        ✅  User, UserRole (admin|reseller)
│   │   ├── subscriber.py                  ✅  Subscriber, SubscriberStatus
│   │   ├── plan.py                        ✅  Plan
│   │   ├── subscription.py               ✅  Subscription
│   │   ├── device.py                      ✅  Device + fingerprint, android_id, serial_hash
│   │   ├── audit.py                       ✅  AuditLog (JSONB details)
│   │   ├── session.py                     ✅  Session (IPTV DB sessions)
│   │   └── channel.py                     ✅  Channel (channel_key, stream_key, source_type, category)
│   ├── schemas/
│   │   ├── auth.py                        ✅
│   │   ├── common.py                      ✅
│   │   ├── user.py                        ✅
│   │   ├── subscriber.py                  ✅
│   │   ├── device.py                      ✅
│   │   ├── plan.py                        ✅
│   │   ├── subscription.py               ✅  +SubscriptionAdminCreate
│   │   ├── session.py                     ✅  SessionOut, SessionRevoke
│   │   ├── playback.py                    ✅  PlayRequest, PlaybackTokenOut, ValidateRequest/Response
│   │   ├── client.py                      ✅  login, token, profile, EPG, PlaybackResponse (+playback_url)
│   │   └── channel.py                     ✅  ChannelPublic, ChannelAdminOut, StreamStatusOut
│   ├── core/
│   │   ├── security.py                    ✅  Argon2id, PyJWT + create_client_access/refresh_token
│   │   ├── dependencies.py               ✅  get_current_user, require_admin, get_current_subscriber
│   │   └── exceptions.py                  ✅  NexoraException, unauthorized, forbidden, locked
│   ├── integrations/
│   │   ├── __init__.py                    ✅  NUEVO Fase 3d
│   │   └── flussonic_client.py            ✅  NUEVO Fase 3d — READ-ONLY, _WriteBlocker, singleton
│   ├── services/
│   │   ├── auth_service.py                ✅
│   │   ├── session_service.py             ✅
│   │   ├── connection_service.py          ✅  Redis ZSET concurrencia IPTV
│   │   ├── stream_auth_service.py         ✅
│   │   ├── subscription_service.py        ✅
│   │   ├── client_auth_service.py         ✅
│   │   ├── channel_service.py             ✅  list_active, get_by_key (READ ONLY)
│   │   ├── user_service.py                ✅
│   │   ├── subscriber_service.py         ✅
│   │   ├── device_service.py              ✅  heartbeat: ZSET + active_connections
│   │   ├── plan_service.py                ✅
│   │   ├── audit_service.py               ✅
│   │   └── stb_service.py                 ✅  authenticate_subscriber, validate_active, validate_device
│   ├── api/
│   │   ├── v1/                            ✅  /api/v1/ — legacy compat
│   │   ├── stb/                           ✅  /api/stb/ — heartbeat, register, connections, playback auth
│   │   ├── admin/
│   │   │   ├── router.py                  ✅  +flussonic router
│   │   │   ├── sessions.py                ✅
│   │   │   ├── subscriptions.py           ✅
│   │   │   ├── channels.py                ✅  +GET /{id}/stream-status (Flussonic live status)
│   │   │   └── flussonic.py               ✅  NUEVO Fase 3d: /health, /streams, /streams/{name}
│   │   ├── subscriber/                    placeholder
│   │   └── client/                        ✅  /api/client/ — Modern Client API
│   │       ├── auth.py                    login, refresh, logout
│   │       ├── profile.py                 profile, devices, heartbeat
│   │       ├── catalog.py                 canales DB + mock EPG
│   │       └── playback.py                ✅  ACTUALIZADO Fase 3d: playback_url desde Flussonic
│   └── middleware/
│       └── rate_limit.py                  ✅
├── migrations/
│   └── versions/
│       ├── 001_initial_schema.py          ✅
│       ├── 002_sessions_device_fingerprint.py ✅
│       └── 003_channels.py               ✅  tabla channels (21 filas)
├── scripts/
│   ├── create_admin.py                    ✅
│   ├── dev_server.py                      ✅
│   ├── seed_channels.py                   ✅
│   ├── map_flussonic_channels.py          ✅  NUEVO Fase 3d: mapeo DB -> Flussonic stream names
│   └── reset_test_password.py             ✅  utilidad dev
├── web_player/                            ✅  Vite + React + hls.js
│   ├── .env                               VITE_* vars (sin credenciales Flussonic)
│   ├── vite.config.ts                     ✅  proxy /api/* -> localhost:8000
│   └── src/
│       └── player/
│           └── playbackUrl.ts             ✅  usa playback_url de /authorize (Flussonic URL)
├── mcp_server/
│   └── server.py                          ✅  14 herramientas
├── .env                                   ✅  FLUSSONIC_* vars (solo backend, en .gitignore)
├── requirements.txt                       ✅  +httpx
├── Dockerfile                             ✅
└── docker-compose.yml                     ✅
```

---

## ENDPOINTS DISPONIBLES

### /api/client/ — Modern Client API (Fase 3c + 3d)

| Método | Ruta | Auth | Descripción |
|--------|------|------|-------------|
| POST | /api/client/auth/login | — | Login suscriptor + auto-register dispositivo |
| POST | /api/client/auth/refresh | — | Refresh token (rotación) |
| POST | /api/client/auth/logout | client_access | Logout + revoca tokens |
| GET | /api/client/profile | client_access | Perfil + estado suscripción |
| GET | /api/client/profile/devices | client_access | Lista de dispositivos |
| POST | /api/client/profile/devices/register | client_access | Registrar dispositivo adicional |
| POST | /api/client/profile/devices/heartbeat | client_access | Heartbeat autenticado |
| GET | /api/client/catalog/channels | client_access | 21 canales reales desde DB |
| GET | /api/client/catalog/channels/{key}/epg | client_access | EPG (mock) |
| POST | /api/client/playback/authorize | client_access | Token + URL HLS Flussonic |
| GET | /api/client/playback/{channel_id} | client_access | Reemite playback token |

**PlaybackResponse incluye:** `token` (60s JWT), `expires_in`, `channel_id`, `subscriber_id`, `playback_url` (HLS Flussonic directo)

### /api/admin/channels — Canal catalog (read-only)

| Método | Ruta | Auth | Descripción |
|--------|------|------|-------------|
| GET | /api/admin/channels | admin/reseller | Lista todos los canales |
| GET | /api/admin/channels/{id} | admin/reseller | Detalle (incluye stream_key) |
| GET | /api/admin/channels/{id}/stream-status | admin/reseller | Estado live en Flussonic |

### /api/admin/flussonic — Inspección Flussonic (Fase 3d)

| Método | Ruta | Auth | Descripción |
|--------|------|------|-------------|
| GET | /api/admin/flussonic/health | admin/reseller | Conectividad Flussonic (host:port only) |
| GET | /api/admin/flussonic/streams | admin/reseller | Lista streams Flussonic (sin credenciales) |
| GET | /api/admin/flussonic/streams/{name} | admin/reseller | Estado de stream específico |

### /api/admin/subscribers/{sub_id}/subscriptions

| Método | Ruta | Auth | Descripción |
|--------|------|------|-------------|
| POST | /api/admin/subscribers/{sub_id}/subscriptions | admin/reseller | Crear suscripción |
| GET | /api/admin/subscribers/{sub_id}/subscriptions | admin/reseller | Historial |
| POST | .../subscriptions/{id}/renew | admin/reseller | Renovar |
| POST | .../subscriptions/{id}/cancel | admin/reseller | Cancelar |

### /api/v1/, /api/admin/, /api/stb/

Ver documentación anterior — sin cambios en Fase 3d.

---

## ESTADO DEL ENTORNO LOCAL

| Componente | Estado | Detalle |
|------------|--------|---------|
| Docker postgres:16 | healthy | puerto 5433 |
| Docker redis:7 | healthy | puerto 6380 |
| FastAPI | running | puerto 8000, 96 rutas |
| Alembic | head (003) | 3 migraciones aplicadas |
| Admin user | creado | `admin / Admin1234!` |
| Flussonic | configured | 181.78.246.211:8002, 77 streams (actualmente DOWN) |
| Web Player | ready | Vite localhost:5173, proxy /api/* -> 8000 |

**Test subscriber:** `testuser1 / NexoraTest123!` (UUID: `23486171-e6ec-4667-9008-4b207077617f`)
**Test device:** `test-device-001`
**Test subscription:** activa hasta 2026-06-17

---

## FLUSSONIC INTEGRATION — Estado Validado (Fase 3d)

### Flujo de playback end-to-end (VALIDADO 2026-05-18)

```
1. POST /api/client/auth/login
   -> { access_token, refresh_token, subscriber_id }

2. GET /api/client/catalog/channels
   -> 21 canales (channel_key, name, category — SIN stream_key)

3. POST /api/client/playback/authorize { channel_id: "canal-1", device_id: "..." }
   -> {
        token: "<60s JWT>",
        expires_in: 60,
        channel_id: "canal-1",           <- channel_key, nunca stream_key
        subscriber_id: "...",
        playback_url: "http://181.78.246.211:8002/ECUADOR_TV/index.m3u8"
      }

4. POST /api/client/profile/devices/heartbeat
   -> { subscription_active: true, active_connections: 1 }
```

### Seguridad verificada

- Credenciales Flussonic (`SoporteEC`, `S0p0rt3.R3D`) NUNCA aparecen en ninguna respuesta
- `FlussonicClient` tiene `_WriteBlocker` — métodos write lanzan `RuntimeError`
- `stream_key` nunca se expone al cliente (solo `channel_key`/`channel_id`)
- HLS URLs no llevan usuario/password embebido: `http://HOST:PORT/{stream}/index.m3u8`
- Credenciales solo en `.env` (backend) — en `.gitignore`

### Mapeo de canales (21 canales)

| channel_key | Nombre | stream_key Flussonic | Categoría |
|-------------|--------|----------------------|-----------|
| canal-1 | Ecuador TV | ECUADOR_TV | general |
| canal-2 | GamaTv | GAMATV | general |
| canal-3 | Televicentro | TELEVICENTRO | general |
| canal-4 | RCN | RCN | general |
| canal-5 | Canal Uno | CANAL_UNO | general |
| canal-6 | Canal Uno Ecu | CANAL_UNO_ECU | general |
| canal-7 | Zaracay TV | ZARACAY_TV | general |
| canal-8 | Noticiero 24/7 | Noticiero_24/7 | news |
| canal-9 | Canal Local | CANAL_LOCAL | news |
| canal-10 | Teleandina | TELEANDINA | news |
| canal-11 | Telemar | TELEMAR | news |
| canal-12 | Telemar ESM | TELEMAR_ESM | news |
| canal-13 | Caracol Internacional | CARACOL_INTERNACIONAL | news |
| canal-14 | UCSG TV | UCSG_TV | news |
| canal-15 | ESPN | ESPN-CO | sports |
| canal-16 | ESPN 2 | ESPN-2CO | sports |
| canal-17 | ESPN 3 | ESPN-3CO | sports |
| canal-18 | ESPN 4 | ESPN-4CO | sports |
| canal-19 | TUDN | TUDN | sports |
| canal-20 | Adrenalina | ADRENALINA | sports |
| canal-21 | Oromar | OROMAR | sports |

---

## STREAM AUTH — Arquitectura de sesiones IPTV

```
JWT playback claims:
  sub  = subscriber_id
  dev  = device.id
  ses  = session_jti   (enlaza al registro en sessions DB)
  chn  = stream_key    (ECUADOR_TV, ESPN-CO, etc.)
  type = "playback"
  jti  = playback_jti
  iat, exp (60s)

Redis keys:
  nexora:client:{jti}               -> subscriber_id (TTL 24h access token)
  nexora:client_refresh:{jti}       -> subscriber_id (TTL 90d, rotación)
  nexora:active_conns:{sub_id}      -> ZSET (score=expire_unix, member=device_uuid)
  nexora:session:{session_jti}      -> cache sesión IPTV (TTL 4h)
  nexora:playback:{playback_jti}    -> token corto (TTL 60s)
```

---

## REGLAS ARQUITECTÓNICAS

- Nexora NO hace proxy de video — el cliente reproduce directo desde Flussonic
- Flussonic es READ ONLY desde Nexora — nunca se crean/modifican/eliminan streams
- Credenciales Flussonic solo en backend `.env`
- El cliente recibe solo: `playback_url`, `token`, `expires_in`
- Stalker/MAG/Xtream/PHP eliminados del flujo nuevo

---

## ERRORES CONOCIDOS / PENDIENTES

| Issue | Estado |
|-------|--------|
| Streams Flussonic actualmente DOWN | Externo — depende de fuentes IPTV |
| `Noticiero_24/7` stream contiene `/` en nombre | Puede causar problemas URL — revisar |
| EPG real no implementado | Mock temporal en catalog.py |
| hls.js en navegador no testado end-to-end | Pendiente — ver TODO_NEXT.md |
| Sin signed URLs / backend-auth formal para Flussonic | Pendiente Fase 4 |
