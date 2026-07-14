# Módulo 2 — Diagnóstico Live TV (FASE 3)

> Diagnóstico defensivo (solo lectura) del playback en vivo de `nexoraplay.net`. Evidencia tomada en producción con curl read-only; tokens/secretos enmascarados; sin descargar streams completos. Este documento es el preludio del diseño profundo (`modules/02_PLAYBACK_DEEP.md`, pendiente).

---

## 1. Veredicto

**El backend de playback está SANO end-to-end.** La cadena `authorize → playback_url (HTTPS) → manifest HLS` funciona para un dispositivo registrado con suscripción vigente. El síntoma original *"no se ven los canales"* tenía como causa raíz una **suscripción vencida** (ya resuelta). El bloqueo real que queda hoy para un **dispositivo nuevo** del usuario es el **límite de dispositivos acoplado al login**, y la deuda de seguridad pendiente es que la **playback_url aún no está firmada** (anti-hotlink no aplicado).

---

## 2. Evidencia (producción, enmascarada)

### 2.1 Estado público
- `GET /` → HTTP 200 (HSTS, X-Frame-Options, X-Content-Type-Options, Referrer-Policy) ✅
- `/health`, `/api/health` → `redis:ok` ✅
- `/stream/ec-main/`, `/stream/co-main/` → 302 (Flussonic responde) ✅

### 2.2 Login + suscripción
- `POST /api/client/auth/login` (device ya registrado) → **200**, `access_token` (len≈279, `eyJ…`).
- `GET /api/client/profile` → `status:"active"`, suscripción vigente (`days_remaining` > 0). ✅
  (La suscripción anual se creó en la sesión del incidente vía API admin; cerró el `403 "No active subscription found"` original.)

### 2.3 Cadena de reproducción (canal-1)
```
POST /api/client/playback/authorize {channel_id:"canal-1", device_id} → HTTP 200
playback_url = https://nexoraplay.net/stream/co-main/TeleNostalgia/index.m3u8
   ✓ HTTPS same-origin      ✓ pasa por /stream/*      ✓ NO contiene IP origen (38.210/181.78)
GET (HEAD) playback_url → HTTP 200 · Content-Type: application/vnd.apple.mpegurl
manifest → #EXTM3U / #EXT-X-STREAM-INF ... RESOLUTION=1920x1080 (variante válida)
```
→ El player **debería** reproducir con este `playback_url`.

---

## 3. Hallazgos actuales (por severidad)

| Sev | Hallazgo | Evidencia | Área/endpoint |
|---|---|---|---|
| 🟠 Alto | **Device limit (5) bloquea el LOGIN completo** y devuelve **HTTP 400** | device nuevo (`claude-diag-002`, 6º) → `400 "Device limit reached (5)"`; device ya registrado → 200 | `ClientAuthService.login` → `DeviceService.register` · `POST /api/client/auth/login` |
| 🔴 Alto (seguridad) | **playback_url SIN firma** (`?token=` ausente) → el manifest se sirve público por el proxy | `playback_url` no contiene token; manifest 200 sin auth | `_resolve_playback_url` (playback.py) + Nginx `/stream/*` |
| 🟡 Medio | **CORS abierto** en el edge (`access-control-allow-origin: *`) | header del manifest | Flussonic edge (config externa) |
| 🟡 Medio | **El plan no valida el canal** (no hay `plan_channels`) | authorize no comprueba entitlement por canal | `StreamAuthService.authorize` |

> No se observaron: credenciales en URL ✗, IP origen expuesta ✗, mixed-content ✗, URL http directa ✗.

---

## 4. Causa raíz del síntoma "no se ven los canales"

1. **Histórica (resuelta):** la suscripción de `testuser1` venció (2026-06-17) → `authorize` devolvía `403 "No active subscription found"` → el player no obtenía `playback_url`. **Resuelto** al crear la suscripción anual.
2. **Actual (más probable para un equipo nuevo):** si el dispositivo del usuario **no** está entre los 5 registrados, el **login falla con 400** y el usuario "no puede ver nada" — no porque el playback falle, sino porque **ni siquiera autentica**. `testuser1` ya tiene 5 devices ocupados (varios de diagnóstico + de prueba).

---

## 5. Archivos / endpoints afectados

