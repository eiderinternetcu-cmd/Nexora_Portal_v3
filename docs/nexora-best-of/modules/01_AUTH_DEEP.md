# Módulo 1 — AUTH / Login / Sesiones / Refresh / Dispositivos (investigación profunda)

> Investigación defensiva (solo lectura) sobre Xtream 2.93 (**A**), R22F/CKMOD41 (**B**) y Ministra/Stalker (**C**), reconciliada con el **código real de `nexora_api`**. Sin copiar código legacy, sin secretos completos, sin credenciales en URL, sin bypass de licencia.
> Leyenda de estado en Nexora: ✅ hecho · 🟡 parcial · ⬜ por construir.
> Resumen ejecutivo enlazado desde [../05_FLUJO_AUTH_FINAL.md](../05_FLUJO_AUTH_FINAL.md).

---

## 1. Resumen comparativo

### 1.1 Extracción del legacy (FASE 1)

| # | Pregunta | Xtream 2.93 (A) | R22F/CKMOD41 (B) | Ministra (C) |
|---|---|---|---|---|
| 1 | Login admin (archivo) | `login.php` → `functions.php:doLogin()` [LEÍDO] | seed `install.py:238` (`admin/admin`); app descargada | `server/Lib/Admin.php:20 checkAuthorization()` [LEÍDO] |
| 2 | Login cliente/player | `player_api.php` **ausente** [INFERIDO] | ausente [INFERIDO] | núcleo STB **ofuscado** + `User.php` (wrapper) |
| 3 | Auth MAG/STB | admin de devices en `mag.php`/`api.php`; portal ausente | `mag_security` flag (d.py) | `load.php?type=stb&action=handshake` → mgr ofuscado |
| 4 | Endpoints | `login.php`, `api.php` (AJAX panel) | instalador | `load.php` (type/action), `api/v{1,2,3}`, SOAP |
| 5 | Parámetros | `username,password` (panel) | — | `mac,sn,device_id,stb_type,token` |
| 6 | Tablas | `reg_users`,`member_groups`,`users`,`user_activity_now` | mismas (seed) | `administrators`,`users`,`access_tokens` |
| 7 | Campos críticos | `reg_users.password`(salt fijo),`users.exp_date/max_connections/bouquet` | idem | `administrators.pass`(MD5),`users.mac/access_token/status` |
| 8 | Validación password | `crypt($pw,'$6$…$xtreamcodes$')` **salt fijo**, `==` | idem (seed `admin/admin`) | `md5($pass)` admin; `md5(md5($pw).$id)` usuario |
| 9 | Estado activo | `users.enabled=1 AND admin_enabled=1` | idem | `users.status` (**invertido** 0=activo) |
| 10 | Expiración | `users.exp_date > now` [INFERIDO] | idem | `tariff_expired_date`,`expire_billing_date` |
| 11 | max_connections | `COUNT(user_activity_now) < users.max_connections` [INFERIDO] | idem | por streamer `StreamServer::getStreamerSessions` |
| 12 | Dispositivo/MAC | `mag_devices.mac → users.id` | `mag_security` | `users.mac` UNIQUE + `auto_add_stb=true` |
| 13 | Crear sesión | sesión PHP `$_SESSION['user_id']` (panel); `user_activity_now` (línea) | idem | `access_token=md5(microtime+uniqid)`; `$_SESSION['pass']=hash` |
| 14 | Cerrar sesión | logout.php (panel); watchdog baja la línea | idem | re-handshake al cambiar MAC borra token |
| 15 | Actualizar actividad | watchdog/`user_activity_now` | idem | `Watchdog::getEvents()` por MAC (sin token) |
| 16 | Login fallido | sin rate-limit/lockout | idem | sin lockout; `==` type-juggling |
| 17 | IP/User-Agent | `getIP()` confía en `X-Forwarded-For`/`Client-IP` | mitigación CF en d.py | `HTTP_X_REAL_IP` confiado; `allowed_ua` |
| 18 | Login↔playback | credenciales en URL de playback | idem | `createLink` **no** revalida (IDOR) |
| 19 | Login↔bouquets | `users.bouquet` (JSON ids) | idem | `getServicesByType()` entitlements |
| 20 | Login↔MAG/STB | `mag_devices.user_id` | idem | MAC↔users + handshake ofuscado |

