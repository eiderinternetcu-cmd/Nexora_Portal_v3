# 00 — Resumen Ejecutivo · Nexora "Best-of"

> Síntesis de tres auditorías defensivas (solo lectura) destiladas en el diseño final de Nexora.
> Fuentes: **A** = XtreamUI 2.93 (panel admin PHP real) · **B** = Xtream-UI R22F/CKMOD41 (instalador) · **C** = Ministra/Stalker 5.6.10 (portal MAG completo).
> Regla rectora: **se extrae el conocimiento del dominio, NO el código**. Nada de legacy, nada de nulled, nada de license-bypass. Secretos enmascarados.

---

## 1. Veredicto en una frase

Las tres plataformas legacy resuelven **el mismo problema de negocio** (OTT/IPTV: suscriptores, dispositivos, paquetes, catálogo, EPG, VOD, playback, billing) con **el mismo conjunto de errores estructurales** (secretos reversibles, hashing débil, identidad por MAC, credenciales/tokens en URL, autorización no centralizada, estado en BD compartida). Nexora **reescribe desde cero** conservando el **modelo de dominio probado** y cerrando cada vulnerabilidad de raíz.

## 2. Qué aporta cada auditoría al diseño

| Fuente | Su mayor valor para Nexora |
|---|---|
| **A · Xtream 2.93** | Modelo de datos real (líneas, bouquets, streams, conexiones), flujo de **auth admin** leído, separación control-plane/data-plane, contrato compatible con clientes Xtream. |
| **B · R22F/CKMOD41** | **Blueprint de despliegue**: puertos, tmpfs/FFmpeg, tuning BD, alta de load balancers y, sobre todo, la lista de **errores de supply-chain/infra** a no repetir (IaC, firma de artefactos, secretos). |
| **C · Ministra** | El insumo **funcionalmente más rico**: flujos reales de STB handshake, **entitlements**, **generación de enlaces** (y su IDOR), **catálogo de adapters de tokenización por proveedor**, catch-up por EPG, tarifa→paquete→servicio, watchdog, billing OSS, y un esquema de BD real (277 migraciones). |

## 3. Los 7 principios de diseño de Nexora (derivados del consenso de las 3)

1. **Autorización centralizada y server-side.** *Toda* emisión de enlace pasa por un **Playback Authorization Service** (cierra el IDOR de C).
2. **Sin credenciales ni secretos en URL / código / disco / BD.** JWT + **signed URLs HMAC-SHA256** de vida corta; secretos en Vault/env.
3. **Identidad fuerte de dispositivo.** `device_id` + `device_secret`/cert, **no** MAC sola; sin auto-provisión silenciosa.
4. **Estado efímero en Redis, verdad declarativa en PostgreSQL.** Concurrencia atómica (`INCR/DECR`+TTL), nunca `COUNT(*)`.
5. **Control-plane ≠ data-plane**, comunicados por API autenticada (mTLS/JWT de servicio); origen real de stream **oculto** tras el edge.
6. **Media server gestionado** (Flussonic/Astra) en vez de FFmpeg+Nginx artesanal; estado por métricas, no por BD compartida.
7. **Integridad y trazabilidad:** FKs/constraints reales, RBAC, **auditoría inmutable**, rate-limiting en todo endpoint público, hashing **Argon2id**.

## 4. Lo que Nexora ya tiene hoy (no reinventar)

El backend `nexora_api` (FastAPI) ya implementa parte del diseño objetivo:

