# 00 — Backlog combinado de implementación · Módulos 1–3

> Plan ejecutable para convertir los documentos aprobados en implementación real. Fuentes: [01_AUTH_DEEP.md](01_AUTH_DEEP.md), [02_PLAYBACK_DEEP.md](02_PLAYBACK_DEEP.md), [03_SUBSCRIBERS_PLANS_DEVICES_ENTITLEMENTS_DEEP.md](03_SUBSCRIBERS_PLANS_DEVICES_ENTITLEMENTS_DEEP.md) (+ resúmenes 05/06/07).
> **Reglas de ejecución:** sin commit aún · sin tocar producción · sin reiniciar servicios · **sin modificar Flussonic** · sin imprimir tokens/secretos. Listo para pasar a Codex.
> Estado base: ✅ existe · 🟡 parcial · ⬜ nuevo. Head Alembic actual: **004**.

---

## 1. Resumen ejecutivo de implementación

Nexora ya tiene la base correcta (Argon2id, JWT con jti, refresh rotativo, authorize central, concurrencia ZSET atómica, sesiones IPTV PG+Redis, `/stream/*` HTTPS con origen oculto). Faltan **tres cierres P0** que juntos hacen el sistema seguro y correcto:

1. **AUTH:** separación estricta por `aud/iss/type` (hoy solo `type`) y **desacoplar el device cap del login**.
2. **ENTITLEMENT:** crear **`plan_channels`** + `EntitlementService` e integrarlo en `playback/authorize` (hoy un suscriptor activo ve **todos** los canales).
3. **PLAYBACK:** **token obligatorio en `playback_url`** validado en `/stream/*` vía **Nginx `auth_request` → FastAPI** (hoy el manifest es público — anti-hotlink ausente).

El resultado P0 es: **un usuario solo reproduce los canales de su plan, con URL firmada que caduca y no es compartible, sin tocar Flussonic.** Verificado end-to-end con la cadena `login → catálogo → authorize → playback_url → manifest 200`.

---

## 2. Dependencias entre AUTH, PLAYBACK y ENTITLEMENTS

```
AUTH (Mód.1)
  ├─ aud/iss/type estrictos ─────────────► PLAYBACK valida client_access/stb (P0-001/002)
  └─ device desacoplado del login ───────► ENTITLEMENT/DEVICE (P0-003/004)

ENTITLEMENT (Mód.3)
  ├─ plan_channels (migración+modelo) ───► EntitlementService (P0-005/006/007)
  └─ EntitlementService.can_watch_channel ─► PLAYBACK.authorize (P0-008)
                                            seed 24 canales evita romper testuser1 (P0-009)

PLAYBACK (Mód.2)
  ├─ authorize (ya central) ── usa ──────► EntitlementService (dep de Mód.3)
  ├─ token en playback_url (P0-011) ─────► Nginx auth_request (P0-012/013)
  └─ playback_url HTTPS/same-origin ✅ (P0-014, ya cumplido; blindar)
```

**Reglas de dependencia:**
- PLAYBACK `authorize` **no** puede aplicar entitlement por canal hasta que exista `plan_channels` + `EntitlementService` → **Mód.3 P0 precede a P0-008**.
- Activar token obligatorio en `/stream/*` (P0-011..013) **debe ir junto** con firmar la URL (si se exige token y la URL no lo lleva, se rompe el playback) → **atómico**.
- `aud/iss` (P0-001/002) es transversal pero **independiente** del entitlement; puede ir en paralelo con migración de datos.

---

## 3. Camino crítico P0

```
P0-001 ─┐
P0-002 ─┴─(AUTH, paralelo)
P0-003 ─┐
P0-004 ─┴─(DEVICE, paralelo)
P0-005 → P0-006 → P0-007 → P0-008 → P0-009 → P0-010   (ENTITLEMENT, secuencial — núcleo)
P0-011 ─┬─(PLAYBACK token, ATÓMICO con)
P0-012 ─┤
P0-013 ─┘
P0-014 (verificar/blindar) → P0-015 (E2E)
```
**El cuello de botella** es la cadena `005→008` (entitlement) y el bloque atómico `011→013` (token en edge). Todo lo demás paraleliza.

