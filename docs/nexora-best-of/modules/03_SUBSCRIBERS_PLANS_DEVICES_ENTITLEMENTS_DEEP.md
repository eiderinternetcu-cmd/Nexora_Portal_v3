# Módulo 3 — Subscribers / Plans / Devices / Entitlements (investigación profunda)

> Investigación defensiva (solo lectura del legacy) reconciliada con el **código real de `nexora_api`**. Sin copiar código legacy, sin credenciales en URL, sin secretos. Estado Nexora: ✅ hecho · 🟡 parcial · ⬜ por construir.
> Decisiones aprobadas: **plan_channels directo (MVP)** · **status enum en subscriptions** · entrega **por lotes**.
> **Estado del documento:** completo (§1–§12). Lote 1 (§1–§9) aprobado; Lote 2 (§10 backlog ENTITLEMENT-001..030, §11 pruebas, §12 checklist) añadido. Resumen ejecutivo: [../07_SUBSCRIBERS_PLANS_DEVICES_ENTITLEMENTS_FINAL.md](../07_SUBSCRIBERS_PLANS_DEVICES_ENTITLEMENTS_FINAL.md).

---

## 1. Resumen comparativo

### 1.1 Extracción legacy (FASE 1)

| # | Pregunta | Xtream 2.93 (A) | R22F/CKMOD41 (B) | Ministra (C) |
|---|---|---|---|---|
| 1 | Usuarios/líneas | `users` (línea = cuenta+credencial+límites) | idem | `users` (cuenta STB = 1 MAC) |
| 2 | Paquetes/bouquets | `bouquets` (JSON de stream ids en `users.bouquet`) | idem | `services_package`+`service_in_package` |
| 3 | Canales→paquete | JSON ids (sin FK) | idem | `service_in_package.service_id` (varchar→id) |
| 4 | Vencimiento | `users.exp_date` (epoch) | idem | `tariff_expired_date`/`expire_billing_date` |
| 5 | Status | `enabled` + `admin_enabled` (2 flags) | idem | `users.status` **invertido** (0=activo) |
| 6 | max_connections | `users.max_connections` (COUNT activity) | idem | por streamer (`getStreamerSessions`) |
| 7 | device limits | implícito (MAG = 1 línea) | idem | 1 cuenta = 1 MAC; multi vía `stb_groups` |
| 8 | MAG/STB devices | `mag_devices.user_id` | `mag_security` | `users.mac` UNIQUE + `auto_add_stb` |
| 9 | ¿puede ver canal? | streams ∩ `users.bouquet` | idem | `getServicesByType('tv')` (NO en createLink → IDOR) |
| 10 | actualizar plan | editar `users.bouquet`/`exp_date` | idem | `subscribeToPackage`/`unsubscribeFromPackage` |
| 11 | actividad | `user_activity_now` | idem | `played_itv` + `keep_alive` |
| 12 | tablas | `users`,`bouquets`,`streams`,`mag_devices` | idem | `users`,`tariff_plan`,`*package*`,`itv_subscription` |
| 13 | problemas | bouquet JSON sin FK, 2 flags de estado, creds en línea | idem | status invertido, CSV `sub_ch`, FK string |
| 14 | ideas útiles | separación línea/bouquet; exp_date | despliegue | tarifa→paquete→servicio; suscripción opcional |
| 15 | descartar | JSON sin FK, flags duales, creds en URL | idem | status invertido, CSV, MD5, MAC sola |

### 1.2 Matriz comparativa legacy ↔ Nexora (FASE 3)