- ✅ Client API + Admin API + STB API separadas (`/api/client`, `/api/admin`, `/api/stb`, `/api/v1`).
- ✅ Auth cliente y admin con **JWT access + refresh en Redis** (rotación), login de suscriptor con auto-registro de dispositivo.
- ✅ Modelos `subscriber`, `plan`, `subscription`, `device`, `session`, `channel`, `audit`.
- ✅ **Playback Authorization** (`StreamAuthService.authorize`) + **concurrencia atómica en Redis** (ZSET `nexora:active_conns:{sub}`) + sesión IPTV en PostgreSQL.
- ✅ **Stream backend-auth** endpoint (`/api/stb/auth/validate`) para validar tokens de playback (base del Stream Token Service).
- ✅ Cliente **Flussonic read-only** multi-nodo (ec-main, co-main) con `_WriteBlocker`.
- ✅ Observabilidad base (`/api/admin/metrics`, `/sessions/live`, `/nodes/health`) + limpieza de sesiones zombie.
- ✅ Nginx reverse-proxy HTTPS con rutas `/stream/*` (resuelve mixed-content; origen oculto).

> El backlog (doc 11) marca explícitamente **"ya hecho" vs "por construir"** para que Nexora no reimplemente lo existente.

## 5. Lo que falta para completar el diseño "best-of"

- **Signed URLs activas de extremo a extremo** (HMAC con IP/sesión/TTL validado en el edge Flussonic) — hoy el endpoint existe pero el backend-auth no está activado.
- **Identidad de dispositivo reforzada** (`device_secret`/cert + handshake firmado), hoy es más laxa.
- **Modelo de entitlements normalizado** (`plan_channels`/`package_contents` con FK) — hoy el plan es simple.
- **EPG real**, **VOD/Series**, **catch-up**, **Billing**, **Multi-Flussonic Registry formal**, **RBAC admin completo**, **auditoría inmutable** y **monitoreo extendido**.

## 6. Riesgo #1 a no repetir (de las tres)

**Bypass de autorización en reproducción (IDOR).** En Ministra, `Itv::createLink()` emite enlace de cualquier canal **sin** validar suscripción, control parental ni estado del dispositivo. Nexora lo cierra haciendo que **el único camino a una URL reproducible** sea `POST /play/authorize`, que valida suscripción + entitlement + parental + dispositivo + concurrencia **antes** de firmar la URL.

## 7. Índice de entregables

| Doc | Contenido |
|---|---|
| [01_MEJORES_IDEAS_EXTRAIDAS.md](01_MEJORES_IDEAS_EXTRAIDAS.md) | Conceptos a conservar de cada auditoría |
| [02_LO_QUE_NO_DEBEMOS_COPIAR.md](02_LO_QUE_NO_DEBEMOS_COPIAR.md) | Antipatrones y vulnerabilidades + mitigación |
| [03_ARQUITECTURA_FINAL_NEXORA.md](03_ARQUITECTURA_FINAL_NEXORA.md) | 18 servicios de dominio |
| [04_MODELO_DATOS_FINAL.md](04_MODELO_DATOS_FINAL.md) | Esquema PostgreSQL por dominios |
| [05_FLUJO_AUTH_FINAL.md](05_FLUJO_AUTH_FINAL.md) | Autenticación cliente/admin/device |
| [06_FLUJO_PLAYBACK_FINAL.md](06_FLUJO_PLAYBACK_FINAL.md) | Playback live/VOD/series firmado |
| [07_FLUJO_STB_MAG_COMPATIBLE.md](07_FLUJO_STB_MAG_COMPATIBLE.md) | STB/MAG handshake seguro |
| [08_FLUJO_EPG_VOD_SERIES.md](08_FLUJO_EPG_VOD_SERIES.md) | EPG, VOD, series, catch-up |
| [09_CONTROL_CONCURRENCIA_SESIONES.md](09_CONTROL_CONCURRENCIA_SESIONES.md) | Concurrencia atómica Redis |
| [10_SEGURIDAD_FINAL.md](10_SEGURIDAD_FINAL.md) | Política de seguridad (20 puntos) |
| [11_BACKLOG_IMPLEMENTACION.md](11_BACKLOG_IMPLEMENTACION.md) | Backlog MVP/F2/F3 |
| [12_ROADMAP_PRIORIZADO.md](12_ROADMAP_PRIORIZADO.md) | Roadmap con dependencias |
| [13_DECISIONES_TECNICAS_ADR.md](13_DECISIONES_TECNICAS_ADR.md) | 10 ADR |