> **Conclusión de extracción:** ninguna plataforma valida bien; **C** es la más rica (auth real legible salvo núcleo ofuscado) y revela el patrón device/handshake/entitlements; **A** aporta el login admin real; **B** confirma el seed inseguro.

### 1.2 Matriz comparativa (FASE 2)

| Tema | Xtream 2.93 | R22F/CKMOD41 | Mejor enfoque | Riesgo detectado | Decisión Nexora |
|---|---|---|---|---|---|
| 1 Login admin | `doLogin` salt fijo | `admin/admin` seed | ninguno | crítico | Argon2id + JWT `admin_access`+aud ✅🟡 |
| 2 Login cliente | inferido (URL creds) | inferido | ninguno | alto | `client_access` JWT, sin creds en URL ✅ |
| 3 Login MAG/STB | MAC | `mag_security` | C (handshake) | MAC spoof | handshake reto-respuesta + device_secret ⬜ |
| 4 Password storage | salt fijo SHA-512 | idem | ninguno | crítico | **Argon2id** ✅ |
| 5 Password verify | `==` | `==` | ninguno | medio | `verify` constant-time ✅ |
| 6 Session mgmt | PHP session | idem | — | medio | JWT + store Redis (allowlist) ✅🟡 |
| 7 Token mgmt | — | — | — | — | jti + tipos + aud ✅🟡 |
| 8 Refresh/revocación | no | no | — | alto | refresh rotativo + revocación (híbrido PG+Redis) ✅🟡 |
| 9 Device registration | mag CRUD | idem | C parcial | auto-add | registro con estado `pending`/activación ⬜ |
| 10 MAC validation | sola | `mag_security` | C | spoofeable | device_secret/cert, MAC informativa ⬜ |
| 11 Expiration check | `exp_date` | idem | A/C | — | `subscriptions.expires_at` ✅ |
| 12 Subscriber status | enabled flags | idem | — | invertido(C) | enum `active/suspended/banned/expired` ✅ |
| 13 Max connections | `COUNT(*)` | idem | C (por streamer) | carrera | Redis ZSET atómico ✅ |
| 14 Login throttling | no | no | — | alto | lockout Redis (user+ip) ✅🟡 |
| 15 Audit logs | disperso | `/root/credentials.txt` | — | sin audit login | `audit_logs` PG (login admin) 🟡 |
| 16 Seguridad URLs | creds en URL | idem | — | alto | signed URLs; nada de creds en URL ✅ |
| 17 Separación a/c/stb | mezclado | idem | — | escalada | routers + **aud** por superficie ✅🟡 |
| 18 Errores HTTP | genérico (bien) | idem | A | enumeración | 401 genérico ✅ |
| 19 Compat legacy | contrato Xtream | idem | A | — | XtreamCompat opcional ⬜ |
| 20 Impacto playback | IDOR | idem | — | crítico | authorize central ✅🟡 |

---

## 2. Decisiones tomadas

1. **Doc profundo** en `modules/01_AUTH_DEEP.md`; `05_FLUJO_AUTH_FINAL.md` queda como resumen que enlaza aquí.
2. **Refresh híbrido PG + Redis:** `client_refresh_tokens` (hash + device + family) en PostgreSQL para forense/reuse-detection + Redis para ruta rápida.
3. **Rutas admin estandarizadas** a `/api/admin/auth/*` (hoy `/api/v1/auth/*` → migrar/alias).
4. **`aud` + `iss` + `type` unificado** en todos los JWT; validación estricta por endpoint (cierra confusión de superficie).
5. **Modelo de revocación unificado a allowlist/session-store** (suspensión invalida al instante); admin migra de blacklist a session-store.
6. **Argon2id** se mantiene (ya implementado, parámetros fuertes).
7. **IP real** desde proxy de confianza (no confiar en `X-Forwarded-For` arbitrario).
8. **Identidad de device fuerte** (device_secret/cert + activación; sin auto-add silencioso).
9. **Persistir** `login_attempts` y auditoría de login admin en PG (forense), además del lockout efímero en Redis.

---

## 3. Arquitectura AuthService (9 submódulos · FASE 4)

> Servicios de dominio reutilizables por los 3 routers (admin/client/stb). Estado actual entre paréntesis.