| Tema | Xtream A | R22F B | **Nexora actual** | Mejor idea | Riesgo | Decisión Nexora |
|---|---|---|---|---|---|---|
| 1 Subscriber model | `users` línea | idem | `subscribers` ✅ | Nexora | — | mantener |
| 2 Line/account | mezclado | idem | subscriber + devices separados ✅ | Nexora | — | mantener |
| 3 Status | 2 flags | idem | enum `active/expired/suspended/banned` ✅ | Nexora | — | + history |
| 4 Credentials | en línea | idem | `password_hash` en `subscribers` 🟡 | C(separable) | rotación | `subscriber_credentials` ⬜ |
| 5 Plan/tariff | bouquet/exp | idem | `plans` (max_conn/devices) ✅ | Nexora | — | mantener |
| 6 Bouquet | JSON ids | idem | **no hay** 🟡 | C(paquetes) | sin FK | **`plan_channels`** ⬜ |
| 7 Channel entitlement | streams∩bouquet | idem | **no valida canal** 🔴 | C | acceso total | `plan_channels` + authorize |
| 8 Subscription range | exp_date | idem | `starts_at/expires_at` ✅ | Nexora | — | + `status` enum |
| 9 Renewal | editar exp | idem | `renew()` (extiende) ✅ | Nexora | sin historial | `subscription_history` ⬜ |
| 10 Cancellation | disable | idem | `cancel()` revoca sesiones ✅ | Nexora | sin historial | history + audit |
| 11 Suspension | admin_enabled | idem | status=suspended ✅🟡 | Nexora | revocar playback | revocar al suspender |
| 12 Device registration | mag CRUD | idem | auto-register en login 🟡 | — | acoplado al login | **desacoplar** |
| 13 Device limit | implícito | idem | `plan.max_devices` (400, bloquea login) 🟡 | Nexora | 400 + bloqueo | **409 + desacoplar** |
| 14 Max connections | COUNT | idem | ZSET atómico (`plan.max_connections`) ✅ | Nexora | — | mantener (≠ device limit) |
| 15 MAG/STB binding | MAC→user | `mag_security` | device→subscriber ✅🟡 | C | MAC sola | device_secret (Mód.1) |
| 16 Web/mobile devices | n/a | n/a | `device_type` (web/android/ios/mag) ✅ | Nexora | — | mantener |
| 17 Admin ops | panel | idem | admin API (subscribers/subscriptions) ✅🟡 | Nexora | — | + plans/channels |
| 18 Audit logs | disperso | `/root/creds.txt` | `audit_logs` ✅🟡 | Nexora | cobertura | + history tables |
| 19 Playback integration | bouquet en player_api | idem | authorize (sin plan_channel) 🟡 | Nexora | entitlement | EntitlementService |
| 20 Billing future | externo | idem | ⬜ | C(OSS) | — | BillingProvider (F3) |

---

## 2. Estado actual Nexora (FASE 2)

| Pieza | Realidad | Estado |
|---|---|---|
| `subscribers` | id·username·**password_hash**·activation_code·email·phone·full_name·id_cedula·**status enum(active/expired/suspended/banned)**·notes·created_by | ✅ |
| `subscriber_credentials` | **no existe** (hash en `subscribers`) | ⬜ |
| `plans` | id·name·**max_connections**·**max_devices**·duration_days·price·**is_active** | ✅ |
| `subscriptions` | id·subscriber_id·plan_id·starts_at·**expires_at**·**is_active**(bool)·created_by·renewal_note | ✅ (sin `status` enum) |
| `devices` | id·subscriber_id·device_id UNIQUE·mac·android_id·fingerprint·serial_hash·model·brand·device_type·**is_blocked**·block_reason·last_seen | ✅ (sin `status` enum) |
| `channels` | id·channel_key·number·name·category·stream_key·flussonic_node·hls_path·requires_subscription·is_active | ✅ |
| `plan_channels` | **no existe** | ⬜ |
| Validación suscripción | `_load_active_subscription`: `is_active=True AND expires_at>now` (join plan) | ✅ |
| Validación plan | usa `plan.max_connections`; **no** valida canal∈plan | 🟡 |
| Device cap | `DeviceService.register` vs `plan.max_devices` → **400 bloquea login** | 🟡 |
| max_connections | `ConnectionService.open_connection(..., plan.max_connections)` ZSET | ✅ |
| Playback↔plan | authorize carga suscripción+plan; **no** entitlement por canal | 🟡 |
| Endpoints admin | `/api/admin/subscribers/{id}/subscriptions` (create/renew/cancel), `/api/v1/plans` CRUD, `/api/admin/devices/*` (list/block/unblock/delete) | ✅🟡 |
| Endpoints client | `/api/client/profile`, `/profile/devices` (list/register/heartbeat) | ✅🟡 |
| Migraciones | 001 initial · 002 sessions · 003 channels · 004 channel_flussonic_node | ✅ |
| Seed | plan anual creado por API (incidente); 24 canales por `import_m3u_channels.py` | ✅ |
| Deuda | plan_channels, subscription.status, device.status, credentials, history tables, entitlement cache | ⬜ |

**Falta para entitlement por canal:** `plan_channels` + que `PlaybackAuthorizationService` lo consulte. Es el bloqueante directo del pendiente de Módulo 2.

---

## 3. Decisiones tomadas

