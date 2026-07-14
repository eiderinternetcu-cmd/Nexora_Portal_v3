# Módulo 2 — PLAYBACK Authorization / Stream Token / Concurrencia (investigación profunda)

> Investigación defensiva (solo lectura) sobre Xtream 2.93 (**A**), R22F/CKMOD41 (**B**), Ministra (**C**), reconciliada con el **código real de `nexora_api`** y con **evidencia live de producción** (`nexoraplay.net`). Sin copiar código legacy, sin secretos/tokens completos, sin credenciales en URL, sin tocar Flussonic (read-only/producción).
> Estado Nexora: ✅ hecho · 🟡 parcial · ⬜ por construir. Diagnóstico operativo: [02_PLAYBACK_LIVETV_DIAGNOSTIC.md](02_PLAYBACK_LIVETV_DIAGNOSTIC.md). Resumen ejecutivo: [../06_FLUJO_PLAYBACK_FINAL.md](../06_FLUJO_PLAYBACK_FINAL.md).

---

## 0. Hallazgos críticos (lo esencial)

1. **El playback actual funciona técnicamente:** `authorize → playback_url HTTPS → manifest HLS 200` (verificado en producción, 1080p, same-origin, sin IP origen).
2. **Causa actual de "no se ven canales":** el **límite de 5 dispositivos bloqueaba el LOGIN completo con HTTP 400** → el usuario nuevo ni autenticaba. **Mitigado** liberando devices de diagnóstico; login desde equipo nuevo ahora 200.
3. **Decisión final:** **desacoplar login del device cap** — `/auth/login` **no** debe fallar por límite de dispositivos.
4. **`/devices/register`** debe devolver **409 Conflict** cuando se alcance el límite.
5. **`/playback/authorize`** debe exigir **device registrado y activo** (no bloqueado).
6. **Deuda crítica de seguridad:** la `playback_url` **todavía no lleva `?token=`** → el manifest queda **público** vía proxy (anti-hotlink no aplicado).
7. **Decisión anti-hotlink:** **Nginx `auth_request` → FastAPI** (`/api/stb/auth/validate`), **sin tocar Flussonic**.
8. **Prioridad siguiente:** implementar **token obligatorio para `/stream/*`**.

---

## 1. Resumen comparativo (legacy)

### 1.1 Extracción legacy de playback (FASE 1)

| # | Pregunta | Xtream A | R22F/CKMOD B | Ministra C |
|---|---|---|---|---|
| 1 | player_api.php | JSON user_info+listas [INFERIDO, ausente] | idem | n/a (usa load.php) |
| 2 | get.php (M3U) | M3U con **user/pass embebidos por canal** | idem | n/a |
| 3 | xmltv.php | EPG XMLTV filtrado por usuario | idem | `Epg` |
| 4 | MAG create_link | n/a (portal ausente) | `mag_security` | `Itv::createLink` (**IDOR**) |
| 5 | valida user/pass | en URL (texto/MD5) | idem | MD5 derivado |
| 6 | valida bouquet | `users.bouquet` (JSON) | idem | `getServicesByType` (no en createLink) |
| 7 | stream activo | `streams_sys.pid` | idem | `ch_links.status` |
| 8 | selección servidor | `streaming_servers` + `hash_lb` | idem | `StreamServer::getForLink` (menos cargado) |
| 9 | URL final | rewrite Nginx `/U/P/ID.ts`→`clients_live.php` | idem (+ext variable) | rama por proveedor (12 esquemas) |
| 10 | redirect/proxy/HLS | HLS desde tmpfs; proxy/redirect | idem | proxy nginx/redirect/token |
| 11 | user_activity | `user_activity_now` (INSERT) | idem | `played_itv` + `now_playing` |
| 12 | max_connections | `COUNT(user_activity_now)` [INFERIDO] | idem | por streamer (`getStreamerSessions`) |
| 13 | conexiones activas | `user_activity_now` | idem | `keep_alive>now-2*timeout` |
| 14 | liberar conexión | watchdog/timeout | idem | watchdog 120s |
| 15 | zapping | sin manejo especial | idem | now_playing cache 10s |
| 16 | VOD/series | `/movie|series/U/P/ID` | idem | `Vod`/`video_*` |
| 17 | streams caídos | `pid_monitor.php` | idem | `monitoring_url` |
| 18 | riesgos | creds en URL, COUNT no atómico, origen expuesto | idem | **IDOR**, MD5, secretos default, TTL 8h |

