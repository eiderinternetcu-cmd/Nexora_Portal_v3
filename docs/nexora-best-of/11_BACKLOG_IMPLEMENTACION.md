# 11 — Backlog de implementación

> Tareas ejecutables (para Codex/Claude). Formato: **ID · título · descripción · módulo · prioridad · archivos · endpoints · tablas · validaciones · pruebas · criterio de aceptación**.
> Estado: ✅ ya hecho en `nexora_api` · 🟡 parcial · ⬜ nuevo. Agrupado en MVP / Fase 2 / Fase 3.

> **Importante:** muchos ítems MVP **ya existen** en `nexora_api` (marcados ✅/🟡). El backlog los lista para completitud y para señalar los huecos.

---

## MVP

### NX-AUTH — Auth cliente/admin
- **Estado:** ✅🟡 (existe; faltan MFA/lockout/audit/Argon2id-verify)
- **Descripción:** login, refresh rotativo, logout, rate-limit/lockout, hash Argon2id.
- **Módulo:** AuthService · **Archivos:** `app/services/auth_service.py`, `client_auth_service.py`, `app/api/v1/auth.py`, `app/api/client/auth.py`
- **Endpoints:** `POST /api/v1/auth/login`, `/api/client/auth/{login,refresh,logout}`
- **Tablas:** `users`, `subscribers`, `audit_log`(login) ⬜
- **Validaciones:** Argon2id constant-time; lockout N fallos; token rotado.
- **Pruebas:** login OK/KO, refresh-rotación, lockout, token expirado.
- **AC:** credenciales malas→401; refresh rotado revoca anterior; login admin auditado.

### NX-SUB — Subscribers
- **Estado:** ✅🟡 · **Descripción:** CRUD + estado enum + history + expiración.
- **Archivos:** `app/services/subscriber_service.py`, `app/api/admin/subscribers.py`
- **Endpoints:** `/api/admin/subscribers` CRUD, `/{id}/status`
- **Tablas:** `subscribers`, `subscriber_status_history` ⬜
- **AC:** suspender revoca sesiones+refresh; expiración bloquea playback (✅ verificado).

### NX-DEV — Devices (identidad fuerte)
- **Estado:** 🟡 · **Descripción:** registro device_id+serial+secret/cert; activación; sin auto-add silencioso; rate-limit re-binding.
- **Módulo:** DeviceService · **Archivos:** `app/services/device_service.py`, `app/api/client/profile.py`, `app/api/stb/*`
- **Endpoints:** `/api/client/profile/devices/{register,heartbeat}`, `/api/stb/handshake`
- **Tablas:** `devices` (+ `cert_fingerprint`,`device_secret_ref`,`status`), `device_commands` ⬜
- **Validaciones:** handshake HMAC; device nuevo=`pending`; límite.
- **Pruebas:** handshake OK, spoof MAC rechazado (negativo), límite.
- **AC:** device sin secret válido no obtiene token; MAC sola insuficiente.

### NX-SUBSCR — Subscriptions
- **Estado:** ✅ · **Endpoints:** `/api/admin/subscribers/{id}/subscriptions` create/renew/cancel
- **Tablas:** `subscriptions` · **AC:** crear desactiva activa previa; cancelar revoca sesiones (✅).

### NX-PLAN — Plans + paquetes/contenidos
- **Estado:** 🟡 (plan simple ✅; paquetes ⬜)
- **Descripción:** `packages`, `plan_packages(optional)`, `package_contents(content_type,content_id)`.
- **Tablas:** `plans`✅, `packages`,`plan_packages`,`package_contents` ⬜
- **Migración:** Alembic 005 ⬜
- **AC:** entitlement efectivo = subs→packages→contents resuelto en authorize.

### NX-CAT — Channel Catalog
- **Estado:** ✅🟡 · **Descripción:** canales/géneros, links N:M, parental, CRUD admin write.
- **Archivos:** `app/services/channel_service.py`, `app/api/client/catalog.py`, `app/api/admin/channels.py`
- **Endpoints:** `/api/client/catalog/channels` ✅, admin CRUD ⬜
- **Tablas:** `channels`✅, `genres`/`channel_genres` ⬜, `streaming.channel_streams` ⬜
- **AC:** cliente recibe `channel_key` (nunca `stream_key`); censored→PIN.

### NX-PLAY — Playback Authorization
- **Estado:** ✅🟡 · **Descripción:** authorize central con entitlement+parental.
- **Archivos:** `app/services/stream_auth_service.py`, `app/api/client/playback.py`
- **Endpoints:** `POST /api/client/playback/authorize`✅, `GET /playback/{id}`✅
- **Validaciones:** suscripción+entitlement(⬜)+parental(⬜)+device+concurrencia.
- **Pruebas:** canal no suscrito→403 (anti-IDOR); device ajeno→403.
- **AC:** único emisor de URL; sin entitlement no firma.

