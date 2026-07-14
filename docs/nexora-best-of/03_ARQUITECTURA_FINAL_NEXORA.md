# 03 — Arquitectura final Nexora

> 18 servicios de dominio. Para cada uno: responsabilidad · endpoints · tablas · Redis keys · eventos/logs · reglas · validaciones · riesgos · prioridad. Estado: ✅ existe en `nexora_api` · 🟡 parcial · ⬜ por construir.

---

## Diagrama de alto nivel

```
                ┌──────── Nginx / Edge (TLS, WAF, rate-limit, /stream/* HTTPS) ────────┐
   Admin Next ──┤  /admin/*        /api/client/*      /api/stb/*      /stream/*          │
   Player Web ──┤      │                 │                │              │ (HLS firmado)  │
   Apps TV/Mob ─┘      ▼                 ▼                ▼              ▼                 │
              Admin/RBAC        Auth · Subscriber     Auth · Device   (origen oculto)     │
                                Plan · Catalog        Playback Auth ──► Stream Token ──► edge
                                    │   │                  │                │             │
                                    ▼   ▼                  ▼                ▼             │
              ┌──────────── PostgreSQL (FK, RLS, particiones) ─────────┐  ┌─ Redis ──────┐
              │ subscribers, devices, plans, packages, subscriptions,  │  │ sesiones,    │
              │ channels, channel_streams, epg, videos, playback_      │  │ tokens,      │
              │ sessions, audit_log, events …                          │  │ concurrencia,│
              └─────────────────────────────────────────────────────────┘  rate-limit ──┘
   EPG/VOD/Billing/Audit/Monitoring (async workers)   Flussonic/Astra (edges) ◄─ Integration Svc
```

**Capas:** Admin API (`/api/admin`,`/api/v1`) · Client API (`/api/client`) · STB API (`/api/stb`) — separación ya presente en `nexora_api`.

---

## Los 18 servicios

### 1) AuthService ✅🟡
- **Responsabilidad:** login admin y suscriptor, emisión/rotación JWT, refresh, MFA opcional, lockout.
- **Endpoints:** `POST /api/v1/auth/login` (admin), `POST /api/client/auth/login` · `/refresh` · `/logout`, `POST /auth/mfa/verify` ⬜.
- **Tablas:** `users` (admin), `subscribers`, `audit_logins` ⬜.
- **Redis:** `nexora:client:{jti}`, `nexora:client_refresh:{jti}` (✅), lockout `nexora:login_fail:{user}:{ip}` ⬜.
- **Reglas:** Argon2id 🟡(verificar); access corto + refresh rotativo revocable; constant-time; lockout backoff.
- **Validaciones:** expiración/rotación de token; revocación en logout.
- **Riesgos:** robo de refresh → device-binding + rotación.
- **Prioridad:** 🔴 MVP (existe; falta MFA, lockout, audit login).

### 2) AdminService ✅🟡
- **Responsabilidad:** operadores, **RBAC**, resellers (multi-tenant), settings.
- **Endpoints:** `/api/admin/users`, `/roles` ⬜, `/resellers` ⬜, `/settings` ⬜.
- **Tablas:** `users`, `roles` ⬜, `permissions` ⬜, `role_permissions` ⬜, `resellers` ⬜, `settings(jsonb)` ⬜.
- **Reglas:** RBAC real (sin superuser-bypass implícito); aislamiento por `reseller_id` (RLS PostgreSQL).
- **Riesgos:** escalada horizontal → RLS + tests de tenancy.
- **Prioridad:** 🟠 F2.

### 3) SubscriberService ✅
- **Responsabilidad:** alta/baja/suspensión, perfiles, expiración, feature-flags.
- **Endpoints:** `/api/admin/subscribers` (CRUD), `/{id}/status`, `/api/client/profile`.
- **Tablas:** `subscribers (status enum)`, `subscriber_status_history` ⬜, `subscriber_features` ⬜.
- **Reglas:** estado **explícito** (no invertido); expiración por suscripción; soft-delete + audit.
- **Prioridad:** 🟠 MVP (existe; falta history/features).