### 1.2 Matriz comparativa legacy ↔ Nexora (FASE 4)

| Tema | Xtream A | R22F B | **Nexora actual** | Mejor idea | Riesgo | Decisión Nexora final |
|---|---|---|---|---|---|---|
| 1 Player API | creds en URL | idem | Client API JWT ✅ | Nexora | creds en URL | mantener JWT |
| 2 get.php/M3U | creds embebidas | idem | ⬜ | — | exposición | M3U firmado opcional (XtreamCompat F3) |
| 3 MAG create_link | n/a | n/a | authorize central ✅ | Nexora | IDOR(C) | un solo authorize |
| 4 valida usuario | URL | URL | JWT client_access ✅ | Nexora | — | aud/type estricto |
| 5 valida suscripción | exp_date | idem | `_load_active_subscription` ✅ | A/C | — | mantener |
| 6 valida paquete/canal | bouquet JSON | idem | **no valida canal** 🟡 | C (entitlements) | acceso a no incluidos | `plan_channels`/`package_contents` |
| 7 valida canal | `streams_sys` | idem | `get_active_by_key` ✅ | Nexora | — | mantener |
| 8 stream URL gen | rewrite/creds | idem | `stream_hls_url`/`source_url` ✅(sin token) | Nexora | sin firma | firmar + auth_request |
| 9 token playback | — | — | JWT 60s ✅(no en URL) | Nexora | no aplicado en edge | poner en URL + validar |
| 10 expiración token | — | — | 60s ✅ | Nexora | — | mantener + renovación |
| 11 selección servidor | LB | idem | routing por `channel.flussonic_node` 🟡 | C | sin failover | registry + scheduling |
| 12 load balancer | BD compartida | idem | ⬜ | — | blast radius | métricas Redis |
| 13 concurrencia | COUNT(*) | idem | **Redis ZSET atómico** ✅ | Nexora | carrera (sin Lua) | envolver en Lua |
| 14 heartbeat | watchdog | idem | extend_connection ✅🟡 | Nexora | endpoint dedicado ⬜ | `/playback/heartbeat` |
| 15 timeout | watchdog | idem | TTL 180s ✅ | Nexora | — | mantener |
| 16 revocación suspensión | — | — | revoca sesiones+playback ✅ | Nexora | — | mantener |
| 17 zapping | — | — | reuso por device ✅ | Nexora | — | mantener |
| 18 logs reproducción | user_activity | idem | sesión IPTV PG 🟡 | C | sin analytics | `stream_access_logs` |
| 19 anti-hotlink | IP/UA débil | idem | **ninguno** 🔴 | — | hotlink | auth_request token |
| 20 mixed-content | — | — | **resuelto** ✅ | Nexora | — | mantener |
| 21 IP origen | expuesta | idem | **oculta** ✅ | Nexora | — | mantener |
| 22 Flussonic | n/a | n/a | cliente read-only ✅ | Nexora | — | registry |
| 23 Astra | n/a | n/a | ⬜ | — | — | adapter F2/F3 |
| 24 restreaming | fácil | idem | parcial (sin token) 🟡 | — | robo contenido | token+IP+TTL |
| 25 compat STB | MAG | idem | router /api/stb 🟡 | C | — | handshake (doc 07) |

---

## 2. Diagnóstico del problema actual (resumen)

Detalle completo + evidencia en [02_PLAYBACK_LIVETV_DIAGNOSTIC.md](02_PLAYBACK_LIVETV_DIAGNOSTIC.md). Síntesis:

- **Backend sano:** authorize 200 → `https://nexoraplay.net/stream/co-main/TeleNostalgia/index.m3u8` → manifest 200 `application/vnd.apple.mpegurl` (1080p).
- **Bloqueo real:** device cap (5) → `400` en login. **Acción ejecutada (evidencia):** liberados 2 devices de diagnóstico (`claude-check-001`, `claude-diag-001`) vía `DELETE /api/admin/devices/{id}` (auditado); conteo 5→3; **login desde equipo nuevo → 200**; authorize → 200. Devices reales `web-*` intactos.
- **Deuda:** `playback_url` sin `?token=` → manifest público.