1. **`plan_channels(plan_id, channel_id)` directo** como entitlement MVP (FK real). `packages/package_contents` queda como evolución Fase 2 (con ruta de migración).
2. **`subscriptions.status` enum** (`active/expired/cancelled/suspended`) + mantener `expires_at` (la `is_active` actual se deriva/migra). Distingue cancelada vs vencida; evita filas vencidas con `is_active=true`.
3. **`devices.status` enum** (`active/blocked/pending`) además de `is_blocked` (compat); `pending` habilita activación STB (Módulo 1).
4. **Login desacoplado del device cap** (Módulo 2): login no falla por límite; `/devices/register` → **409**.
5. **`subscriber_credentials`** como tabla destino (rotación/historial); MVP puede seguir con `password_hash` en `subscribers` y migrar.
6. **Tablas de historial**: `subscriber_status_history`, `subscription_history`, `device_blocks`, `device_history` (no se borran historiales críticos).
7. **EntitlementService** centraliza `can_watch_channel(...)` con **reason codes**; consultable con baja latencia (caché Redis opcional).
8. **max_devices ≠ max_connections**: registro vs concurrencia; nunca mezclar.

---

## 4. Modelo de datos final (FASE 6)

> Prioridad: 🔴 MVP · 🟠 F2 · 🟢 F3. Migración Alembic 005+ para lo nuevo.

| Tabla | Campos clave | Índices/constraints | Sensibles | Retención | Legacy | Prio |
|---|---|---|---|---|---|---|
| **subscribers** ✅ | id·username UNIQUE·full_name·email·phone·id_cedula·status enum·created_by·created_at | uq(username),idx(status,email,id_cedula) | — | permanente | `users`(A)/`users`(C) | 🔴 |
| **subscriber_credentials** ⬜ | subscriber_id FK PK·password_hash·updated_at·rotated_at | pk(subscriber_id) | password_hash | permanente | `users.password` | 🟠 |
| **subscriber_status_history** ⬜ | id·subscriber_id FK·old_status·new_status·reason·actor_id·at | idx(subscriber_id,at) | — | 1–2a | — | 🟠 |
| **plans** ✅ | id·name UNIQUE·max_connections·max_devices·duration_days·price·is_active | uq(name) | — | permanente | `bouquet`/`tariff_plan` | 🔴 |
| **subscriptions** ✅🟡 | id·subscriber_id FK·plan_id FK·starts_at·expires_at·**status enum(active/expired/cancelled/suspended)**·created_by·renewal_note | idx(subscriber_id,status,expires_at) | — | permanente | `exp_date`/`tariff_*` | 🔴 |
| **subscription_history** ⬜ | id·subscription_id FK·event(create/renew/cancel/change_plan)·old_plan_id·new_plan_id·old_expires·new_expires·actor_id·at | idx(subscription_id,at) | — | 1–2a | — | 🟠 |
| **plan_channels** ⬜ | plan_id FK·channel_id FK | **pk(plan_id,channel_id)**, idx(channel_id) | — | permanente | `users.bouquet`/`service_in_package` | 🔴 |
| **plan_vod_categories** ⬜ | plan_id FK·vod_category_id FK | pk(plan_id,vod_category_id) | — | permanente | — | 🟢 |
| **plan_series_categories** ⬜ | plan_id FK·series_category_id FK | pk(plan_id,series_category_id) | — | permanente | — | 🟢 |
| **devices** ✅🟡 | id·subscriber_id FK·device_id UNIQUE·mac·android_id·fingerprint·serial_hash·model·brand·device_type·**status enum(active/blocked/pending)**·is_blocked(compat)·block_reason·last_seen·registered_at | uq(device_id),idx(subscriber_id,status) | serial_hash | permanente | `mag_devices`/`users.mac` | 🔴 |
| **device_sessions** ⬜ | id·device_id FK·subscriber_id FK·jti·issued_at·expires_at·revoked_at·ip·ua | uq(jti),idx(subscriber_id) | — | 90d | — | 🟠 |
| **device_blocks** ⬜ | id·device_id FK·reason·blocked_by·blocked_at·**unblocked_at(NULL)**·unblocked_by | idx(device_id) | — | 1a | — | 🟠 (bloqueo reversible) |
| **device_history** ⬜ | id·device_id FK·event(register/block/unblock/delete/rebind)·detail jsonb·actor·at | idx(device_id,at) | — | 1a | — | 🟠 |
| **channel_categories** 🟡 | id·name·censored·sort | uq(name) | — | permanente | `tv_genre`/`*_categories` | 🟠 |
| **channels** ✅ | (ver Módulo 2) channel_key·stream_key(interno)·flussonic_node·is_active·requires_subscription·censored⬜ | uq(channel_key),idx(is_active) | stream_key | permanente | `streams`/`itv` | 🔴 |
| **audit_logs** ✅🟡 | id·actor_type·actor_id·action·target_type·target_id·details jsonb·ip·at | idx(actor_id,at),idx(action,at)·append-only·PARTITION | — | 1–2a | disperso | 🟠 |