### 3.1 ClientAuthService ✅🟡 (`app/services/client_auth_service.py`)
- **Responsabilidad:** login suscriptor (+auto-registro device), refresh rotativo, logout.
- **Endpoints:** `/api/client/auth/{login,refresh,logout}`.
- **Tablas:** `subscribers`,`subscriber_credentials`⬜,`subscriptions`,`devices`,`device_sessions`⬜,`client_refresh_tokens`⬜.
- **Redis:** `auth:client_session:*`, `auth:refresh:*`, `auth:login_fail:*`.
- **Audit:** `client.login.ok/fail`, `client.refresh`, `client.logout`.
- **Reglas:** lockout pre-check; verificar Argon2id; status+suscripción; emitir access+refresh; binding device.
- **Errores:** 401 (cred), 403 (status/suscripción), 409 (límite device), 429 (rate).
- **Pruebas:** ver §11 casos 1–13.

### 3.2 AdminAuthService ✅🟡 (`app/services/auth_service.py`)
- **Responsabilidad:** login admin, refresh, logout; nunca acepta token cliente.
- **Endpoints:** `/api/admin/auth/{login,refresh,logout}`, `/api/admin/me`.
- **Tablas:** `admin_users`,`admin_roles`,`admin_permissions`,`admin_sessions`,`login_attempts`,`audit_logs`.
- **Redis:** `auth:admin_session:*`, `rate:login:*`.
- **Audit:** `admin.login.ok/fail`, `admin.refresh`, `admin.logout` (**inmutable**).
- **Reglas:** lockout user+ip; rol/permiso; JWT `aud=nexora-admin`; rotación.
- **Errores:** 401/403/429.
- **Pruebas:** casos 14–22.

### 3.3 StbAuthService 🟡 (`app/services/stb_service.py` + nuevo)
- **Responsabilidad:** handshake reto-respuesta, profile (entitlements), logout device.
- **Endpoints:** `/api/stb/auth/{handshake,profile,logout}`.
- **Tablas:** `devices`,`device_sessions`,`subscriptions`.
- **Redis:** `auth:device_heartbeat:*`, sesión device.
- **Reglas:** HMAC(device_secret); device `pending`→activación; `aud=nexora-stb`.
- **Errores:** 401 (handshake), 403 (device bloqueado/sin provisión).

### 3.4 RefreshTokenService 🟡 (parte de auth services + nuevo)
- **Responsabilidad:** emitir/rotar/revocar refresh; **reuse-detection** por family.
- **Tablas:** `client_refresh_tokens`,`admin_sessions`.
- **Redis:** `auth:refresh:{jti}`, `revoked:jti:{jti}`.
- **Reglas:** single-use (rotación); si llega un jti ya rotado ⇒ revocar **toda la family** + alertar.

### 3.5 DeviceSessionService 🟡 (`app/services/device_service.py` + nuevo)
- **Responsabilidad:** sesiones por dispositivo, heartbeat, `logout-all`, bloqueo.
- **Tablas:** `devices`,`device_sessions`.
- **Redis:** `auth:device_heartbeat:{device_id}`.
- **Reglas:** binding device↔subscriber; límite `max_devices`; cerrar todas las sesiones del suscriptor.

### 3.6 LoginRateLimitService ✅🟡 (en auth services)
- **Responsabilidad:** rate-limit y lockout por user/ip.
- **Redis:** `rate:login:ip:{ip}`, `rate:login:user:{username}`, `auth:login_fail:{username}:{ip}`.
- **Reglas:** N fallos ⇒ lockout TTL; backoff; (captcha opcional). Persistir resumen en `login_attempts`.

### 3.7 PasswordPolicyService 🟡 (`app/core/security.py`)
- **Responsabilidad:** hashing Argon2id, verificación, política de fortaleza, rehash.
- **Reglas:** longitud/complejidad mínima; `needs_rehash` al subir parámetros; nunca loguear claves.

### 3.8 SessionRevocationService 🟡 (`session_service.py` + dependencias)
- **Responsabilidad:** revocar por jti, por device, por suscriptor (suspensión), por logout.
- **Redis:** allowlist (`auth:*_session:*`) + `revoked:jti:*`.
- **Reglas:** suspensión/baneo ⇒ revocar todas las sesiones+refresh del suscriptor.

