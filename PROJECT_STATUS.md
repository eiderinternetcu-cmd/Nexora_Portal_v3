# PROJECT_STATUS.md — Nexora API
_Last updated: 2026-07-15_

> 📍 Estado y trabajo pendiente completos en [`docs/ROADMAP.md`](docs/ROADMAP.md). Runbook de prod en [`deploy/RUNBOOK_PRODUCTION_P0.md`](deploy/RUNBOOK_PRODUCTION_P0.md). Lo de abajo es histórico (Fases 1–4).

## ESTADO ACTUAL — 2026-07-15

**Producción `nexoraplay.net` (45.184.225.4) · Alembic 007 · stack `docker-compose.production.yml`.**

Desplegado y operativo en prod:
- **P0 seguridad**: `ENTITLEMENT_ENFORCE`, `JWT_REQUIRE_AUD`, `SIGNED_URL_ENFORCE` = ON; Nginx `auth_request` gate en `/stream/*` + grant Redis de segmentos.
- **M1 playback seguro**: Argon2id, device-secret (flag `DEVICE_SECRET_ENFORCE` off), grant hardening (`STREAM_GRANT_MAX_LIFETIME_SECONDS`=0, `STREAM_GRANT_TOKEN_FALLBACK`=on), concurrencia atómica de conexiones (Lua). Migración **006**.
- **M2 operación observable**: métricas de playback en `/admin/metrics` (success/failure por reason), auditoría admin **inmutable** (`/admin/audit`, trigger append-only), correlation-id (`X-Request-ID`), `/admin/nodes/health` (multinodo) + `/admin/alerts`. Migración **007**.

Flags **OFF** (activación posterior con ventana): `PLAYBACK_IP_BINDING_MODE` (2D), `DEVICE_SECRET_ENFORCE`, tope de grant.

Pendiente inmediato (P0): activar **2D** (IP-binding); resolver **alerting de nodos** (el backend no alcanza Flussonic directo — solo el edge; recomendado probe HLS firmado vía nginx — ver `docs/ROADMAP.md` P0.5).

Endpoints admin nuevos: `GET /api/admin/metrics` (con `.playback`), `/api/admin/nodes/health`, `/api/admin/alerts`, `/api/admin/audit`.

Migraciones: 001→**007** (005 plan_channels · 006 device secret/status · 007 audit_logs append-only).

---

## HISTÓRICO (Fases 1–4)

---

## FASE 1 — COMPLETADA ✅
## FASE 2 — COMPLETADA ✅
## FASE 3 — COMPLETADA ✅
- 3a ✅ · 3b ✅ · 3b.2 ✅ · 3c ✅ · 3c.1 Canal Catalog ✅ · 3d Flussonic Integration ✅ · 3e Web Player Docker ✅ · **3f Multi-device LAN ✅**
## FASE 4 — EN PROGRESO 🔧

---

## ESTRUCTURA ACTUAL DE ARCHIVOS

```
nexora_api/
├── app/
│   ├── config.py                          ✅  +flussonic nodes multi-region
│   ├── database.py                        ✅  psycopg3, async_sessionmaker
│   ├── main.py                            ✅  FastAPI, CORS, RateLimit (96 rutas)
│   ├── redis_client.py                    ✅  key helpers incl. key_client(), key_client_refresh()
│   ├── models/
│   │   ├── user.py                        ✅  User, UserRole (admin|reseller)
│   │   ├── subscriber.py                  ✅  Subscriber, SubscriberStatus
│   │   ├── plan.py                        ✅  Plan
│   │   ├── subscription.py               ✅  Subscription
│   │   ├── device.py                      ✅  Device + fingerprint, android_id, serial_hash
│   │   ├── audit.py                       ✅  AuditLog (JSONB details)
│   │   ├── session.py                     ✅  Session (IPTV DB sessions)
│   │   └── channel.py                     ✅  Channel (channel_key, stream_key, source_type, category)
│   ├── schemas/
│   │   ├── client.py                      ✅  FIXED: os_version max_length 32→512 (navigator.userAgent)
│   │   └── channel.py                     ✅  ChannelPublic, ChannelAdminOut, StreamStatusOut
│   ├── integrations/
│   │   └── flussonic_client.py            ✅  READ-ONLY, _WriteBlocker, singleton
│   ├── services/                          ✅  todos los servicios completos
│   ├── api/
│   │   ├── v1/                            ✅  /api/v1/ — legacy compat
│   │   ├── stb/                           ✅  /api/stb/ — heartbeat, register, connections, playback auth
│   │   ├── admin/                         ✅  +flussonic router, channels, sessions, subscriptions
│   │   └── client/                        ✅  /api/client/ — Modern Client API (auth, profile, catalog, playback)
│   └── middleware/
│       └── rate_limit.py                  ✅
├── migrations/
│   └── versions/
│       ├── 001_initial_schema.py          ✅
│       ├── 002_sessions_device_fingerprint.py ✅
│       └── 003_channels.py               ✅  tabla channels (21 filas)
├── scripts/
│   ├── create_admin.py                    ✅
│   ├── dev_server.py                      ✅  SelectorEventLoop Windows + Python 3.14
│   ├── seed_channels.py                   ✅
│   ├── map_flussonic_channels.py          ✅  mapeo DB -> Flussonic stream names
│   └── reset_test_password.py             ✅  utilidad dev
├── web_player/                            ✅  Vite + React + hls.js — DOCKERIZADO (Fase 3e)
│   ├── Dockerfile                         ✅  NUEVO: multi-stage Node build → nginx:1.27-alpine
│   ├── nginx.conf                         ✅  NUEVO: proxy /api/ → api:8000, SPA fallback
│   ├── .dockerignore                      ✅  NUEVO: excluye node_modules, dist, .env
│   ├── .env                               VITE_NEXORA_API_BASE_URL= (vacío → usa window.location.origin)
│   ├── vite.config.ts                     ✅  proxy /api/* -> localhost:8000 (dev local)
│   └── src/
│       └── player/
│           └── playbackUrl.ts             ✅  usa playback_url de /authorize (Flussonic URL)
├── mcp_server/
│   └── server.py                          ✅  14 herramientas
├── .env                                   ✅  FLUSSONIC_* vars (solo backend, en .gitignore)
├── requirements.txt                       ✅  +httpx
├── Dockerfile                             ✅  FastAPI container
└── docker-compose.yml                     ✅  ACTUALIZADO: +web_player service, fix REDIS/POSTGRES hosts
```