**Reglas mínimas cumplidas:** `subscriptions(starts_at, expires_at, status)` ✅ · `plans(max_devices, max_connections, is_active)` ✅ · `plan_channels(plan_id, channel_id)` ✅ · `devices.status` ✅ · `device_blocks` reversible ✅ · `subscriber_status_history` ✅ · `subscription_history` ✅ · **entitlement por canal eficiente**: `pk(plan_id,channel_id)` + idx(channel_id) → lookup O(log n).

**Consulta de entitlement (índice clave):**
```sql
-- ¿el suscriptor puede ver el canal?  (1 query indexada)
SELECT 1
FROM subscriptions s
JOIN plans p        ON p.id = s.plan_id AND p.is_active
JOIN plan_channels pc ON pc.plan_id = s.plan_id
WHERE s.subscriber_id = :sub
  AND s.status = 'active' AND s.expires_at > now()
  AND pc.channel_id = :channel
LIMIT 1;
```

---

## 5. Endpoints finales (FASE 7)

### Admin — Subscribers
`GET /api/admin/subscribers` (list/filtro/paginado) · `POST` (crear) · `GET /{id}` · `PATCH /{id}` ·
`POST /{id}/suspend` (status=suspended + revoca sesiones) · `POST /{id}/activate` · `POST /{id}/disable` (banned).
- 200/201/403/404/409(username dup)/422 · tablas: subscribers, subscriber_status_history · logs: `subscriber.*`.

### Admin — Plans
`GET /api/admin/plans` · `POST` · `GET /{id}` · `PATCH /{id}` ·
**`POST /{id}/channels`** `{channel_ids:[...]}` → añade a `plan_channels` ·
**`DELETE /{id}/channels/{channel_id}`** → quita.
- AC: cambiar plan_channels **invalida caché de entitlement** + afecta playback inmediato. logs: `plan.channels.add/remove`.

### Admin — Subscriptions
`GET /api/admin/subscriptions` · `POST` (crear) · `POST /{id}/renew` · `POST /{id}/cancel` (revoca playback) · **`POST /{id}/change-plan`** `{plan_id}` (cambia plan + invalida entitlement + audit).
- tablas: subscriptions, subscription_history · logs: `subscription.*`.

### Admin — Devices
`GET /api/admin/subscribers/{id}/devices` · `POST /api/admin/devices/{id}/block` (+`device_blocks`) · `POST /{id}/unblock` (cierra `device_blocks.unblocked_at`) · `DELETE /api/admin/devices/{id}` (revoca sesiones del device primero).

### Cliente
`GET /api/client/profile` ✅ · `GET /api/client/subscription` (vigencia/plan) · `GET /api/client/devices` ✅ ·
`POST /api/client/devices/register` → **409** si `max_devices` alcanzado ·
`POST /api/client/devices/heartbeat` ✅ · `POST /api/client/devices/{id}/logout` · `DELETE /api/client/devices/{id}` (auto-gestión; revoca sus sesiones).

### Interno — Playback
**`EntitlementService.can_watch_channel(subscriber_id, device_id, channel_id) → {allow: bool, reason_code}`**
(opcional HTTP interno `POST /internal/entitlements/check-channel`, solo red interna/mTLS, nunca público).

---

## 6. Reglas de negocio (FASE 8)

1. **Login NO falla por device cap** → autentica + devuelve tokens + flag `device_registration`.
2. **`/devices/register` → 409** si `max_devices` alcanzado.
3. **Playback exige device registrado y activo** (status=active, no blocked, del suscriptor).
4. **Playback exige subscription activa** (`status=active AND expires_at>now`).
5. **Playback exige plan activo** (`plans.is_active`).
6. **Playback exige `plan_channels`** (canal ∈ plan).
7. **suspended → no reproduce** (403).
8. **expired → no reproduce** (403).
9. **banned/disabled → no loguea** (403 en login).
10. **Cambio de plan afecta entitlement inmediato** (invalida caché).
11. **Renovación**: si vigente extiende desde `expires_at`; si vencida desde `now`; status→active.
12. **Cancelación revoca playback activo** (sesiones + tokens).
13. **Suspensión revoca playback activo**.
14. **Bloqueo de device revoca sesiones de ese device**.
15. **max_devices ≠ max_connections** (registro vs concurrencia).
16. **max_connections se valida en Playback/Concurrency** (ZSET), no en registro.
17. **Device se elimina solo si no tiene playback activo** (o se revoca primero).
18. **Todo cambio admin → `audit_logs`**.
19. **No se borran historiales críticos**; se marca estado / se historiza.
20. **Entitlement consultable con baja latencia** (índice + caché Redis).