| Tema | Ubicación |
|---|---|
| Login + auto-registro de device | [app/services/client_auth_service.py](../../../app/services/client_auth_service.py) (`login` → `DeviceService.register`) |
| Device limit | `DeviceService.register` (límite `max_devices`=5) |
| Authorize / playback_url | [app/api/client/playback.py](../../../app/api/client/playback.py) (`_resolve_playback_url`) |
| Firma/validación en edge | [deploy/nginx/nexoraplay.conf](../../../deploy/nginx/nexoraplay.conf) (`/stream/*`) + `/api/stb/auth/validate` (ya existe) |
| Entitlement por canal | [app/services/stream_auth_service.py](../../../app/services/stream_auth_service.py) (`authorize`) |

---

## 6. Correcciones propuestas (con decisiones tomadas)

### 6.1 Device limit — **desacoplar del login + 409** (decisión aprobada)
- **Qué:** el login **no** debe fallar por tope de dispositivos. Autenticar y devolver tokens; el alta de device se maneja aparte y devuelve **`409 "device limit reached"`** solo en `POST /api/client/devices/register`, indicando al usuario liberar uno.
- **Por qué:** un usuario legítimo con suscripción vigente no debe quedar fuera por un cap de hardware; además `400` es semánticamente incorrecto (debe ser `409 Conflict`).
- **Archivo:** `client_auth_service.py` (separar `register` del flujo de login; en login: si el device ya existe → ok; si es nuevo y hay cupo → registrar; si no hay cupo → **login OK** pero marcar `device_registration: "limit_reached"` en la respuesta, sin bloquear).
- **Riesgo:** bajo. Cambia contrato de error (400→409) y semántica de login; el web player debe manejar el flag.
- **Mitigación inmediata (sin código):** liberar devices sobrantes de `testuser1` vía admin para pruebas.

### 6.2 Anti-hotlink — **Nginx `auth_request` → FastAPI** (decisión aprobada)
- **Qué:** firmar la `playback_url` con un token de playback y que el **`/stream/*` de Nexora** valide cada request vía `auth_request` contra `/api/stb/auth/validate` (que ya existe y valida JWT playback + Redis + sesión). **No** se toca Flussonic (read-only/producción).
- **Flujo objetivo:**
  ```
  authorize → playback_url = https://nexoraplay.net/stream/<node>/<stream>/index.m3u8?token=<jwt_playback>
  Nginx /stream/  → auth_request /__playback_auth (interno) → FastAPI valida token (IP+session+exp)
       200 ⇒ proxy_pass a Flussonic | 401 ⇒ bloquea
  ```
- **Por qué:** aplica anti-hotlink/replay sin exponer origen ni depender de configurar Flussonic.
- **Riesgo:** una llamada de validación por request HLS → cachear validación corta en Redis; afinar para no penalizar segmentos.

### 6.3 Entitlement por canal
- **Qué:** authorize debe validar que el plan del suscriptor incluye el canal (`plan_channels`/`package_contents`). Hoy solo valida suscripción activa (cualquier canal).
- **Diferido** al diseño profundo (Módulo 2 deep + Módulo de Plans).

---

## 7. Verificación después de corregir

| Corrección | Cómo verificar |
|---|---|
| Device limit desacoplado | login con device nuevo y tope lleno → **200** + flag `device_registration: limit_reached`; `/devices/register` → **409** |
| Signed URL + auth_request | `playback_url` contiene `?token=`; GET sin token → **401**; con token válido → 200; token de otra IP/expirado → **401** |
| Entitlement | authorize de canal fuera del plan → **403** |
| Sin regresión | authorize canal del plan → 200 + manifest 200 (como hoy) |

---

## 8. Acción inmediata sugerida (sin código, reversible)
Para que un dispositivo real pueda entrar **ya** mientras se diseña el fix: liberar 1–2 dispositivos de prueba de `testuser1` (quedan 5/5 ocupados por devices de diagnóstico). Se hace vía admin API (`/api/admin/...` devices) — read/write controlado, reversible. ¿Lo hago?

---

> **Siguiente:** con estas decisiones, el diseño completo (submódulos, endpoints, modelo PG, Redis keys, algoritmo de concurrencia, reglas de signed URL, Nginx `/stream/*`, Flussonic/Astra, backlog PLAYBACK-001..030, pruebas) irá en `modules/02_PLAYBACK_DEEP.md` y el resumen en `06_FLUJO_PLAYBACK_FINAL.md`.