### 3.9 AuthAuditService 🟡 (`app/services/audit_service.py`)
- **Responsabilidad:** registrar eventos de auth (inmutable, particionado).
- **Tablas:** `audit_logs`.
- **Reglas:** login admin (faltaba en legacy), cambios de estado, emisión/revocación de tokens; **sin secretos** en el detalle.

---

## 4. Endpoints finales (FASE 5)

> Formato compacto: request → response · status · validaciones · tablas · Redis · audit · riesgos.

### Admin
**`POST /api/admin/auth/login`** (hoy `/api/v1/auth/login` ✅)
- req `{username,password}` → res `{access_token,refresh_token,token_type,expires_in}`
- 200 / 401 / 403 (rol/disabled) / 429
- valida: lockout(user,ip) → Argon2id → `is_active` → rol
- tablas: `admin_users`,`login_attempts`,`audit_logs`; redis: `rate:login:*`,`auth:admin_session:*`
- audit: `admin.login.ok|fail`; riesgo: enumeración → mensaje genérico.

**`POST /api/admin/auth/refresh`** → rota par; 200/401(revocado).
**`POST /api/admin/auth/logout`** → revoca access+refresh; 204.
**`GET /api/admin/me`** → perfil+rol+permisos; 200/401; exige `aud=nexora-admin`,`type=admin_access`.

### Cliente
**`POST /api/client/auth/login`** ✅
- req `{username,password,device_id,device_type,model,brand,os_version,app_version,activation_code?}` → `{access_token,refresh_token,expires_in,subscriber_id}`
- 200/401/403(status)/409(límite device)/429
- valida: lockout → Argon2id → status → **suscripción activa** (en authorize de playback; en login solo status) → registrar device
- tablas: `subscribers`,`devices`,`device_sessions`,`client_refresh_tokens`; redis: `auth:client_session:*`,`auth:refresh:*`
- audit: `client.login.*`.

**`POST /api/client/auth/refresh`** ✅ → rotación single-use; 200/401.
**`POST /api/client/auth/logout`** ✅ → revoca access(+refresh); 204.
**`GET /api/client/profile`** ✅ → perfil+suscripción+devices; 200/401/403.

### Dispositivos (cliente)
**`POST /api/client/devices/register`** 🟡 → alta de device adicional; 200/409(límite).
**`POST /api/client/devices/heartbeat`** ✅🟡 → presencia + eventos; **autenticado por token**; 200.
**`GET /api/client/devices`** ✅ → lista de devices del suscriptor.
**`POST /api/client/devices/logout-all`** ⬜ → revoca todas las sesiones/refresh del suscriptor; 204; audit `client.logout_all`.

### STB (futuro)
**`POST /api/stb/auth/handshake`** ⬜ → `{device_id,mac,serial,nonce}` → `{device_token,expires_in}`; HMAC(device_secret); 200/401/403(no provisionado).
**`POST /api/stb/auth/profile`** ⬜ → settings+cuenta+entitlements; `aud=nexora-stb`.
**`POST /api/stb/auth/logout`** ⬜ → revoca device_token.

---

## 5. Modelo PostgreSQL (FASE 6)

> Estado: algunas existen como `users`/`subscribers`/`devices`/`sessions`/`audit_logs`; el resto son nuevas. Migración Alembic dedicada.