---

## 7. Integración con Playback (FASE 9)

`PlaybackAuthorizationService` delega la decisión a `EntitlementService.can_watch_channel`:

```
authorize(jwt, device_id, channel_id):
  sub = subscriber_from_jwt(jwt)                 # aud/type/jti ya validados (Mód.1)
  verdict = EntitlementService.can_watch_channel(sub.id, device_id, channel_id)
  if not verdict.allow: return 403 {error: verdict.reason_code}
  # allow → continuar con ConcurrencyService + StreamTokenService (Mód.2)
  slot = ConcurrencyService.open(sub, device, plan.max_connections)  # 409 si lleno
  token + playback_url firmada
  audit_log(playback.authorize, reason=verdict.reason_code or "ALLOW")
```

**`can_watch_channel` (orden de checks → primer fallo gana):**
1. subscriber.status → `SUBSCRIBER_SUSPENDED` / `SUBSCRIBER_DISABLED`(banned)
2. subscription activa → `SUBSCRIPTION_NOT_FOUND` / `SUBSCRIPTION_EXPIRED`
3. plan activo → `PLAN_INACTIVE`
4. canal activo → `CHANNEL_INACTIVE`
5. canal ∈ plan_channels → `CHANNEL_NOT_INCLUDED`
6. device registrado/activo → `DEVICE_NOT_REGISTERED` / `DEVICE_BLOCKED`
7. (concurrencia se evalúa después, en Playback) → `DEVICE_LIMIT_REACHED` se usa solo en register; concurrencia → 409
→ `{allow:true}` si pasa todo.

**Reason codes:** `SUBSCRIBER_SUSPENDED · SUBSCRIBER_DISABLED · SUBSCRIPTION_EXPIRED · SUBSCRIPTION_NOT_FOUND · PLAN_INACTIVE · CHANNEL_NOT_INCLUDED · CHANNEL_INACTIVE · DEVICE_NOT_REGISTERED · DEVICE_BLOCKED · DEVICE_LIMIT_REACHED`.

> **Si deny:** Playback responde **403** con el reason_code (sin firmar URL → no toca Flussonic). **Si allow:** sigue el flujo del Módulo 2 (concurrencia + token + signed URL).

---

## 8. Redis / Cache (FASE 10)

| Key | Propósito | TTL | Invalidación | Stale risk | Si Redis cae |
|---|---|---|---|---|---|
| `entitlement:subscriber:{sub}:active_subscription` | suscripción+plan vigente cacheada | 60–300s | al renovar/cancelar/cambiar plan/suspender | medio (acotar TTL) | leer de PG (fuente de verdad) |
| `entitlement:plan:{plan}:channels` | set de channel_ids del plan | 300–600s | al editar `plan_channels` | medio | leer de PG |
| `device:subscriber:{sub}:count` | conteo de devices | 300s | register/delete/block | bajo | `COUNT` en PG |
| `device:{device}:status` | estado del device | 60–120s | block/unblock/delete | medio | leer de PG |

**Estrategia de invalidación:** toda mutación admin (cambiar plan, plan_channels, suspender, bloquear) **publica** invalidación de las keys afectadas (o las borra) → entitlement fresco. **Regla:** la caché es optimización; **PG es la verdad**. Si Redis cae, `EntitlementService` consulta PG directo (la query indexada de §4 es barata). Nunca permitir acceso por caché stale tras una suspensión/cancelación → en esos eventos **borrar** la key (no solo expirar).

---

## 9. Riesgos legacy descartados (FASE 4)

### 🔴 Crítico
| Área | Hallazgo | Evidencia | Mitigación | Tarea |
|---|---|---|---|---|
| Entitlement | **Playback sin `plan_channels`** (suscriptor ve todos los canales) | authorize no valida canal∈plan | `plan_channels` + EntitlementService | ENT-001/010 |
| Estado | usuario activo **sin suscripción válida** podía intentar playback | mitigado: authorize 403 ✅; reforzar | status enum + checks | ENT-004 |
| Identidad | bouquet/CSV sin FK (A/C) | JSON/CSV | FK reales `plan_channels` | ENT-010 |

### 🟠 Alto
| Hallazgo | Mitigación | Tarea |
|---|---|---|
| Login bloqueado por device cap (400) | desacoplar + 409 | ENT-020 |
| max_connections mezclado con device limit (legacy) | separados ✅; documentar | ENT-021 |
| expired/suspended mal diferenciados (C invertido) | enum explícito ✅ + history | ENT-005 |
| Renovaciones/cambios sin historial | `subscription_history` | ENT-006 |
| Eliminación de devices sin registro | `device_history`+revocar sesiones | ENT-024 |
| Suspensión sin revocar playback | revocar al suspender | ENT-007 |