### 4) DeviceService ✅🟡
- **Responsabilidad:** registro (device_id/MAC/serial/cert), presencia/heartbeat, comandos remotos, bloqueo.
- **Endpoints:** `/api/client/profile/devices/register|heartbeat`, `/api/stb/handshake` 🟡, `/devices/{id}/command` ⬜.
- **Tablas:** `devices` (+ `device_fingerprint`, `serial_hash`, `android_id`), `device_commands` ⬜.
- **Redis:** presencia `nexora:device_seen:{id}` 🟡; límite de devices.
- **Reglas:** verificación MAC **+ serial + device_secret/cert**; **sin auto-provisión silenciosa** (activation code); rate-limit re-registro.
- **Riesgos:** spoof MAC → cert/secret resuelve.
- **Prioridad:** 🔴 MVP (existe registro; falta secret/cert + activation + commands).

### 5) PlanService ✅🟡
- **Responsabilidad:** planes, paquetes, contenidos por paquete.
- **Endpoints:** `/api/v1/plans` (CRUD ✅), `/packages` ⬜, `/plans/{id}/packages` ⬜.
- **Tablas:** `plans` ✅, `packages` ⬜, `plan_packages(optional)` ⬜, `package_contents(content_type,content_id)` ⬜.
- **Reglas:** obligatorios auto-aplicados; opcionales por API; FK reales.
- **Prioridad:** 🟠 MVP→F2 (plan simple existe; falta paquetes/contenidos).

### 6) SubscriptionService ✅
- **Responsabilidad:** asignar/renovar/cancelar suscripciones; vigencia.
- **Endpoints:** `/api/admin/subscribers/{id}/subscriptions` (create/renew/cancel ✅).
- **Tablas:** `subscriptions (starts_at, expires_at, is_active)` ✅.
- **Reglas:** desactiva activa previa al crear; cancelación revoca sesiones IPTV; auditoría.
- **Prioridad:** 🔴 MVP (existe).

### 7) ChannelCatalogService ✅🟡
- **Responsabilidad:** canales, categorías/géneros, links físicos, control parental, orden.
- **Endpoints:** `/api/client/catalog/channels` ✅, `/api/admin/channels` ✅(read), CRUD admin ⬜, `/genres` ⬜.
- **Tablas:** `channels` ✅, `channel_streams` ⬜(hoy en `channel`), `categories/genres` 🟡, `channel_genres` ⬜.
- **Reglas:** `requires_subscription`/`censored` con **PIN server-side** ⬜; orden por número/favoritos.
- **Riesgos:** exponer `stream_key` al cliente (prohibido) → solo `channel_key`.
- **Prioridad:** 🟠 MVP (catálogo existe; falta links N:M, parental, CRUD admin write).

### 8) PlaybackAuthorizationService ✅ ★
- **Responsabilidad:** decidir si suscriptor/dispositivo puede reproducir **ANTES** de firmar URL.
- **Endpoints:** `POST /api/client/playback/authorize` ✅ → token + playback_url; `GET /playback/{id}` (reemite) ✅.
- **Tablas:** lee `subscriptions`, `package_contents` ⬜, `channels.requires_subscription`, `devices.status`.
- **Redis:** concurrencia (ver §9); token corto `nexora:playback:{jti}` ✅.
- **Reglas:** suscripción vigente **+** contenido en paquete **+** parental **+** device activo **+** concurrencia. **Único** emisor de URL.
- **Riesgos:** ninguno si es el único punto de autorización (cierra IDOR).
- **Prioridad:** 🔴 MVP (existe `StreamAuthService.authorize`; falta entitlement por paquete + parental).

### 9) StreamTokenService 🟡 ★
- **Responsabilidad:** firmar URLs por proveedor (HMAC) y validarlas en el edge.
- **Endpoints:** interno (`mint(content,node,client_ip,ttl)`); validador `POST /api/stb/auth/validate` ✅ (backend-auth Flussonic).
- **Reglas:** **HMAC-SHA256**, ligado a **IP+sesión+TTL** corto; secretos en Vault, rotables; `TokenAdapter` por motor (Flussonic MVP).
- **Riesgos:** fuga de secreto → rotación + secreto por edge.
- **Prioridad:** 🔴 MVP (validador existe; falta activar firma en URL + IP-binding en Flussonic).

### 10) SessionConcurrencyService ✅
- **Responsabilidad:** sesiones de playback, límite concurrencia, presencia, heartbeat.
- **Endpoints:** implícito en authorize/heartbeat; `DELETE /sessions/{id}` (admin) 🟡.
- **Tablas:** `sessions` (IPTV) ✅, `playback_sessions` (histórico particionado) ⬜.
- **Redis:** ZSET `nexora:active_conns:{sub}` ✅, `nexora:session:{ses}` ✅.
- **Reglas:** límite por suscriptor/plan server-side; heartbeat con token; timeout → libera slot.
- **Prioridad:** 🔴 MVP (existe; falta histórico particionado + límite por plan formal).