| Tabla | Campos clave | Índices/constraints | Sensibles | Retención | Equivalente legacy | Motivo |
|---|---|---|---|---|---|---|
| **admin_users** ✅(`users`) | id uuid PK·username citext UNIQUE·password_hash·email·role_id FK·status enum·last_login_at/ip·created_at | uq(username) | password_hash | permanente | `reg_users`(A)/`administrators`(C) | admins separados de clientes |
| **admin_roles** ⬜ | id·name UNIQUE | uq(name) | — | permanente | `member_groups`/`adm_grp_action_access` | RBAC |
| **admin_permissions** ⬜ | id·code UNIQUE·role_id FK | uq(code) | — | permanente | acl matrix | permisos por endpoint |
| **admin_sessions** ⬜ | id·admin_id FK·jti UNIQUE·issued_at·expires_at·revoked_at·ip·ua | idx(admin_id),uq(jti) | — | 90d | sesión PHP | revocación/forense admin |
| **subscribers** ✅ | id·username citext UNIQUE·full_name·email·status enum(active/suspended/banned/expired)·reseller_id·max_devices·created_at | uq(username),idx(status) | — | permanente | `users`(A líneas)/`users`(C) | clientes |
| **subscriber_credentials** ⬜ | subscriber_id FK·password_hash·updated_at | pk(subscriber_id) | password_hash | permanente | `users.password` | separar credencial del perfil (rotación/audit) |
| **subscriber_status_history** ⬜ | id·subscriber_id FK·old·new·reason·actor_id·at | idx(subscriber_id,at) | — | 1–2a | — | trazabilidad de suspensión |
| **subscriptions** ✅ | id·subscriber_id FK·plan_id FK·starts_at·expires_at·is_active·created_by | idx(subscriber_id,is_active,expires_at) | — | permanente | `users.exp_date`/`tariff_*` | vigencia |
| **devices** ✅🟡 | id·subscriber_id FK·device_id UNIQUE·type·model·brand·os_version·mac·serial_hash·cert_fingerprint·device_secret_ref·status enum·last_seen | uq(device_id),idx(subscriber_id,status) | secret_ref | permanente | `mag_devices`/`users.mac` | identidad fuerte |
| **device_sessions** ⬜ | id·device_id FK·subscriber_id FK·jti·issued_at·expires_at·revoked_at·ip·ua | idx(subscriber_id),uq(jti) | — | 90d | — | logout-all, cierre por device |
| **client_refresh_tokens** ⬜ | id·subscriber_id FK·device_id FK·token_hash·family_id·issued_at·expires_at·rotated_to·revoked_at | uq(token_hash),idx(family_id),idx(subscriber_id) | token_hash | hasta exp+30d | — | reuse-detection persistente |
| **login_attempts** ⬜ | id·identity(username/ip)·kind·success·ip·ua·at | idx(identity,at) | — | 30–90d | — | forense de fuerza bruta |
| **audit_logs** ✅🟡 | id·actor_type·actor_id·action·target_type·target_id·details jsonb·ip·at | idx(actor_id,at),idx(action,at)·**append-only**·PARTITION BY at | — | 1–2a | disperso(legacy) | auditoría inmutable |

**Notas:** `subscriber_credentials` separada permite rotación e historial sin tocar el perfil; hoy el hash vive en `subscribers`/`users` (aceptable para MVP, migrable). `token_hash` = hash del refresh (nunca el token).

---

## 6. Redis keys (FASE 7)

| Key | Propósito | TTL | Crea | Actualiza | Elimina | Si Redis cae |
|---|---|---|---|---|---|---|
| `auth:client_session:{sub}:{device}:{jti}` | sesión access cliente (allowlist) | = access TTL | login/refresh | — | logout/suspensión | access inválido hasta re-login; refresh (PG) sigue → degradación segura |
| `auth:admin_session:{admin}:{jti}` | sesión access admin (allowlist) | = access TTL | login/refresh | — | logout | admin re-login |
| `auth:refresh:{jti}` | ruta rápida de refresh | = refresh TTL | login/refresh | rotación | uso/revocación | fallback a `client_refresh_tokens` (PG) |
| `auth:device_heartbeat:{device}` | presencia device | watchdog_timeout (~120s) | heartbeat | heartbeat | expira | presencia se recalcula al volver |
| `auth:login_fail:{username}:{ip}` | contador de fallos | lockout window | fallo | incr | éxito/expira | lockout se reinicia (se persiste resumen en PG) |
| `rate:login:ip:{ip}` | rate-limit por IP | ventana | request | incr | expira | rate-limit laxo temporal |
| `rate:login:user:{username}` | rate-limit por usuario | ventana | request | incr | expira | idem |
| `revoked:jti:{jti}` | denylist puntual (defensa en profundidad) | = exp del token | revocación | — | expira | allowlist sigue siendo la fuente |

> **Modelo elegido:** allowlist (la sesión **debe** existir en Redis) + denylist puntual como refuerzo. Allowlist hace que suspender/cancelar revoque al instante. PG (`client_refresh_tokens`/`admin_sessions`) es el respaldo duradero.
> Hoy Nexora usa `nexora:client:{jti}` (allowlist cliente) y `nexora:blacklist:{jti}` (admin) → **unificar** a allowlist + el esquema namespaced de arriba.

---

## 7. Reglas JWT (FASE 10)