---

## 3. Decisiones tomadas

1. **Login desacoplado del device cap:** `/api/client/auth/login` autentica aunque el device no quepa; devuelve tokens + flag `device_registration` (`registered`|`limit_reached`). Nunca `400/409` por device en login.
2. **`/api/client/devices/register` → 409 Conflict** al alcanzar el límite (semántica correcta; hoy `400`).
3. **`/api/client/playback/authorize` exige device registrado + activo** (no bloqueado, pertenece al suscriptor). (Ya valida pertenencia/bloqueo; añadir verificación explícita de "registrado".)
4. **Anti-hotlink vía Nginx `auth_request` → FastAPI** (`/api/stb/auth/validate`), sin configurar Flussonic.
5. **`playback_url` firmada:** `…/index.m3u8?token=<jwt_playback>`; token ligado a subscriber+device+channel+session+node+exp.
6. **Concurrencia atómica** (envolver ZSET en Lua) + heartbeat/stop endpoints dedicados.
7. **Entitlement por canal** (`plan_channels`) en authorize.
8. **Multi-nodo formal** (`stream_nodes`) + health + failover; **Astra** como segundo motor por adapter.
9. **Sin commit** hasta validar; toda evidencia documentada.

---

## 4. Arquitectura PlaybackService (12 submódulos · FASE 6)

| # | Submódulo | Responsabilidad | Endpoints | Tablas | Redis | Estado | Prio |
|---|---|---|---|---|---|---|---|
| 1 | **PlaybackAuthorizationService** | decidir si puede reproducir (sub+suscripción+plan_canal+device+concurrencia) ANTES de firmar | `POST /playback/authorize` | subscriptions, plan_channels, channels, devices | `playback:*`,`concurrency:*` | ✅🟡 (falta plan_channel) | 🔴 |
| 2 | **StreamTokenService** | emitir/validar token de playback (JWT corto, aud=nexora-playback) | interno + `/api/stb/auth/validate` | — | `playback:token:{jti}` | ✅🟡 (no en URL) | 🔴 |
| 3 | **PlaybackSessionService** | crear/cerrar/renovar sesión (PG+Redis) | authorize/heartbeat/stop | playback_sessions | `playback:session:*` | ✅🟡 | 🔴 |
| 4 | **ConcurrencyService** | límite por plan/sub/device, atómico | (interno) | concurrency_locks(opcional) | `concurrency:subscriber:*`,`active_conns:*` | ✅🟡 (Lua) | 🔴 |
| 5 | **StreamNodeSelectionService** | elegir nodo por zona/carga/salud | (interno) | stream_nodes, node_health_checks | `stream_node:health|load:*` | 🟡 | 🟠 |
| 6 | **FlussonicUrlSigner** | construir URL firmada Flussonic | (interno) | — | — | 🟡 | 🔴 |
| 7 | **AstraSourceResolver** | resolver origen Astra (adapter) | (interno) | astra_nodes | — | ⬜ | 🟢 |
| 8 | **HlsProxyService** | conceptual: contrato del proxy `/stream/*` (auth_request) | Nginx | — | `playback:token:*` | 🟡 | 🔴 |
| 9 | **PlaybackHeartbeatService** | mantener sesión viva, renovar token | `POST /playback/heartbeat` | playback_sessions | `active_conns:*` | 🟡 | 🟠 |
| 10 | **PlaybackAuditService** | log de reproducción/eventos (inmutable) | — | stream_access_logs, audit_logs | — | 🟡 | 🟠 |
| 11 | **PlaybackRevocationService** | revocar por jti/session/device/suscriptor | `/admin/.../revoke` | playback_revocations | `revoked:jti:*` | ✅🟡 | 🟠 |
| 12 | **PlaybackFallbackService** | si nodo falla, reautorizar a otro nodo | (interno) | stream_nodes | `stream_node:health:*` | ⬜ | 🟠 |

---

## 5. Endpoints finales (FASE 7)