---

## 4. Backlog agrupado por prioridad

### 🔴 P0 — Crítico (obligatorio)

| ID | Tarea | Archivos | Migración | Endpoint | Test | AC | Rollback |
|---|---|---|---|---|---|---|---|
| **P0-001** | aud/iss/type estrictos en JWT | `app/core/security.py` (añadir `aud`,`iss` al encode), `app/config.py` (issuer/audiences) | — | todos | token sin/incorrecto `aud`→401 | todos los JWT llevan `aud`+`iss`; decode valida | flag `JWT_REQUIRE_AUD` (off acepta legacy) |
| **P0-002** | Separar `admin_access`/`client_access`/`stb_access` | `security.py`, `app/core/dependencies.py` | — | admin/client/stb | token cliente en ruta admin→403; admin en cliente→403 | cada dependencia exige su `aud`+`type` | aceptar `type` viejo en ventana de migración |
| **P0-003** | Desacoplar device cap del login | `app/services/client_auth_service.py` | — | `POST /api/client/auth/login` | login con cap lleno→200 + flag | login no falla por límite; devuelve `device_registration` | revertir a registro acoplado |
| **P0-004** | `/devices/register` → 409 al límite | `app/services/device_service.py`, `app/api/client/profile.py` | — | `POST /api/client/devices/register` | cap lleno→409 | 409 Conflict + mensaje claro | revertir status 400 |
| **P0-005** | Migración `plan_channels` | `migrations/versions/005_plan_channels.py` | **005** | — | up/down | tabla PK(plan_id,channel_id)+idx(channel_id) | `downgrade()` drop |
| **P0-006** | Modelo `PlanChannel` | `app/models/plan_channel.py`, rel en `plan.py`/`channel.py` | — | — | insert/cascade | relaciones navegables | revertir modelo |
| **P0-007** | `EntitlementService.can_watch_channel` | `app/services/entitlement_service.py`, `app/core/reason_codes.py` | — | interno | casos §11 Mód.3 | `{allow,reason_code}` correcto | flag `ENTITLEMENT_ENFORCE=off` |
| **P0-008** | Integrar Entitlement en authorize | `app/services/stream_auth_service.py`, `app/api/client/playback.py` | — | `POST /api/client/playback/authorize` | canal no incluido→403 (no firma URL) | deny→403 reason_code; allow→sigue | flag enforce off |
| **P0-009** | Seed: plan anual = 24 canales | `scripts/seed_plan_channels.py` (idempotente) | — | — | re-run no duplica | testuser1 sigue viendo canal-1 | borrar filas del plan |
| **P0-010** | Tests canal incluido/no incluido | `tests/test_entitlement.py` | — | — | — | verde en CI | n/a |
| **P0-011** | Token obligatorio en `playback_url` | `stream_auth_service.py`, `playback.py` (`_resolve_playback_url`) | — | authorize | `playback_url` lleva `?token=` | URL firmada; **atómico con P0-012/013** | flag `SIGNED_URL_ENFORCE=off` |
| **P0-012** | Nginx `auth_request` → FastAPI | `deploy/nginx/nexoraplay.conf` (no aplicar a prod aún), `/api/stb/auth/validate` (ya existe) | — | `/stream/*`, `/__playback_auth` | GET con token válido→200 | `/stream/*` valida token vía `auth_request` | quitar `auth_request` |
| **P0-013** | `/stream/*` sin token → 401/403 | `nexoraplay.conf` + validate | — | `/stream/*` | GET sin token→401 | acceso sin token bloqueado | desactivar enforce |
| **P0-014** | playback_url HTTPS/same-origin/sin IP | `playback.py`, config `FLUSSONIC_*_BASE_URL` | — | authorize | inicia con `https://nexoraplay.net/stream/`; sin IP origen | ✅ ya cumplido; añadir test de regresión | n/a |
| **P0-015** | E2E: login→catálogo→authorize→playback_url→manifest 200 | `tests/test_e2e_playback.py` | — | cadena | manifest 200 `application/vnd.apple.mpegurl` | E2E verde (staging) | n/a |