### 🟡 Medio
| Hallazgo | Mitigación |
|---|---|
| Credenciales mezcladas con perfil | `subscriber_credentials` |
| Falta de índice en consulta de playback | `pk(plan_id,channel_id)`+idx(channel_id) |
| Plan sin canales asignados | validar/avisar en admin; authorize→CHANNEL_NOT_INCLUDED |
| Subscription `is_active=true` vencida (stale) | status enum + job de expiración |

### 🔵 Bajo
| Hallazgo | Mitigación |
|---|---|
| Falta integración billing | `BillingProvider` (F3) |
| Sin retención de historiales | particionar + retención |

---

## 10. Backlog Codex (FASE 12) — ENTITLEMENT-001..030

> Formato por ítem: descripción · archivos · tablas · endpoints · redis · validaciones · tests · AC · riesgo · rollback. Prio: 🔴 MVP · 🟠 F2 · 🟢 F3.

### 🔴 MVP — Entitlement core (orden de implementación)

**ENT-001 — Migración `plan_channels`**
- Desc: crear tabla de unión plan↔canal. · Archivos: `migrations/versions/005_plan_channels.py` · Tablas: `plan_channels(plan_id,channel_id)` PK compuesta, FK ON DELETE CASCADE, idx(channel_id) · Endpoints: — · Redis: — · Validaciones: FK válidas · Tests: up/down de migración · AC: tabla creada con PK(plan_id,channel_id)+idx(channel_id) · Riesgo: bajo · Rollback: `downgrade()` drop tabla.

**ENT-002 — Modelo SQLAlchemy `PlanChannel`**
- Desc: mapear la tabla + relationships. · Archivos: `app/models/plan_channel.py`, `plan.py` (rel), `channel.py` (rel) · Tablas: plan_channels · Tests: insert/select; cascade al borrar plan/canal · AC: `Plan.channels` y `Channel.plans` navegables · Riesgo: bajo · Rollback: revertir modelo.

**ENT-003 — `EntitlementService.can_watch_channel`**
- Desc: servicio central con la query indexada de §4 y reason codes (§7). · Archivos: `app/services/entitlement_service.py` · Tablas: subscriptions, plans, plan_channels, channels, devices · Endpoints: — (interno) · Redis: opcional (ENT-008) · Validaciones: orden de checks subscriber→subscription→plan→channel→plan_channels→device · Tests: ver §11.1–10 · AC: devuelve `{allow, reason_code}` correcto por caso · Riesgo: medio (lógica central) · Rollback: feature-flag `ENTITLEMENT_ENFORCE` (si off, comportamiento actual).

**ENT-004 — Integrar EntitlementService en PlaybackAuthorizationService**
- Desc: authorize llama `can_watch_channel` antes de concurrencia/token. · Archivos: `app/services/stream_auth_service.py`, `app/api/client/playback.py` · Endpoints: `POST /api/client/playback/authorize` · Validaciones: deny→403 con reason_code; allow→sigue Módulo 2 · Tests: §11.1–2,21 · AC: canal fuera de plan→403 CHANNEL_NOT_INCLUDED, NO se firma URL · Riesgo: alto (ruta crítica) · Rollback: feature-flag (revertir a "suscripción activa basta").

**ENT-005 — Endpoints admin asignar/remover canales a plan**
- Desc: gestionar plan_channels. · Archivos: `app/api/admin/plans.py` (o `app/api/v1/plans.py`) · Endpoints: `POST /api/admin/plans/{id}/channels {channel_ids:[]}`, `DELETE /api/admin/plans/{id}/channels/{channel_id}` · Tablas: plan_channels · Redis: invalidar `entitlement:plan:{id}:channels` · Validaciones: admin/reseller; canales existen · Tests: añadir/quitar; idempotencia · AC: cambios reflejados en authorize · Riesgo: medio · Rollback: revertir router.

**ENT-006 — Seed: plan anual incluye los 24 canales**
- Desc: poblar plan_channels para el plan anual de prueba con los 24 canales actuales. · Archivos: `scripts/seed_plan_channels.py` (idempotente) · Tablas: plan_channels · Validaciones: solo canales `is_active` · Tests: re-ejecución no duplica · AC: el plan anual mapea 24 canales; testuser1 sigue viendo canal-1 · Riesgo: bajo · Rollback: borrar filas del plan.