### Cliente
**`POST /api/client/playback/authorize`** ✅🟡
- req `{channel_id:<channel_key>, device_id}` → res `{token, expires_in, channel_id, subscriber_id, playback_url}`
- 200 / 401(jwt) / 403(suscripción/plan/device) / 404(canal) / 409(concurrencia) / 429(rate)
- valida: aud/type/jti · subscriber active · suscripción vigente · **plan incluye canal** · canal activo · **device registrado+activo** · slot concurrencia
- tablas: subscriptions, plan_channels, channels, devices, playback_sessions · redis: `playback:token`,`concurrency:*` · logs: `playback.authorize.*`
- riesgo: si no exige token en edge, la URL es pública → **firmar**.

**`POST /api/client/playback/heartbeat`** 🟡 → `{session_id|token}` → renueva sesión+concurrencia; 200/401/410(expirada).
**`POST /api/client/playback/stop`** ⬜ → cierra sesión + libera slot; 204.
**`GET /api/client/playback/sessions`** 🟡 → sesiones activas del suscriptor.

### Admin
**`GET /api/admin/playback/sessions/live`** ✅ (`/api/admin/sessions/live`) → sesiones IPTV en vivo.
**`POST /api/admin/playback/sessions/{id}/revoke`** ✅🟡 → revoca sesión + cierra ZSET.
**`GET /api/admin/streams/health`** ✅ (`/api/admin/nodes/health`) → salud por nodo.
**`GET /api/admin/streams/nodes`** 🟡 → listar nodos (hoy `.env`).
**`POST /api/admin/streams/{id}/test`** ⬜ → probar stream (read-only get_stream_status).

### STB (futuro)
**`POST /api/stb/playback/create-link`** 🟡 (alias de authorize, aud=nexora-stb).
**`POST /api/stb/playback/heartbeat`** 🟡 · **`POST /api/stb/playback/stop`** ⬜.

---

## 6. Modelo PostgreSQL (FASE 8)

| Tabla | Campos clave | Índices/constraints | Sensibles | Retención | Legacy | Motivo |
|---|---|---|---|---|---|---|
| **channels** ✅ | id·channel_key UNIQUE·number·name·category·logo·stream_key(interno)·flussonic_node·hls_path·requires_subscription·censored⬜·is_active | uq(channel_key),idx(is_active,number) | stream_key | permanente | `streams`(A)/`itv`(C) | catálogo |
| **channel_categories** 🟡 | id·name·censored | uq(name) | — | permanente | `*_categories`/`tv_genre` | taxonomía |
| **stream_sources** ⬜ | id·channel_id FK·node_id FK·url·priority·ua_filter·status | idx(channel_id) | url(interno) | permanente | `ch_links`(C) | links físicos N:1 |
| **stream_nodes** 🟡(.env) | id·node_id UNIQUE·base_url·public_base_url·region·priority·is_healthy·max_sessions | uq(node_id) | — | permanente | `streaming_servers` | nodos (sin secretos) |
| **flussonic_nodes** 🟡 | (subtipo de stream_nodes con `engine=flussonic`) | — | creds→Vault | permanente | — | adapter Flussonic |
| **astra_nodes** ⬜ | (subtipo `engine=astra`) | — | creds→Vault | permanente | — | adapter Astra |
| **subscriptions** ✅ | id·subscriber_id·plan_id·starts_at·expires_at·is_active | idx(subscriber_id,is_active,expires_at) | — | permanente | exp_date/tariff | vigencia |
| **plans** ✅ | id·name·max_connections·max_devices·duration_days·price·is_active | — | — | permanente | bouquet/tariff | plan |
| **plan_channels** ⬜ | plan_id FK·channel_id FK | pk(plan_id,channel_id) | — | permanente | `users.bouquet`(JSON)/`service_in_package` | **entitlement por canal (FK real)** |
| **devices** ✅🟡 | id·subscriber_id·device_id UNIQUE·type·model·brand·os_version·mac·serial_hash·cert_fingerprint⬜·is_blocked·status⬜·last_seen | uq(device_id),idx(subscriber_id) | secret⬜ | permanente | mag_devices/users.mac | identidad |
| **playback_sessions** ✅🟡 | id·subscriber_id·device_id·channel_id·node_id·access_token_jti·ip·ua·started_at·expires_at·revoked_at·last_heartbeat_at | idx(subscriber_id,started_at)·**PARTITION**⬜ | ip | 90–180d | user_activity_now/played_* | sesión+auditoría |
| **playback_tokens** ⬜ | jti PK·session_id FK·token_hash·subscriber_id·device_id·channel_id·node_id·issued_at·expires_at·revoked_at | uq(token_hash) | token_hash | =exp+7d | — | auditoría/revocación de token (hash, no plano) |
| **stream_access_logs** ⬜ | id·subscriber_id·channel_id·node_id·action·at·ip | idx(channel_id,at)·**PARTITION** | ip | 30–90d | played_itv | analytics/forense |
| **concurrency_locks** ⬜(opcional) | subscriber_id·count·updated_at | pk(subscriber_id) | — | efímero(PG espejo) | user_activity_now | respaldo del ZSET |
| **node_health_checks** ⬜ | id·node_id FK·alive·latency_ms·stream_count·checked_at | idx(node_id,checked_at)·**PARTITION** | — | 30d | watchdog_data | salud histórica |
| **playback_revocations** ⬜ | id·target_type(jti/session/device/subscriber)·target_id·reason·actor_id·at | idx(target_id) | — | 1a | — | auditoría de revocación |