### 🟠 P1 — Alto (robustez/operación)
| ID | Tarea | Origen |
|---|---|---|
| P1-001 | Revocación unificada a allowlist (admin migra de blacklist) | AUTH-004 |
| P1-002 | `client_refresh_tokens` PG (hash+family) + reuse-detection | AUTH-005/006 |
| P1-003 | `admin_sessions` + `login_attempts` + auditoría de login admin | AUTH-007/008/009 |
| P1-004 | IP real por proxy de confianza | AUTH-010 |
| P1-005 | Rutas `/api/admin/auth/*` formales (alias de `/api/v1/auth/*`) | AUTH-011 |
| P1-006 | `subscriptions.status` enum (mig 006) + job de expiración | ENT-009/030 |
| P1-007 | `devices.status` enum (mig 007) | ENT-010 |
| P1-008 | Endpoints admin plan↔canales (`POST/DELETE /plans/{id}/channels`) | ENT-005 |
| P1-009 | `change-plan` + invalidación de caché | ENT-021 |
| P1-010 | Suspensión/cancelación/bloqueo revocan playback | ENT-019/020/024 |
| P1-011 | Concurrencia atómica en Lua | PB-020 |
| P1-012 | `/playback/heartbeat` + `/playback/stop` dedicados | PB-021/022 |
| P1-013 | Caché Redis de entitlement + invalidación por eventos | ENT-008/023 |
| P1-014 | `stream_nodes` formal (de `.env` a DB) + health + failover | PB-023/024/025 |
| P1-015 | Historial: status_history, subscription_history, device_blocks, device_history | ENT-015..018 |
| P1-016 | RBAC admin (roles/permissions) | AUTH-020 |
| P1-017 | Ocultar token en logs (Nginx/app) + CORS explícito en `/stream/` | PB-014/030 |

### 🟢 P2 — Evolución (F2/F3)
| ID | Tarea |
|---|---|
| P2-001 | `packages`/`package_contents` (plan_channels → vista derivada) |
| P2-002 | VOD/Series + entitlement (`plan_vod_categories`,`plan_series_categories`) |
| P2-003 | Adapter Astra (`StreamProvider`) |
| P2-004 | XtreamCompat read-only (sin credenciales en URL) |
| P2-005 | STB handshake endurecido (device_secret/cert) |
| P2-006 | Monitoring stack (Prometheus/Grafana/OTel) + alertas |
| P2-007 | `playback_sessions`/`audit_log` particionadas + retención |
| P2-008 | Billing (`BillingProvider`, webhooks idempotentes) |

---

## 5. Orden exacto de implementación

```
# Fase P0 (sprint 1) — paralelizable en 3 carriles + cierre
Carril A (AUTH):     P0-001 → P0-002
Carril B (DEVICE):   P0-003 → P0-004
Carril C (ENTITLE):  P0-005 → P0-006 → P0-007 → P0-008 → P0-009 → P0-010
Cierre (PLAYBACK):   P0-011 + P0-012 + P0-013 (atómico) → P0-014 → P0-015 (E2E)

Regla: P0-015 (E2E) es el gate. No se pasa a P1 sin E2E verde en staging.

# Fase P1 (sprint 2): P1-006/007 (enums) → P1-008/009/010 (admin+revocación) →
#   P1-002/003 (refresh/audit) → P1-011/012 (concurrencia/heartbeat) →
#   P1-013/014 (cache/nodos) → P1-001/004/005/015/016/017

# Fase P2 (después): según roadmap (doc 12).
```

---

## 6. Archivos a crear / modificar

**Crear:**
- `app/models/plan_channel.py`
- `app/services/entitlement_service.py`
- `app/core/reason_codes.py`
- `migrations/versions/005_plan_channels.py` (P1: 006 subscription_status, 007 device_status, 008 subscriber_credentials)
- `scripts/seed_plan_channels.py`
- `tests/test_entitlement.py`, `tests/test_e2e_playback.py`, `tests/test_auth_aud.py`