**ENT-007 — Tests canal incluido / no incluido**
- Desc: cobertura de entitlement por canal. · Archivos: `tests/test_entitlement.py` · Tests: incluido→allow; no incluido→403; plan vacío→403 · AC: verde en CI · Riesgo: bajo · Rollback: n/a.

**ENT-008 — Cache Redis de entitlement + invalidación**
- Desc: cachear suscripción/plan/canales con invalidación. · Archivos: `entitlement_service.py`, `redis_client.py` · Redis: `entitlement:subscriber:{sub}:active_subscription`, `entitlement:plan:{plan}:channels` · Validaciones: borrar (no solo expirar) al suspender/cancelar/cambiar plan/editar plan_channels · Tests: §11.20; stale tras suspensión = denegado · AC: hit-ratio alto; PG fallback si Redis cae · Riesgo: medio (stale) · Rollback: desactivar caché (leer siempre PG).

**ENT-009 — Migración `subscriptions.status` enum**
- Desc: añadir `status(active/expired/cancelled/suspended)`; backfill desde `is_active`+`expires_at`. · Archivos: `migrations/versions/006_subscription_status.py`, `app/models/subscription.py` · Tablas: subscriptions · Validaciones: backfill correcto (vencidas→expired) · Tests: migración + queries · AC: authorize usa `status=active AND expires_at>now` · Riesgo: medio (datos) · Rollback: mantener `is_active` en paralelo durante transición.

**ENT-010 — Migración `devices.status` enum**
- Desc: `status(active/blocked/pending)`; backfill desde `is_blocked`. · Archivos: `007_device_status.py`, `device.py` · Validaciones: blocked↔is_blocked consistentes · Tests: register pending, block→blocked · AC: authorize exige `status=active` · Riesgo: bajo · Rollback: derivar de is_blocked.

**ENT-011 — Desacoplar login del device cap (+ flag)**
- Desc: login no falla por límite; devuelve `device_registration: registered|limit_reached`. · Archivos: `client_auth_service.py` · Endpoints: `POST /api/client/auth/login` · Validaciones: device existente→ok; nuevo+cupo→registra; nuevo+lleno→login OK sin registrar · Tests: §11.11–12 · AC: login 200 con cap lleno + flag · Riesgo: medio (contrato login) · Rollback: revertir a comportamiento actual.

**ENT-012 — `/devices/register` → 409 al límite**
- Desc: corregir 400→409 Conflict. · Archivos: `device_service.py`, `profile.py` · Endpoints: `POST /api/client/devices/register` · Tests: cap lleno→409 · AC: 409 con mensaje claro · Riesgo: bajo · Rollback: revertir status.

**ENT-013 — Reason codes estandarizados**
- Desc: enum de reason codes (§7) compartido. · Archivos: `app/core/reason_codes.py`, entitlement/playback · Tests: cada deny mapea su código · AC: 403 incluye reason_code · Riesgo: bajo · Rollback: n/a.

### 🟠 Fase 2 — Robustez, historial, operación

**ENT-014 — `subscriber_credentials`** · `008_subscriber_credentials.py`, `subscriber_credentials.py` · mover `password_hash`; rotación · Tests: login sigue OK · AC: credencial separada · RB: mantener hash en subscribers.
**ENT-015 — `subscriber_status_history`** · history al cambiar status · AC: cada cambio historizado · RB: drop.
**ENT-016 — `subscription_history`** · registrar create/renew/cancel/change_plan · AC: trazabilidad completa · RB: drop.
**ENT-017 — `device_blocks` reversible** · block crea fila, unblock cierra `unblocked_at` · AC: historial de bloqueos · RB: drop.
**ENT-018 — `device_history`** · register/block/unblock/delete/rebind · AC: auditoría de device · RB: drop.
**ENT-019 — Suspensión revoca playback** · `subscribers/{id}/suspend` revoca sesiones+tokens+ZSET · Tests: §11.17 · AC: tras suspender no reproduce · RB: revertir hook.
**ENT-020 — Cancelación revoca playback** · ya parcial en `cancel()`; asegurar tokens+ZSET · Tests: §11.16 · AC: tras cancelar no reproduce.
**ENT-021 — `change-plan` + invalidación** · `POST /api/admin/subscriptions/{id}/change-plan` · invalida caché entitlement · Tests: §11.13–14 · AC: cambio afecta playback inmediato · RB: revertir.
**ENT-022 — Admin subscribers CRUD + suspend/activate/disable** · `GET/POST/PATCH /subscribers`, `/{id}/suspend|activate|disable` · audit · AC: operable + auditado · RB: revertir router.
**ENT-023 — Eventos de invalidación de caché** · publish/borrar keys al mutar plan/plan_channels/status · Tests: §11.20 · AC: sin stale tras mutación · RB: TTL corto.
**ENT-024 — Borrado de device revoca sesiones** · DELETE device revoca sesiones del device primero · Tests: §11.18 · AC: no quedan sesiones huérfanas · RB: revertir.
**ENT-025 — Cobertura audit_logs en todas las ops admin** · cada acción admin → audit · AC: 100% de mutaciones auditadas · RB: n/a.