> **Sensibles:** `stream_key`/`stream_sources.url` jamás al cliente; tokens nunca en claro (solo `token_hash`); creds Flussonic/Astra en Vault.

---

## 7. Redis keys (FASE 9)

| Key | Propósito | TTL | Crea | Actualiza | Elimina | Si Redis cae / reconstrucción |
|---|---|---|---|---|---|---|
| `playback:token:{jti}` | token playback válido (allowlist) | 60s | authorize | — | uso/revocación/exp | edge rechaza (fail-closed); re-authorize. Reconstruible no (efímero) |
| `playback:session:{id}` | cache de sesión | =sesión | authorize | heartbeat | stop/revocación | fallback a `playback_sessions` (PG) |
| `playback:subscriber:{sub}:sessions` | set de sesiones del sub | =sesión | authorize | — | stop | desde PG `playback_sessions` |
| `playback:device:{dev}:sessions` | set por device | =sesión | authorize | — | stop | desde PG |
| `playback:channel:{ch}:viewers` | contador de viewers (métricas) | =sesión | authorize | heartbeat | stop/exp | aproximado; recalcular de PG |
| `concurrency:subscriber:{sub}` (=`active_conns:{sub}` ZSET) | slots concurrentes | 180s+60 | authorize | heartbeat | stop/exp | **clave**: si cae, authorize re-puebla; PG audita |
| `concurrency:device:{dev}` | slot por device | 180s | authorize | heartbeat | stop | re-puebla |
| `stream_node:health:{node}` | salud del nodo | 30–60s | health check | health check | exp | health check repuebla |
| `stream_node:load:{node}` | carga del nodo | 30s | métricas | métricas | exp | recalcular |
| `rate:playback_authorize:{sub}` | rate-limit por sub | ventana | request | incr | exp | laxo temporal |
| `rate:playback_authorize_ip:{ip}` | rate-limit por IP | ventana | request | incr | exp | laxo temporal |

> Hoy existen `nexora:active_conns:{sub}` (ZSET ✅), `nexora:playback:{jti}` ✅, `nexora:session:{jti}` ✅, `nexora:session_playbacks:{ses}` ✅. El resto son la evolución namespaced.

---

## 8. Algoritmo de concurrencia (FASE 11)

**Requisitos:** límite por plan/sub/device · zapping no duplica · heartbeat obligatorio · timeout configurable · stop explícito · Redis atómico · PG auditoría · tolerante a caída de Redis · libera si player cae · admin revoca · suspensión revoca todo.