**Modificar:**
- `app/core/security.py` (aud/iss/type)
- `app/core/dependencies.py` (validación por aud/type/superficie)
- `app/config.py` (issuer, audiences, flags `JWT_REQUIRE_AUD`,`ENTITLEMENT_ENFORCE`,`SIGNED_URL_ENFORCE`)
- `app/services/client_auth_service.py` (desacople device cap)
- `app/services/device_service.py`, `app/api/client/profile.py` (409)
- `app/services/stream_auth_service.py`, `app/api/client/playback.py` (entitlement + token en URL)
- `app/models/plan.py`, `app/models/channel.py` (relationships)
- `deploy/nginx/nexoraplay.conf` (auth_request — **NO desplegar a prod aún**)

---

## 7. Migraciones necesarias

| Rev | Contenido | Prio | Reversible |
|---|---|---|---|
| **005** | `plan_channels(plan_id FK, channel_id FK, PK compuesta, idx(channel_id))` | P0 | sí (drop) |
| 006 | `subscriptions.status` enum + backfill desde `is_active`+`expires_at` | P1 | sí (mantener is_active) |
| 007 | `devices.status` enum + backfill desde `is_blocked` | P1 | sí |
| 008 | `subscriber_credentials` + mover `password_hash` | P1 | sí (hash en subscribers) |
| 009+ | history tables, device_blocks, particiones | P1/P2 | sí |

> Aplicar con `alembic upgrade head` **en staging primero**. Nunca `downgrade` en prod con datos sin backup.

---

## 8. Endpoints afectados

| Endpoint | Cambio | Prio |
|---|---|---|
| `POST /api/client/auth/login` | no falla por device cap; flag `device_registration` | P0-003 |
| `POST /api/client/devices/register` | 409 al límite | P0-004 |
| `POST /api/client/playback/authorize` | entitlement por canal + token en `playback_url` | P0-008/011 |
| `/stream/*` (Nginx) | `auth_request` → 401 sin token | P0-012/013 |
| `POST /api/stb/auth/validate` | usado por auth_request (ya existe) | P0-012 |
| `/api/v1/auth/login`, `/api/client/auth/*` | aud/iss/type | P0-001/002 |
| `POST/DELETE /api/admin/plans/{id}/channels` | gestionar plan_channels | P1-008 |
| `POST /api/admin/subscriptions/{id}/change-plan` | cambio + invalidación | P1-009 |

---

## 9. Tests obligatorios

**P0 (gate):**
1. JWT sin `aud`/`aud` incorrecto → 401; token cliente en ruta admin → 403.
2. Login con device cap lleno → 200 + flag; `/devices/register` lleno → 409.
3. authorize canal **incluido** → 200 + playback_url firmada; canal **no incluido** → 403, **sin** `playback_url`.
4. `/stream/...` **sin** token → 401; **con** token válido → 200; token de otra IP/expirado → 401.
5. playback_url empieza por `https://nexoraplay.net/stream/`, **sin** IP origen.
6. **E2E:** login → catálogo (24) → authorize → GET playback_url → manifest 200 `application/vnd.apple.mpegurl`.
7. Seed: tras enforcement, testuser1 (plan anual con 24 canales) sigue reproduciendo canal-1.

**P1:** refresh reuse-detection; suspensión revoca playback; change-plan afecta entitlement inmediato; concurrencia carrera ≤max; cache invalida tras mutación.

---

## 10. Riesgos