### 7.1 Claims obligatorios
`sub` (id) · `jti` (uuid) · `type` · `aud` · `iss` (`nexora`) · `iat` · `exp`. (Hoy faltan `aud`/`iss`.)

### 7.2 Tipos y audiencias
| type | aud | TTL sugerido | Hoy |
|---|---|---|---|
| `admin_access` | `nexora-admin` | 15 min | `access`+role (sin aud) 🟡 |
| `admin_refresh` | `nexora-admin` | 7–30 d | `refresh` 🟡 |
| `client_access` | `nexora-client` | 24 h (revisar→ más corto) | ✅ (sin aud) |
| `client_refresh` | `nexora-client` | 90 d | ✅ |
| `stb_access` | `nexora-stb` | corto | ⬜ |
| `playback_token` | `nexora-playback` | 60 s | ✅ (`type=playback`, sin aud) |

### 7.3 Reglas de validación (por endpoint)
1. Algoritmo **fijo** en `decode` (ya ✅; nunca `none`). Considerar RS256 para admin (cross-service) — opcional.
2. Rechazar si falta `aud`/`type`/`jti`.
3. `aud` debe coincidir con la superficie del endpoint (admin≠client≠stb≠playback).
4. `type` debe ser el esperado por el endpoint.
5. Revocación por `jti` (allowlist debe existir; denylist no debe existir).
6. **Nunca** aceptar `playback_token` como access; **nunca** token cliente en rutas admin.
7. Rotación de refresh (single-use) + reuse-detection (family).
8. **No** loguear tokens completos (enmascarar a `xxxxxx…últimos6`).

---

## 8. Flujos finales

### 8.1 Cliente (FASE 8)
```
[App] POST /api/client/auth/login {user,pass,device}
  1. rate-limit (rate:login:ip|user) + lockout (auth:login_fail)        ✅🟡
  2. subscriber = lookup(username)                                       ✅
  3. verify Argon2id(password)  (constant-time)                         ✅
  4. status == active (suspended/banned ⇒ 403)                          ✅
  5. (suscripción activa se exige en playback/authorize)                ✅
  6. register/update device (límite max_devices ⇒ 409)                  ✅🟡
  7. access JWT (client_access, aud=nexora-client, jti)                 ✅(+aud⬜)
  8. refresh JWT (client_refresh) + guardar token_hash en PG            ✅(PG⬜)
  9. allowlist en Redis (auth:client_session, auth:refresh)            ✅
 10. audit_logs(client.login.ok)                                        🟡
 11. ← {access, refresh, expires_in, subscriber_id}                     ✅
[App] usa access para catálogo; PLAYBACK = flujo separado (doc 06)
```

### 8.2 Admin (FASE 9)
```
[Admin] POST /api/admin/auth/login {user,pass}
  1. rate-limit + lockout (user+ip)                                     ✅
  2. verify Argon2id                                                    ✅
  3. is_active + rol/permisos                                           ✅🟡
  4. crear admin_session (PG) + allowlist Redis                         🟡
  5. audit_logs(admin.login.ok)  ← faltaba en legacy                    ⬜
  6. JWT admin_access (aud=nexora-admin) + admin_refresh                ✅(+aud⬜)
  7. NUNCA aceptar token cliente en rutas admin (aud+type estricto)     🟡
```

### 8.3 STB (futuro)
```
handshake(device_id,serial,nonce + HMAC(device_secret)) → device_token (aud=nexora-stb)
profile(token) → entitlements (subs→packages→contents)
toda reproducción → /play/authorize (anti-IDOR)
```

---

## 9. Riesgos del legacy descartados (FASE 3)

### 🔴 Crítico
| Hallazgo | Archivo/func | Riesgo | Impacto si se copia | Mitigación | Prio |
|---|---|---|---|---|---|
| Salt fijo / MD5 passwords | `functions.php:cryptPassword`(A); `Admin.php:26`,`User.php:152`(C) | crackeo masivo | compromiso total | **Argon2id** ✅ | P0 |
| Admin por defecto `admin/admin`·`admin/1` | `install.py:238`(B); `Version…:374`(C) | acceso trivial | takeover | bootstrap efímero + cambio forzado | P0 |
| Sesión = hash en `$_SESSION['pass']` | `Admin.php:32,61`(C) | filtrar BD ⇒ suplantar | bypass admin | session-store opaco/JWT ✅ | P0 |
| IDOR playback (login no liga a authorize) | `Itv::createLink`(C) | ver cualquier canal | robo de contenido | authorize central ✅🟡 | P0 |
| Credenciales en URL de playback/M3U | player_api/get.php(A) | fuga en logs/proxies | exposición masiva | signed URLs ✅ | P0 |