**Algoritmo (authorize):**
```
key = concurrency:subscriber:{sub}   (ZSET member=device, score=exp)
LUA (atómico):
  1. ZREMRANGEBYSCORE key -inf now           # limpia expirados (caídas/zapping)
  2. score = ZSCORE key device
  3. if score == nil:
        if ZCARD key >= plan.max_connections: return LIMIT   # 409
  4. ZADD key {device: now+TTL}              # nuevo o renovado (zapping NO consume slot)
  5. EXPIRE key TTL+60
  return OK
→ crear/renovar playback_session (PG) + emitir token
```
- **Zapping:** mismo device renueva su score (no abre slot nuevo) → sin `409` falso. (Ya implementado en `ConnectionService.open_connection`; falta envolver pasos 1–4 en **Lua** para atomicidad total bajo carrera.)
- **Heartbeat:** `ZADD` renueva score; sin heartbeat el score vence → slot liberado (player caído).
- **Stop:** `ZREM` device.
- **Suspensión/cancelación:** vacía ZSET + revoca sesiones+playback tokens (✅ ya en cancel suscripción).
- **Límite por device:** 1 entrada por device en el ZSET; **por plan:** `plan.max_connections`; **por sub:** tamaño del ZSET.

**TTL:** 180s (heartbeat < TTL). **Lock:** script Lua (no `WATCH/MULTI` disperso).

**Casos borde / pruebas:** carrera N authorize (≤max), zapping 10x (0 falsos), timeout libera, reinicio Redis (re-puebla, PG audita), doble device max=1 (409), reloj de servidor (no cliente).

---

## 9. Reglas de signed URL / token (FASE 12)

1. **Token específico de playback** (no es access token).
2. **No reutilizable** fuera del stream autorizado (ligado a channel+node).
3. **TTL corto:** 30–90 s para el handshake inicial; la continuidad la da el **heartbeat** que renueva sesión/token.
4. **Sesión separada** con heartbeat para continuidad (no extender el token a horas).
5. **Token ligado a:** `subscriber_id · device_id · channel_id · session_id · stream_key(interno) · node_id · exp · jti` (+ `client_ip` para anti-hotlink).
6. **`aud=nexora-playback`, `type=playback_token`** — distinto del access; validación específica.
7. **No imprimir token completo** (enmascarar a `…últimos6`).
8. **No guardar token plano en DB** — solo `token_hash` (`playback_tokens`) si se audita.
9. **Revocable** por `jti`/`session` (allowlist `playback:token:{jti}` + `revoked:jti`).
10. **Validación en edge** vía `auth_request` (ver §10); IP del token debe coincidir con la del request.

**URL objetivo:** `https://nexoraplay.net/stream/<node>/<stream>/index.m3u8?token=<jwt_playback>`
**Hoy:** sin `?token=` → **deuda crítica** (manifest público).

---

## 10. Nginx /stream/* (FASE 13)

**Hoy** ([deploy/nginx/nexoraplay.conf](../../../deploy/nginx/nexoraplay.conf)): `location ^~ /stream/ec-main/` y `/stream/co-main/` → `proxy_pass` a `http://IP:8002/` con Range/If-Range, `proxy_buffering off`, `proxy_redirect off`. ✅ HTTPS same-origin, origen oculto, **sin validación de token**.

**Diseño objetivo (auth_request):**
```nginx
location ^~ /stream/ {
    auth_request /__playback_auth;                 # valida token contra FastAPI
    auth_request_set $au $upstream_http_x_auth;    # opcional propagar
    proxy_pass http://<upstream_por_nodo>/;
    proxy_set_header Host $proxy_host;
    proxy_set_header Range $http_range;             # Range para HLS
    proxy_set_header If-Range $http_if_range;
    proxy_buffering off; proxy_request_buffering off;
    proxy_read_timeout 60s; proxy_redirect off;
    # CORS controlado (no '*'): set por server
}
location = /__playback_auth {
    internal;
    proxy_pass http://nexora_api:8000/api/stb/auth/validate;
    proxy_pass_request_body off;
    proxy_set_header Content-Length "";
    proxy_set_header X-Original-URI $request_uri;   # incluye ?token=
    proxy_set_header X-Forwarded-For $remote_addr;  # IP real para IP-binding
}
```
**Checklist:** ✅ HTTPS obligatorio · ✅ sin mixed-content · ✅ sin IP origen · ⬜ CORS explícito (no `*`) · ⬜ logs sin token completo (ocultar query token en `log_format`) · ⬜ rate limit `/stream/` · ✅ Range · 302 Flussonic: `proxy_redirect off` mantiene HTTPS (la raíz `/stream/<node>/` da 302; el manifest da 200 — comportamiento esperado).