---

## ENDPOINTS DISPONIBLES

### /api/client/ — Modern Client API

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

### /api/admin/channels, /api/admin/flussonic, /api/admin/subscribers

Ver documentación anterior — sin cambios desde Fase 3d.

---

## ESTADO DEL ENTORNO DOCKER

| Contenedor | Imagen | Puerto | Estado |
|------------|--------|--------|--------|
| nexora_postgres | postgres:16-alpine | 5433→5432 | ✅ healthy |
| nexora_redis | redis:7-alpine | 6380→6379 | ✅ healthy |
| nexora_api | build ./Dockerfile | 8000→8000 | ✅ running (uvicorn --reload) |
| nexora_web_player | build ./web_player/Dockerfile | 5173→80 | ✅ running (nginx) |
| nexora_web | nexora_portal-500-v1-web | 80→80 | ✅ running (portal PHP legacy) |

**Arranque completo:**
```bash
docker compose up -d
# Todos los servicios se levantan automáticamente con restart: unless-stopped
```

**Acceso multi-dispositivo validado (2026-05-18):**

| Dispositivo | URL | Estado |
|-------------|-----|--------|
| Windows (local) | `http://127.0.0.1:5173` | ✅ |
| Mac (LAN) | `http://192.168.100.221` | ✅ |

> Puerto 5173 expuesto en `0.0.0.0` — accesible desde toda la LAN.
> Requiere regla de Firewall Windows: TCP inbound 5173 Allow.
> Firewall rule: `New-NetFirewallRule -DisplayName "Nexora Web Player 5173" -Direction Inbound -Protocol TCP -LocalPort 5173 -Action Allow -Profile Any` (ejecutar como Admin).

### Variables de entorno críticas (docker-compose.yml override)

El servicio `api` usa `env_file: .env` pero sobreescribe las variables de red para Docker:
```yaml
environment:
  - POSTGRES_HOST=postgres   # nombre del servicio Docker, no localhost
  - POSTGRES_PORT=5432       # puerto interno del container, no 5433
  - REDIS_HOST=redis         # nombre del servicio Docker, no localhost
  - REDIS_PORT=6379          # puerto interno del container, no 6380
```
> El `.env` usa `localhost:5433` y `localhost:6380` para desarrollo local (host machine).
> Docker sobreescribe estos valores para la red interna.

---

## CREDENCIALES Y ACCESOS

| Recurso | Valor |
|---------|-------|
| Web Player | `http://127.0.0.1:5173` |
| API Docs | `http://127.0.0.1:8000/docs` |
| API Health | `http://127.0.0.1:8000/health` |
| Admin user | `admin / Admin1234!` |
| Test subscriber | `testuser1 / NexoraTest123!` |
| Test subscription | activa hasta 2026-06-17 (UUID: `23486171-e6ec-4667-9008-4b207077617f`) |
| Flussonic EC | `181.78.246.211:8002` (READ-ONLY, credenciales en .env) |
| Flussonic CO | `38.210.187.13:8002` (READ-ONLY, credenciales en .env) |

---

## LOGIN END-TO-END — VALIDADO EN BROWSER (2026-05-18)

```
URL: http://127.0.0.1:5173
Usuario: testuser1
Password: NexoraTest123!

Flujo validado:
1. Browser → nginx (5173) → POST /api/client/auth/login → nexora_api (8000)
2. API autentica suscriptor, registra device, emite tokens JWT
3. GET /api/client/catalog/channels → 21 canales cargados
4. Home screen muestra perfil "Test User 29 días" + catálogo
```

**Bug resuelto:** `os_version` en `ClientLoginRequest` tenía `max_length=32`.
`navigator.userAgent` supera ese límite → validación Pydantic fallaba con "String should have at most 32 characters".
Fix: `max_length=512` en `app/schemas/client.py` (ClientLoginRequest + ClientDeviceRegister).

---

## FLUSSONIC INTEGRATION — Estado (Fase 3d)

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
- Todo flujo nuevo pasa por Client API (`/api/client/*`)
- Docker: servicios backend usan nombres de contenedor (redis, postgres), NO localhost

---

## ERRORES CONOCIDOS / PENDIENTES

| Issue | Estado |
|-------|--------|
| Streams Flussonic actualmente DOWN | Externo — depende de fuentes IPTV |
| `Noticiero_24/7` stream contiene `/` en nombre | Puede causar problemas URL — revisar |
| EPG real no implementado | Mock temporal en catalog.py |
| hls.js reproducción end-to-end en browser | ✅ Validado — Noticiero 24/7 reproduciendo |
| Sin signed URLs / backend-auth formal para Flussonic | Pendiente Fase 4 |
| SECRET_KEY en .env es placeholder | Cambiar por valor de 64 chars aleatorios en producción |