### 🟠 Alto
| Hallazgo | Riesgo | Mitigación | Prio |
|---|---|---|---|
| Login sin rate-limit/lockout (A,B,C) | fuerza bruta | lockout Redis user+ip ✅🟡 | P1 |
| IP por header confiado `getIP`/`X-Real-IP` (A,C) | spoofing auditoría/geo | proxy de confianza | P1 |
| Identidad MAC + `auto_add_stb` (C) | suplantación device | device_secret/cert + activación ⬜ | P1 |
| Token device `md5(microtime)` (C) | predicción | CSPRNG + JWT firmado | P1 |
| Sin audit de login admin (C) | sin forense | `audit_logs` ⬜ | P1 |
| Refresh inexistente / sin revocación (A,B,C) | sesión eterna | refresh rotativo revocable ✅ | P1 |

### 🟡 Medio
| Hallazgo | Mitigación | Prio |
|---|---|---|
| `==` comparación hash (A,C) | `verify` constant-time ✅ | P2 |
| Session fixation (A) | rotar/emitir nuevo al login ✅ | P2 |
| Heartbeat por MAC sin token (C) | heartbeat autenticado 🟡 | P2 |
| SQLi/XSS panel clásico (C) | ORM + validación Pydantic ✅ | P2 |
| Enumeración por IDs secuenciales | UUID en API ✅ | P2 |

### 🔵 Bajo
| Hallazgo | Mitigación |
|---|---|
| `var_dump`/trazas, logs con IP spoofable | logs estructurados sin secretos; IP validada |
| Mensaje de login genérico (correcto) | mantener ✅ |

> Búsqueda dirigida (todas confirmadas como **descartadas** en Nexora): creds en URL✗, sesiones sin revocación✗, tokens largos sin jti✗, texto plano✗, hashing obsoleto✗, SQLi✗, sin rate-limit✗, enumeración✗, admin/cliente mezclados✗, MAC sola✗, sin audit✗, sin logout✗, sin invalidación por suspensión✗, replay de tokens✗, creds en logs✗, creds en M3U✗, endpoints públicos sensibles✗.

---

## 10. Backlog Codex (FASE 12) — AUTH-001..020

> Formato: título · descripción · archivos · tablas · endpoints · validaciones · tests · AC · riesgo · rollback.