> **Validación corta cacheada:** para no llamar a FastAPI por cada segmento, cachear el resultado del token por `jti` en Redis con TTL corto y devolver 204 rápido; el segmento hereda la autorización del manifest.

---

## 11. Integración Flussonic / Astra

- **Flussonic** (✅ read-only): `FlussonicClient` con `_WriteBlocker`; multi-nodo (ec-main, co-main) por `get_flussonic_node_client`. `stream_hls_url(base, stream, hls_path)`. **Nunca** se configura Flussonic desde Nexora → por eso anti-hotlink va en **Nexora nginx**, no en Flussonic.
- **Astra** (⬜): adapter `AstraSourceResolver` con la misma interfaz `StreamProvider` (resolver URL + firma propia si aplica). Selección por `stream_nodes.engine`.
- **Selección/failover:** `StreamNodeSelectionService` elige por zona/carga/salud; `PlaybackFallbackService` reautoriza a otro nodo si el health check marca caído.

---

## 12. Riesgos legacy descartados (FASE 5)

### 🔴 Crítico
| Área | Hallazgo | Evidencia | Mitigación Nexora | Tarea |
|---|---|---|---|---|
| Autorización | **IDOR createLink** (C) | `Itv.php:130` | authorize central ✅ | PLAYBACK-001 |
| URL | creds en URL/M3U (A) | get.php | JWT + signed URL ✅ | PLAYBACK-010 |
| Anti-hotlink | **playback_url sin token** (Nexora hoy) | manifest público | auth_request token | PLAYBACK-011..014 |
| Tokens | MD5/secretos default (C) | `nginx_secure_link_secret=supersecret` | HMAC/JWT + Vault | PLAYBACK-012 |

### 🟠 Alto
| Hallazgo | Mitigación | Tarea |
|---|---|---|
| Concurrencia `COUNT(*)` no atómica (A) | ZSET+Lua ✅🟡 | PLAYBACK-020 |
| Sin IP-binding (C, TTL 8h) | token IP-bound, TTL corto | PLAYBACK-012 |
| Device cap bloquea login (Nexora) | desacoplar + 409 | PLAYBACK-005 |
| Sin failover de nodo | registry+health+fallback | PLAYBACK-024 |
| Origen `rtp/udp` sin token (C) | todo tras edge firmado | PLAYBACK-011 |

### 🟡 Medio
| Hallazgo | Mitigación |
|---|---|
| CORS `*` en edge | CORS explícito en `/stream/` |
| Sin entitlement por canal (Nexora) | `plan_channels` |
| Logs con token en URL | ocultar query token en logs |
| Sin heartbeat/stop dedicados | endpoints `/playback/heartbeat|stop` |

### 🔵 Bajo
| Hallazgo | Mitigación |
|---|---|
| `var_dump`/trazas (C) | logs estructurados |
| Enumeración IDs | UUID/channel_key ✅ |

---

## 13. Backlog Codex (FASE 15) — PLAYBACK-001..030

> Formato compacto: título · archivos · tablas/endpoints · AC · rollback.

