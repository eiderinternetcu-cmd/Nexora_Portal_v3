# 06 — Flujo final de playback (resumen)

> **Resumen ejecutivo.** La investigación profunda (comparativa legacy, diagnóstico live, 12 submódulos, endpoints, modelo PG, Redis, algoritmo de concurrencia, reglas de signed URL, diseño Nginx `/stream/*`, Flussonic/Astra, backlog PLAYBACK-001..030, 28 pruebas) está en **[modules/02_PLAYBACK_DEEP.md](modules/02_PLAYBACK_DEEP.md)**. Diagnóstico operativo: **[modules/02_PLAYBACK_LIVETV_DIAGNOSTIC.md](modules/02_PLAYBACK_LIVETV_DIAGNOSTIC.md)**.

---

## Hallazgos críticos

1. **El playback funciona técnicamente** (verificado en producción): `authorize → playback_url HTTPS → manifest HLS 200` (1080p, same-origin, sin IP origen, sin mixed-content).
2. **Causa actual de "no se ven canales":** el **límite de 5 dispositivos bloqueaba el login completo con HTTP 400**. Mitigado liberando devices de diagnóstico; **login desde equipo nuevo → 200** (evidencia en el diagnóstico).
3. **Decisión final:** **desacoplar el login del device cap** — `/api/client/auth/login` **no** debe fallar por límite de dispositivos (devuelve tokens + flag `device_registration`).
4. **`/api/client/devices/register` → 409 Conflict** al alcanzar el límite (hoy `400`).
5. **`/api/client/playback/authorize` exige device registrado y activo** (no bloqueado, del suscriptor).
6. **Deuda crítica:** la `playback_url` **todavía no lleva `?token=`** → el manifest queda **público** vía proxy (anti-hotlink no aplicado).
7. **Decisión anti-hotlink:** **Nginx `auth_request` → FastAPI** (`/api/stb/auth/validate`), **sin tocar Flussonic** (read-only/producción).
8. **Prioridad siguiente:** **token obligatorio para `/stream/*`** (firmar URL + validar en el edge).

## Estado en Nexora (alto nivel)

| Capacidad | Estado |
|---|---|
| Authorize central, suscripción, concurrencia ZSET atómica, sesión IPTV PG+Redis, revocación al cancelar, HTTPS `/stream/*`, origen oculto, mixed-content resuelto | ✅ |
| Entitlement por canal (`plan_channels`), token en URL + auth_request, heartbeat/stop dedicados, device cap desacoplado, multi-nodo formal/failover | 🟡/⬜ |
| Astra, `stream_access_logs`, `playback_tokens` (hash), particionado de sesiones, CORS explícito | ⬜ |

## Flujo Live TV (resumen objetivo)
```
authorize(channel_key,device) → valida aud/type/jti · subscriber · suscripción · plan_channel ·
  device registrado+activo · concurrencia (Lua) → selecciona nodo → crea playback_session →
  token playback (60s, IP+session+node) → playback_url=https://.../stream/<node>/<stream>/index.m3u8?token=
player → GET → Nginx /stream/* → auth_request /api/stb/auth/validate (token+IP) → 200 proxy / 401 bloquea
heartbeat renueva · stop libera · suspensión revoca todo
```

## Concurrencia (resumen)
ZSET `concurrency:subscriber:{sub}` (member=device, score=exp); zapping renueva score (no duplica); límite `plan.max_connections`; heartbeat<TTL(180s); sin heartbeat→libera; envolver en **Lua** para atomicidad. → **[§8 del deep](modules/02_PLAYBACK_DEEP.md)**.

## Signed URL / token (resumen)
`type=playback_token`, `aud=nexora-playback`, TTL 30–90s, ligado a subscriber+device+channel+session+node+exp(+IP); no reutilizable; revocable por jti; nunca en claro en DB (solo hash); nunca completo en logs. → **[§9 del deep](modules/02_PLAYBACK_DEEP.md)**.

## Nginx /stream/* (resumen)
`auth_request` interno → `/api/stb/auth/validate` (valida token+IP); proxy_pass por nodo; Range/HLS; HTTPS; CORS explícito (no `*`); logs sin token; cache corta de validación por jti para no penalizar segmentos. → **[§10 del deep](modules/02_PLAYBACK_DEEP.md)**.

## Backlog y pruebas
- **PLAYBACK-001..030** (AC/rollback): **[§13 del deep](modules/02_PLAYBACK_DEEP.md)**.
- **28 casos de prueba** + checklist de aceptación: **[§14–15 del deep](modules/02_PLAYBACK_DEEP.md)**.

---

> Tablas (`channels`, `stream_sources`, `stream_nodes`, `flussonic_nodes`, `astra_nodes`, `plan_channels`, `playback_sessions`, `playback_tokens`, `stream_access_logs`, `node_health_checks`, `playback_revocations`) y Redis keys (`playback:token`, `playback:session`, `concurrency:*`, `stream_node:*`, `rate:playback_authorize*`): **[§6–7 del deep](modules/02_PLAYBACK_DEEP.md)**.
