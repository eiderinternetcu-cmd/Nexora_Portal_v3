# ROADMAP — Nexora API (lo que está PENDIENTE)

_Actualizado: 2026-07-14_

> Este documento lista **solo lo que falta**. Lo ya entregado está en `PROJECT_STATUS.md`.
> Ordenado por dependencias reales, no por deseo. Alineado con `docs/nexora-best-of/12_ROADMAP_PRIORIZADO.md`
> (hitos M1–M5) y `11_BACKLOG_IMPLEMENTACION.md` (IDs `NX-*`).

---

## Línea base (qué YA está hecho — no está pendiente)

| Área | Estado |
|---|---|
| Fases 1–3 (auth, Client API, catálogo, Flussonic, web player, multi-device) | ✅ |
| Fase 4 · Bloque 0 (M3U real, 24→43 canales, multi-nodo) · Bloque 1 (observabilidad base, hls.js hardening) | ✅ |
| Deploy producción `nexoraplay.net` (HTTPS, `/stream/*` same-origin, UFW lockdown) | ✅ |
| Alembic **005** (`plan_channels`) + seed | ✅ |
| **PROD-2A** `ENTITLEMENT_ENFORCE=true` (anti-IDOR por plan) | ✅ |
| **PROD-2B** `JWT_REQUIRE_AUD=true` (iss/aud/type estrictos) | ✅ |
| **PROD-2C** `SIGNED_URL_ENFORCE=true` + Nginx `auth_request` + grant Redis de segmentos | ✅ validado (13 min, 396/396 req, 0 fallos) |

**Hito M1 (playback seguro) está ~80% cerrado.** Lo que falta de M1 abre la sección P0.

---

## Prioridades

🔴 **P0 — Bloqueante / cierre de M1** · 🟠 **P1 — Estabilización** · 🟡 **P2 — Endurecimiento** · 🟢 **P3+ — Crecimiento**

---

## 🔴 P0 — Cerrar M1 (playback seguro) y limpiar deuda inmediata

### P0.1 · PROD-Fase 2D: IP-binding del playback token
El token ya lleva el claim `cip` (hash de IP) y el gate Nginx ya pasa `X-Real-IP` real al backend.
Solo falta **activar el enforcement**, escalonado:
- `PLAYBACK_IP_BINDING_MODE=soft` → warn + permite. Observar mismatches varios días (clientes móviles cambian de IP).
- Solo si la evidencia lo permite → `strict` (mismatch → 403).
- **Requiere autorización explícita por flag.** Rollback: quitar la línea de `.env.production` + recrear api.
- _Referencia:_ `deploy/RUNBOOK_PRODUCTION_P0.md` · **AC:** misma IP → 200; otra IP → 200+WARN (soft) / 403 (strict).

### P0.2 · Hardening del grant de segmentos (residual de 2C)
Dos comportamientos detectados en la validación de continuidad, **ninguno rompe hoy**, pero debilitan el modelo:
1. **Latencia de revocación:** el grant (`nexora:stream_grant:{node}:{stream}:{ip_hash}`) se **auto-renueva** en cada request, así que un stream en curso queda **desacoplado del ciclo de sesión/heartbeat**. Revocar un suscriptor **no corta** su stream mientras siga pidiendo segmentos (se corta en cambio de canal, gap >180 s, o al fallar una renovación con token).
   → **Opciones:** vida máxima absoluta del grant (independiente de la renovación), y/o re-validar la sesión periódicamente dentro del grant.
2. **Token expirado = 401 duro:** el gate, ante un token *presente pero expirado*, hace 401 en vez de caer al grant. Hoy la renovación del player lo evita, pero es frágil ante fallos de renovación.
   → **Opción:** fallback al grant cuando el token esté expirado pero el grant sea válido.
- **Es cambio de código + tests** (`app/api/internal/stream_auth.py`, `app/services/stream_auth_service.py`).

### P0.3 · Deuda de versionado
- **Mergear PR #9** (`infra: version production auth_request gate`) — versiona el `nexoraplay.conf` real de prod + el runbook. Hoy el fix que corre en producción **solo está en el servidor y en ramas locales**.
- **Pushear** el commit de housekeeping `f577b51` (`.dockerignore` versionado, docs de diseño, `.gitignore` endurecido).
- Tras mergear: consolidar los `.md` de raíz en `docs/` (requiere actualizar las rutas que lee `mcp_server/server.py`).

### P0.4 · co-main caído (externo)
El nodo Flussonic `co-main` (38.210.187.13) está **caído** → 4 canales sin servicio. Es una fuente externa.
→ Health check + **alerta** de nodo/stream caído, y decidir política de **fallback** (enlaza con P2.2 `NX-FLU`).

---

## 🟠 P1 — Estabilización de playback y entornos

### P1.1 · Stress tests de playback (Fase 4 · Bloque 3 — nunca ejecutado)
Con métricas encendidas (`/api/admin/metrics`, `/api/admin/sessions/live`):
- Zapping rápido (5 canales en 30 s) → detectar sesiones zombie y falsos 409.
- Playback continuo 3–6 h → memory leaks en browser, limpieza del ZSET.
- Reconexión de red (WiFi 30 s off) → retry de hls.js recupera.
- Reinicio de `api` y de `redis` → el cliente reconecta; `authorize` sigue funcionando.
- 3 usuarios simultáneos del mismo suscriptor → límite de devices + concurrencia del ZSET.
- Heartbeat timeout (3 min sin latir) → el ZSET expira y corta.

### P1.2 · Concurrencia atómica (`NX-CONC`)
El check-and-add del ZSET de conexiones no es atómico → riesgo de exceder `max_connections` bajo carrera.
→ Mover a **script Lua** en Redis. **AC:** nunca excede el límite bajo concurrencia; 0 falsos 409 en zapping.