### 11) EPGService ⬜
- **Responsabilidad:** ingest XMLTV async, mapeo a canales, consulta de guía.
- **Endpoints:** `/api/client/catalog/channels/{key}/epg` 🟡(mock), `/epg?from&to`, admin `/epg/sources`.
- **Tablas:** `epg (UNIQUE(channel_id,start_at), particionada)`, `epg_sources`.
- **Reglas:** worker async + **cron**; parser sin XXE; límite tamaño/rate; dedup por constraint.
- **Prioridad:** 🟢 F2 (hoy EPG mock).

### 12) VODService ⬜
- **Responsabilidad:** películas/series, metadata, alquiler, resume.
- **Endpoints:** `/api/client/vod`, `/videos/{id}`, `/series/{id}/episodes`, `/play/vod/{id}`.
- **Tablas:** `videos`, `video_genres(N:M)`, `seasons`, `episodes`, `episode_files`, `rentals`, `resume_points`.
- **Reglas:** metadata async (TMDB key en Vault); acceso por suscripción/alquiler; signed URLs; sin path traversal.
- **Prioridad:** 🟢 F2/F3.

### 13) BillingService ⬜
- **Responsabilidad:** billing interno + integración OSS/PSP.
- **Endpoints:** `/billing/invoices`, `/billing/webhooks/{provider}`, `/billing/charge`.
- **Tablas:** `invoices`, `payments`, `billing_events`.
- **Reglas:** `BillingProvider` plug-in; webhooks **idempotentes**; retry/cola.
- **Prioridad:** 🟢 F3.

### 14) AuditLogService 🟡
- **Responsabilidad:** auditoría inmutable de acciones admin y eventos sensibles.
- **Endpoints:** `/api/admin/audit?actor&action&from`.
- **Tablas:** `audit_log` (append-only, particionada).
- **Reglas:** registrar **login admin** (faltaba en legacy), cambios de estado, emisión de tokens.
- **Prioridad:** 🟠 F2 (existe `audit` model; falta cobertura + inmutabilidad).

### 15) MonitoringService ✅🟡
- **Responsabilidad:** métricas, health, alertas, tracing.
- **Endpoints:** `/api/admin/metrics` ✅, `/sessions/live` ✅, `/nodes/health` ✅, `/streams` 🟡.
- **Stack:** Prometheus/Grafana/OTel ⬜; alertas de stream caído ⬜.
- **Prioridad:** 🟠 F2 (base existe; falta stack formal + alertas).

### 16) FlussonicIntegrationService ✅🟡
- **Responsabilidad:** abstracción de edges/DVR/catch-up y balanceo; **read-only**.
- **Endpoints:** interno `getEdge(zone)`, `getArchiveLink`, `getLoad`; admin `/api/admin/flussonic/*` ✅.
- **Tablas:** `streaming_nodes` 🟡(hoy en `.env`+`channel.flussonic_node`), `stream_zones` ⬜.
- **Reglas:** adapter por motor; selección por zona/carga; failover; **nunca** escribe en Flussonic.
- **Prioridad:** 🟠 F2 (cliente read-only existe; falta registry formal + failover).

### 17) AstraIntegrationService ⬜
- **Responsabilidad:** adapter para Astra (análogo a Flussonic).
- **Reglas:** misma interfaz `StreamProvider`; selección y failover entre motores.
- **Prioridad:** 🟢 F2/F3.

### 18) NotificationService ⬜
- **Responsabilidad:** mensajes/eventos push al device (cut_off/on, reboot, mensaje, update_epg) y notificaciones cliente.
- **Endpoints:** cola consumida por `DeviceService`; admin `/api/admin/devices/{id}/notify`.
- **Tablas:** `events(device_id, type, payload jsonb, status)`.
- **Prioridad:** 🟢 F2.

---

## Mapa de prioridad

| MVP (🔴) | Fase 2 (🟠) | Fase 3 (🟢) |
|---|---|---|
| Auth, Subscriber, Device, Subscription, PlaybackAuth, StreamToken, SessionConcurrency, ChannelCatalog | Admin/RBAC, Plan/paquetes, EPG, Audit, Monitoring, Flussonic registry, Notification | VOD, Billing, Astra, XtreamCompat, multi-región/CDN, apps nativas |

> **Restricción del proyecto:** Android TV/apps **no** se inician hasta que playback, sesiones, auth y observabilidad estén estables (alineado con el roadmap actual de Nexora Fase 4).