| ID | Título | Archivos | Tablas | Endpoints | AC / Rollback |
|---|---|---|---|---|---|
| AUTH-001 | Añadir `aud`+`iss` a todos los JWT | `core/security.py` | — | todos | AC: tokens incluyen aud/iss; validación rechaza aud incorrecto. Rollback: aceptar tokens sin aud durante ventana de migración (flag) |
| AUTH-002 | Renombrar admin `type`→`admin_access/admin_refresh` | `core/security.py`,`auth_service.py` | — | admin | AC: rutas admin exigen `admin_access`. RB: aceptar ambos `type` temporal |
| AUTH-003 | Validación estricta por endpoint (aud+type+jti) | `core/dependencies.py` | — | todos | AC: tests 14–19 pasan. RB: revertir dependencia |
| AUTH-004 | Unificar revocación a allowlist (admin) | `dependencies.py`,`session_service.py` | — | admin | AC: suspender admin invalida al instante. RB: reactivar blacklist |
| AUTH-005 | Tabla `client_refresh_tokens` + hash | models, Alembic | client_refresh_tokens | — | AC: refresh persiste; sobrevive flush Redis. RB: drop tabla, Redis-only |
| AUTH-006 | Reuse-detection por family | `refresh service` | client_refresh_tokens | refresh | AC: refresh rotado reusado ⇒ revoca family + audit. RB: solo rotación |
| AUTH-007 | `admin_sessions` + forense | models, Alembic | admin_sessions | admin | AC: sesiones admin listables/revocables. RB: drop |
| AUTH-008 | `login_attempts` persistente | models, Alembic | login_attempts | login | AC: fallos registrados. RB: drop |
| AUTH-009 | Auditoría de login admin | `audit_service.py` | audit_logs | admin login | AC: cada login admin deja registro inmutable |
| AUTH-010 | IP real por proxy de confianza | `dependencies.py`,nginx | — | todos | AC: header spoof no cambia IP auditada |
| AUTH-011 | Estandarizar rutas `/api/admin/auth/*` | `api/admin/auth.py` | — | admin | AC: nuevas rutas; alias `/api/v1` deprecado. RB: mantener v1 |
| AUTH-012 | `device_sessions` + binding | models, Alembic | device_sessions | devices | AC: sesión por device |
| AUTH-013 | `POST /devices/logout-all` | `device_service.py` | device_sessions | logout-all | AC: revoca todo el suscriptor |
| AUTH-014 | Bloqueo de device + revocación | `device_service.py` | devices | admin | AC: device bloqueado no autoriza |
| AUTH-015 | STB handshake reto-respuesta | `stb_service.py` | devices,device_sessions | stb/handshake | AC: HMAC inválido ⇒ 401; MAC sola insuficiente |
| AUTH-016 | `device_secret`/cert + activación | models | devices | register | AC: device nuevo=pending |
| AUTH-017 | Rate-limit formal (token bucket) + 429 | middleware | — | login | AC: supera límite ⇒ 429 |
| AUTH-018 | `subscriber_status_history` | models, Alembic | subscriber_status_history | status | AC: suspensión registra historial |
| AUTH-019 | Enmascarar tokens en logs | logging | — | — | AC: grep de logs no revela tokens |
| AUTH-020 | `GET /api/admin/me` + permisos | `api/admin/*` | admin_roles/permissions | me | AC: devuelve rol+permisos; aud=nexora-admin |

---

## 11. Plan de pruebas (FASE 11) — 22 casos

| # | Caso | Esperado |
|---|---|---|
| 1 | Login correcto (cliente) | 200 + tokens |
| 2 | Password incorrecto | 401 genérico + fallo registrado |
| 3 | Usuario inexistente | 401 genérico (sin filtrar existencia) |
| 4 | Usuario suspendido | 403 |
| 5 | Usuario vencido (sin suscripción) | login 200 / playback 403 |
| 6 | Suscripción activa | authorize 200 |
| 7 | Suscripción expirada | authorize 403 "No active subscription" |
| 8 | Device nuevo | registrado (dentro de límite) |
| 9 | Device bloqueado | 403 |
| 10 | Refresh correcto | 200 + nuevo par; viejo invalidado |
| 11 | Refresh revocado/reusado | 401 + (reuse) revoca family + audit |
| 12 | Logout | access+refresh revocados |
| 13 | Logout all devices | todas las sesiones del suscriptor revocadas |
| 14 | Token cliente en ruta admin | 401/403 (aud≠nexora-admin) |
| 15 | Token admin en ruta cliente | 401/403 (aud≠nexora-client) |
| 16 | Token expirado | 401 |
| 17 | Token con `aud` incorrecto | 401 |
| 18 | Token con `type` incorrecto | 401 |
| 19 | Token con `jti` revocado | 401 |
| 20 | Rate-limit login | 429 tras N intentos |
| 21 | Audit log generado | registro inmutable en `audit_logs` |
| 22 | No se imprime token completo en logs | logs enmascarados |

---

## 12. Checklist de aceptación

- [ ] Argon2id en todos los hash (verificado en `security.py`) ✅
- [ ] `aud`+`iss`+`type` en todos los JWT; validación estricta por endpoint
- [ ] Separación admin/client/stb por `aud` (no solo `type`)
- [ ] Revocación unificada (allowlist) — suspensión invalida al instante
- [ ] Refresh rotativo single-use + reuse-detection (family) + PG persistente
- [ ] `device_sessions` + `logout-all` + bloqueo de device
- [ ] STB handshake con device_secret (MAC sola insuficiente)
- [ ] Rate-limit + lockout (user+ip) + 429; `login_attempts` persistido
- [ ] Auditoría inmutable de login admin y cambios de estado
- [ ] IP real por proxy de confianza (no header arbitrario)
- [ ] Cero credenciales en URL / logs / M3U
- [ ] Tests 1–22 verdes
- [ ] `playback_token` nunca aceptado como access; token cliente nunca en admin