### P1.3 · Entorno de STAGING real (hoy NO existe)
Producción se validó **directo contra prod** porque nunca hubo staging. El runbook `deploy/RUNBOOK_STAGING_P0.md` está escrito pero **sin ejecutar**.
- Servidor `staging.nexoraplay.net` (2.25.68.163, Ubuntu 24.04) ya provisto; **ZeroTier instalado y unido** a la red `633e31d8a2cf3c84`.
- 🔴 **Bloqueado:** el nodo `4c3f6acbc9` está en `ACCESS_DENIED` → hay que **autorizarlo en el controller self-hosted** (`633e31d8a2` @ 35.209.188.59), no en ZeroTier Central.
- Luego: levantar stack aislado (Postgres/Redis/api/nginx propios) para probar flags **antes** de prod.

---

## 🟡 P2 — Observabilidad y resiliencia

### P2.1 · Observabilidad extendida (`NX-MON`, Fase 4 · Bloque 4)
- Contador `playback_failures` (incrementar cuando el gate devuelve 401/403).
- **Alertas de stream/nodo caído** (tarea periódica sobre `get_stream_status()`), incl. co-main.
- `/api/admin/streams` mejorado: `is_active` (DB) vs `alive` (Flussonic).
- Logs estructurados con `correlation_id` por request (structlog).
- Métricas Prometheus/OTel.

### P2.2 · Multi-Flussonic Registry + failover (`NX-FLU`, Fase 4 · Bloque 5)
Hoy `get_flussonic_node_client()` es un stub y el `.env` lista los nodos a mano.
→ `app/integrations/flussonic_registry.py` formal (`node_id`, `base_url`, `region`, `priority`, `is_healthy`) + health check periódico + **failover de nodo**. Sin geo-routing todavía.
**AC:** failover de nodo probado (relevante ya, por co-main).

---

## 🟡 P2b — Seguridad restante del MVP (completa M1/M2)

| ID | Pendiente | AC |
|---|---|---|
| `NX-DEV` | **Identidad fuerte de device**: `device_secret`/cert, handshake HMAC, device nuevo = `pending` (hoy el login **auto-registra** en silencio), rate-limit de re-binding | Device sin secret válido no obtiene token; MAC sola es insuficiente |
| `NX-AUTH` | Verificar/forzar **Argon2id**, lockout por N fallos, **auditoría de login admin**, (MFA opcional) | Credenciales malas → 401; login admin auditado |
| `NX-AUDIT` | `audit_log` **inmutable/append-only** + particionado + retención | Consulta filtrable; no se puede alterar |
| `NX-PARENTAL` | Control parental **PIN server-side** (`channels.censored`) | Canal adulto sin PIN → 403 |

---

## 🟢 P3 — Fase 2 (crecimiento de producto)

- `NX-EPG` — **EPG real** (hoy es mock en `catalog.py`): ingest async + cron, sin duplicados, sin XXE.
- `NX-RBAC` — RBAC admin + **resellers** (aislamiento por tenant).
- `NX-NOTIF` — comandos/eventos push al device (entregados en el heartbeat, con ack).
- **Normalizar entitlements a paquetes** (`packages`/`plan_packages`/`package_contents`). _Divergencia consciente:_ hoy se resolvió con `plan_channels` (005), más simple. Solo si el producto lo exige.

---

## 🟢 P4 — Fase 3 (escala y monetización)

`NX-VOD` (VOD/series) · `NX-CATCHUP` (timeshift/DVR) · `NX-BILL` (billing idempotente) · `NX-ASTRA` (adapter Astra) · `NX-XC` (XtreamCompat read-only) · `NX-CDN` (multi-región/CDN) · `NX-IAC` (IaC + DR).

**`NX-APPS` (Android TV / Mobile / iOS) está BLOQUEADO** por restricción del proyecto: no se empieza hasta que **playback, sesiones y observabilidad** estén estables (⇒ requiere P0 + P1 + P2.1 cerrados).

---

## Orden recomendado (grafo de dependencias)

```
P0.3 versionado (PR #9 + push)          ← barato, hazlo ya
P0.1 IP-binding soft ──► strict          ← cierra M1
P0.2 hardening del grant                 ← cierra M1 (código + tests)
P0.4 alerta co-main ──────┐
                          ├──► P2.2 Flussonic registry + failover
P1.3 STAGING real ────────┤              (desbloquea probar flags sin riesgo)
                          └──► P1.1 stress + P1.2 Lua concurrencia
                                   │
                                   └──► P2.1 observabilidad extendida
                                              │
                                              └──► P2b seguridad MVP (NX-DEV/AUTH/AUDIT)
                                                        │
                                                        └──► P3 (EPG, RBAC) ──► P4 (VOD, billing, APPS)
```

**Regla dura:** nada de apps nativas hasta cerrar P0+P1+P2.1.

---

## Riesgos abiertos

| Riesgo | Estado / mitigación |
|---|---|
| Revocación no corta streams en curso (grant auto-renovable) | **Abierto** → P0.2 |
| `strict` IP-binding rompe clientes móviles | Mitigado: escalonar `off → soft → strict` con observación (P0.1) |
| El fix real de Nginx solo vive en el servidor | **Abierto** → mergear PR #9 (P0.3) |
| No hay staging → los flags se prueban en producción | **Abierto** → P1.3 |
| Fuentes IPTV externas caídas (co-main) | Conocido → alertas + failover (P0.4 / P2.2) |
| Carrera en el ZSET puede exceder `max_connections` | **Abierto** → P1.2 (Lua) |