### 🟢 Fase 3 — Evolución

**ENT-026 — Capa `packages` + `package_contents`** · plan→packages→contents; `plan_channels` pasa a vista derivada · ruta de migración documentada · AC: add-ons opcionales · RB: mantener plan_channels.
**ENT-027 — `plan_vod_categories`** · entitlement VOD por categoría · AC: VOD por plan.
**ENT-028 — `plan_series_categories`** · entitlement series · AC: series por plan.
**ENT-029 — Endpoint interno `/internal/entitlements/check-channel`** · solo red interna/mTLS, nunca público · AC: usable por otros servicios.
**ENT-030 — Job de expiración** · marca `subscriptions.status=expired` al vencer (evita stale `is_active`) · cron/background · AC: sin filas activas vencidas.

---

## 11. Plan de pruebas (FASE 11)

### 11.1 Entitlement (núcleo)
| # | Caso | Esperado |
|---|---|---|
| 1 | Subscriber activo + plan + canal incluido | allow |
| 2 | Subscriber activo + canal NO incluido | deny 403 CHANNEL_NOT_INCLUDED |
| 3 | Subscriber suspendido | deny 403 SUBSCRIBER_SUSPENDED |
| 4 | Subscriber disabled/banned | login deny 403 SUBSCRIBER_DISABLED |
| 5 | Subscription vencida | deny 403 SUBSCRIPTION_EXPIRED |
| 6 | Plan inactivo | deny 403 PLAN_INACTIVE |
| 7 | Plan sin canales | deny 403 CHANNEL_NOT_INCLUDED |
| 8 | Device registrado+activo | allow |
| 9 | Device no registrado | deny 403 DEVICE_NOT_REGISTERED |
| 10 | Device bloqueado | deny 403 DEVICE_BLOCKED |

### 11.2 Devices y suscripción
| # | Caso | Esperado |
|---|---|---|
| 11 | Device cap alcanzado → register | 409 DEVICE_LIMIT_REACHED |
| 12 | Login con device cap alcanzado | login 200 + flag limit_reached |
| 13 | Cambio de plan elimina canal | playback deny inmediato |
| 14 | Cambio de plan agrega canal | playback allow inmediato |
| 15 | Renovación de vencida | playback allow |
| 16 | Cancelación | playback deny/revoke |
| 17 | Suspensión | revoca sesiones activas |
| 18 | Bloqueo device | revoca sesiones del device |

### 11.3 Operación y rendimiento
| # | Caso | Esperado |
|---|---|---|
| 19 | Acción admin | genera audit_log |
| 20 | Cambio plan/plan_channels/status | invalida caché entitlement |
| 21 | Entitlement deny | Playback NO consulta/firma URL |
| 22 | 10k subscribers × 1k canales | query entitlement < ~5ms (índice pk+idx(channel_id)) |

---

## 12. Checklist de aceptación

- [ ] `plan_channels` (migración + modelo) creado con PK(plan_id,channel_id)+idx(channel_id)
- [ ] `EntitlementService.can_watch_channel` con los 10 reason codes
- [ ] `PlaybackAuthorizationService` consulta entitlement ANTES de concurrencia/token
- [ ] Canal no incluido → 403 (no se firma URL)
- [ ] `subscriptions.status` enum (active/expired/cancelled/suspended) + `starts_at/ends_at`
- [ ] `devices.status` enum (active/blocked/pending)
- [ ] Login NO falla por device cap; `/devices/register` → 409
- [ ] Suspensión/cancelación/bloqueo revocan playback activo
- [ ] Cambio de plan / edición de plan_channels invalida caché y afecta playback inmediato
- [ ] PostgreSQL fuente de verdad; Redis solo caché con invalidación (borrar en eventos críticos)
- [ ] Historial: status_history, subscription_history, device_blocks (reversible), device_history
- [ ] Toda mutación admin → audit_log
- [ ] Seed: plan anual incluye los 24 canales; testuser1 sigue reproduciendo
- [ ] Tests 1–22 verdes; query entitlement indexada y rápida