### NX-TOKEN — Stream Token Service
- **Estado:** 🟡 (validador ✅; firma+IP-binding ⬜)
- **Descripción:** firmar `playback_url` HMAC con IP+sesión+TTL; activar backend-auth Flussonic.
- **Archivos:** `app/services/stream_auth_service.py` (validate ✅), adapter ⬜, `app/api/stb/*`
- **Endpoints:** `POST /api/stb/auth/validate` ✅
- **AC:** URL manipulada/expirada/otra-IP → 401 en edge.

### NX-CONC — Concurrencia/Sesiones
- **Estado:** ✅🟡 · **Descripción:** ZSET atómico (Lua), límite por plan+device, histórico.
- **Archivos:** `app/services/connection_service.py`, `session_service.py`
- **Tablas:** `sessions`✅, `playback.sessions` particionada ⬜
- **Pruebas:** carrera (≤max), zapping (0 falsos 409), timeout libera.
- **AC:** nunca excede `max_connections` bajo concurrencia.

### NX-PROXY — Stream Proxy HTTPS
- **Estado:** ✅ · **Descripción:** `/stream/*` Nginx HTTPS, origen oculto (anti mixed-content).
- **Archivos:** `deploy/nginx/nexoraplay.conf` ✅
- **AC:** `playback_url` https same-origin; manifest 200 (✅ verificado).

### NX-AUDIT — Audit logs básicos
- **Estado:** 🟡 · **Tablas:** `audit_log` → inmutable/particionada ⬜
- **AC:** login admin + cambios de estado + emisión de token registrados.

### NX-WEB — Web Player
- **Estado:** ✅🟡 · **Archivos:** `web_player/*` (hls.js hardening, playbackRenewal)
- **AC:** reproduce con signed URLs; reconexión hls.js; renovación de token.

---

## Fase 2

| ID | Título | Módulo | Archivos/Endpoints | Tablas | AC |
|---|---|---|---|---|---|
| NX-RBAC | RBAC admin + resellers (RLS) | AdminService | `/api/admin/{roles,resellers}` ⬜ | `roles,permissions,role_permissions,resellers` | cada endpoint exige permiso; tenant aislado |
| NX-EPG | EPG ingest async + cron | EPGService | worker `epg_ingest`, `/api/client/.../epg` | `epg.sources`,`epg.programmes` | sin dups (UNIQUE); cron; sin XXE |
| NX-MON | Monitoring extendido + alertas | MonitoringService | Prometheus/OTel; `/admin/streams` | — | alerta de stream caído dispara |
| NX-FLU | Multi-Flussonic Registry + failover | FlussonicIntegration | `flussonic_registry.py` ⬜ | `streaming.nodes`,`zones` | failover de nodo probado |
| NX-NOTIF | Eventos/commands push a device | NotificationService | `/api/admin/devices/{id}/command` | `device_commands` | comando llega en heartbeat; acked |
| NX-AUDIT2 | Auditoría inmutable + retención | AuditLogService | `/api/admin/audit` | `audit_log` particionada | append-only; consulta filtrable |
| NX-PARENTAL | Control parental PIN server-side | Catalog/Playback | authorize | `channels.censored` | adulto sin PIN → 403 |

---

## Fase 3

| ID | Título | Módulo | AC |
|---|---|---|---|
| NX-VOD | VOD/Series (catálogo, rentals, resume) | VODService | de pago sin rental→403; resume funciona |
| NX-CATCHUP | Catch-up/timeshift (Flussonic DVR) | EPG+StreamToken | token TTL corto; anclado a EPG |
| NX-BILL | Billing (`BillingProvider`, webhooks idempotentes) | BillingService | webhook duplicado no recobra |
| NX-ASTRA | Adapter Astra | AstraIntegration | mismo `StreamProvider`; failover entre motores |
| NX-XC | XtreamCompat (read-only, signed URLs) | XtreamCompatService | sin credenciales en URL; mismo authorize |
| NX-APPS | Apps Android TV/Mobile/iOS | clientes | tras estabilizar playback/sesiones/obs |
| NX-CDN | Multi-región / CDN / GeoDNS | infra | failover de región probado |
| NX-IAC | IaC + artefactos firmados + DR | producción | deploy idempotente; restore probado |

---

## Orden sugerido (resumen)
1. **Cerrar MVP de seguridad:** Argon2id-verify, device secret/cert, entitlement por paquete, signed URL+IP-binding (activar backend-auth), concurrencia Lua, audit login.
2. **Fase 2:** RBAC+resellers, EPG real, monitoreo+alertas, Flussonic registry+failover, parental.
3. **Fase 3:** VOD/series/catch-up, billing, Astra, XtreamCompat, apps, multi-región, IaC.

> Cada ítem nuevo de datos ⇒ **migración Alembic** numerada (siguiente: 005 paquetes/contenidos).