| ID | Título | Archivos / Endpoints | AC · Rollback |
|---|---|---|---|
| PB-001 | Authorize central como único emisor de URL | `stream_auth_service.py` | AC: ninguna URL sin authorize. RB: n/a |
| PB-002 | Validar aud/iss/type/jti en authorize | `dependencies.py` | AC: token mal aud→401 |
| PB-003 | Exigir suscripción vigente | `_load_active_subscription` | AC: vencida→403 ✅ |
| PB-004 | Entitlement por canal (`plan_channels`) | models+Alembic, authorize | AC: canal fuera de plan→403. RB: drop tabla |
| PB-005 | Desacoplar device cap del login | `client_auth_service.py` | AC: login 200 con cap lleno + flag. RB: revertir |
| PB-006 | `/devices/register`→409 al límite | `device_service.py`,`profile.py` | AC: 409 con cap lleno |
| PB-007 | Authorize exige device registrado+activo | `stream_auth_service.py` | AC: device no registrado→403 |
| PB-010 | Firmar `playback_url` con token | `playback.py`,`stream_auth_service.py` | AC: URL lleva `?token=`. RB: flag legacy sin token |
| PB-011 | Nginx auth_request `/stream/*` | `nexoraplay.conf` | AC: GET sin token→401; con token→200. RB: quitar auth_request |
| PB-012 | Token IP-bound + TTL corto | StreamTokenService | AC: otra IP→401 |
| PB-013 | Cache de validación por jti (Redis) | validate | AC: no penaliza segmentos |
| PB-014 | Ocultar token en logs Nginx/app | log_format/logging | AC: grep sin token |
| PB-020 | Concurrencia en Lua (atómica) | `connection_service.py` | AC: carrera≤max. RB: versión actual |
| PB-021 | `/playback/heartbeat` dedicado | `playback.py` | AC: renueva sesión |
| PB-022 | `/playback/stop` libera slot | `playback.py` | AC: ZREM + revoca token |
| PB-023 | `stream_nodes` formal (de .env a DB) | models+Alembic | AC: nodos en DB |
| PB-024 | Selección+failover de nodo | StreamNodeSelectionService | AC: nodo caído→otro nodo |
| PB-025 | Health checks → `node_health_checks` | monitoring | AC: salud histórica |
| PB-026 | `playback_sessions` particionada | Alembic | AC: partición por mes |
| PB-027 | `stream_access_logs` analytics | models | AC: log por reproducción |
| PB-028 | `playback_tokens` (hash) + revocación | models | AC: revocar por jti |
| PB-029 | Adapter Astra | stream_integration | AC: canal Astra reproduce |
| PB-030 | CORS explícito en `/stream/` | nexoraplay.conf | AC: sin `*` |

---

## 14. Plan de pruebas (FASE 14) — 28 casos

| # | Caso | Esperado |
|---|---|---|
| 1 | Login OK + authorize OK | 200 + playback_url |
| 2 | Usuario vencido | authorize 403 |
| 3 | Usuario suspendido | authorize 403 |
| 4 | Device bloqueado | authorize 403 |
| 5 | Canal inactivo | 404 |
| 6 | Plan sin canal | 403 |
| 7 | Token expirado | 401 |
| 8 | Max connections | 409 |
| 9 | Zapping rápido | no duplica sesión |
| 10 | Stop libera sesión | slot liberado |
| 11 | Heartbeat mantiene | sesión viva |
| 12 | Sin heartbeat | expira por TTL |
| 13 | Suspensión revoca activa | sesión cortada |
| 14 | playback_url HTTPS | ✓ same-origin |
| 15 | Sin mixed-content | ✓ |
| 16 | Sin IP origen | ✓ |
| 17 | Manifest .m3u8 | 200 |
| 18 | Segmentos | 200 (con token) |
| 19 | Flussonic caído | fallback/error claro |
| 20 | Logs sin token | ✓ |
| 21 | Player usa playback_url | ✓ (no URL manual) |
| 22 | Nginx /stream/* | funciona |
| 23 | Redis caída | authorize re-puebla |
| 24 | Reinicio backend | sesiones PG persisten |
| 25 | 10 usuarios simultáneos | concurrencia correcta |
| 26 | Reconexión player | hls.js recupera |
| 27 | Red lenta | buffering, no corte |
| 28 | Android TV (conceptual) | mismo authorize |
| + | **Sin token en /stream/** | **401** (tras PB-011) |
| + | **Login con cap lleno** | **200 + flag** (tras PB-005) |

---

## 15. Checklist de aceptación

- [x] authorize → playback_url HTTPS → manifest 200 (verificado en prod)
- [x] playback_url same-origin, sin IP origen, sin mixed-content
- [ ] Login NO falla por device cap (devuelve tokens + flag)
- [ ] `/devices/register` → 409 al límite
- [ ] authorize exige device registrado+activo
- [ ] **playback_url firmada (`?token=`)** + `/stream/*` valida vía auth_request
- [ ] token IP-bound + TTL corto + no reutilizable
- [ ] entitlement por canal (`plan_channels`)
- [ ] concurrencia atómica (Lua) + heartbeat/stop
- [ ] suspensión/bloqueo revoca sesiones+tokens
- [ ] multi-nodo + failover; Astra por adapter
- [ ] logs sin token; CORS explícito en `/stream/`
- [ ] pruebas 1–28 verdes