| Riesgo | Mitigación |
|---|---|
| Activar entitlement rompe a usuarios sin `plan_channels` poblado | **P0-009 seed antes de P0-008**; flag `ENTITLEMENT_ENFORCE` gradual |
| Exigir token en `/stream/*` sin firmar la URL rompe playback | P0-011/012/013 **atómicos**; flag `SIGNED_URL_ENFORCE`; validar en staging |
| `auth_request` por segmento añade latencia | caché de validación por `jti` en Redis (TTL corto) |
| Migración `subscriptions.status` con datos vivos | backfill probado en staging; mantener `is_active` en paralelo |
| Cambio de claims JWT invalida tokens vivos | `JWT_REQUIRE_AUD` en modo permisivo durante ventana de migración |
| Cambios en Nginx afectan prod | **NO desplegar `nexoraplay.conf` a prod** hasta validar; mantener copia actual |
| auth_request mal configurado bloquea todo el playback | feature-flag + smoke test antes de recargar Nginx (fuera de este alcance) |

---

## 11. Rollback

- **Por feature-flag (preferente):** `JWT_REQUIRE_AUD`, `ENTITLEMENT_ENFORCE`, `SIGNED_URL_ENFORCE` → `off` revierte comportamiento sin redeploy.
- **Migraciones:** `alembic downgrade -1` (005/006/007 son reversibles; con backup previo).
- **Nginx:** restaurar `nexoraplay.conf` anterior (guardar copia antes de cambiar) y recargar.
- **Seed:** `seed_plan_channels.py` es idempotente; para revertir, borrar filas del plan afectado.
- **Código:** cada P0 en su propia rama/PR pequeño → revert de PR aislado.

---

## 12. Checklist de despliegue (staging → prod)

- [ ] Todo P0 mergeado con tests verdes en CI.
- [ ] Migración 005 aplicada en **staging**; `plan_channels` poblado (seed P0-009).
- [ ] Flags en modo seguro: `ENTITLEMENT_ENFORCE`/`SIGNED_URL_ENFORCE` validados en staging antes de prod.
- [ ] E2E (P0-015) verde en staging.
- [ ] Copia de seguridad de `deploy/nginx/nexoraplay.conf` actual.
- [ ] Backup de PostgreSQL antes de migrar en prod.
- [ ] Ventana de mantenimiento acordada (cambios en Nginx/Flussonic-edge no aplican: Flussonic intacto).
- [ ] Plan de rollback por flag verificado.
- [ ] **No** se modifica Flussonic en ningún paso.

---

## 13. Verificación end-to-end (read-only, enmascarada)

```bash
B=https://nexoraplay.net; C="curl -sS --ssl-no-revoke"
# 1. login (device ya registrado o con cupo)
TOKEN=$($C -X POST $B/api/client/auth/login -H 'Content-Type: application/json' \
  -d '{"username":"testuser1","password":"***","device_id":"<device>","device_type":"web_player","model":"x","brand":"Nexora","os_version":"x"}' \
  | jq -r .access_token)            # no imprimir el token
# 2. catálogo (24 canales)
$C $B/api/client/catalog/channels -H "Authorization: Bearer $TOKEN" | jq 'length'   # → 24
# 3. authorize (canal del plan)
R=$($C -X POST $B/api/client/playback/authorize -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' -d '{"channel_id":"canal-1","device_id":"<device>"}')
echo "$R" | jq '{http:"200", has_token:(.token!=null)}'
PURL=$(echo "$R" | jq -r .playback_url)   # contiene ?token= (enmascarar al loguear)
# 4. manifest (con token, tras P0-011..013)
$C -I "$PURL" | grep -iE '^HTTP|content-type'   # → 200 application/vnd.apple.mpegurl
# 5. negativos
#   - canal NO en plan → authorize 403 CHANNEL_NOT_INCLUDED, sin playback_url
#   - GET /stream/.../index.m3u8 SIN token → 401
#   - playback_url empieza por https://nexoraplay.net/stream/ y NO contiene IP origen
```

**Criterio de éxito P0:** pasos 1–4 OK **y** los 3 negativos del paso 5 se comportan como se espera.

---

> **Resultado:** este documento es el plan de implementación para Codex. Camino crítico = `P0-005→008` (entitlement) + bloque atómico `P0-011→013` (token en edge), con `P0-009` (seed) como red de seguridad para no romper a testuser1. Nada se aplica a producción ni a Flussonic hasta validar en staging con flags.
