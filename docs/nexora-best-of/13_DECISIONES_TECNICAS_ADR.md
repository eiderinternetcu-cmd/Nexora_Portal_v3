# 13 — Decisiones técnicas (ADR)

> Architecture Decision Records. Formato: **Decisión · Contexto · Alternativas evaluadas · Decisión final · Consecuencias · Riesgo · Plan de implementación**. Derivadas de las tres auditorías.

---

## ADR-001 — Backend en FastAPI (Python), no PHP legacy

- **Contexto:** A/C son monolitos PHP (panel 2.93; núcleo Ministra **ofuscado** y EOL PHP 7.0). Difíciles de auditar, sin tipos, sin tests.
- **Alternativas:** seguir en PHP (Laravel/Symfony); Node/NestJS; Go; **FastAPI**.
- **Decisión:** **FastAPI** + Pydantic + SQLAlchemy.
- **Consecuencias:** tipado, validación declarativa, OpenAPI autogenerado, async; ecosistema Python para workers (EPG/billing).
- **Riesgo:** rendimiento CPU-bound → mitigar con workers y media server externo (no procesar video en la app).
- **Plan:** ya adoptado en `nexora_api`. Restricción del proyecto: **no PHP en módulos nuevos**.

## ADR-002 — PostgreSQL, no MySQL/MariaDB MyISAM legacy

- **Contexto:** A usa MariaDB monolítica con JSON en TEXT; C usa **MyISAM** (sin FKs, sin transacciones, crash-unsafe), CSV en TEXT, `status` invertido.
- **Alternativas:** MySQL/InnoDB; MongoDB; **PostgreSQL**.
- **Decisión:** **PostgreSQL 16** con FK/constraints, enums, `jsonb` selectivo, RLS multi-tenant, particiones por fecha en logs.
- **Consecuencias:** integridad referencial real, transacciones, particionado nativo, RLS para resellers.
- **Riesgo:** curva de migración de datos legacy → ETL idempotente con reconciliación.
- **Plan:** ya adoptado; migraciones Alembic. Restricción: **no MySQL en módulos nuevos**; **psycopg, no asyncpg**.

## ADR-003 — Redis para concurrencia y sesiones, no `COUNT(*)` en BD

- **Contexto:** A cuenta conexiones con `COUNT(user_activity_now)` → **carrera** (no atómico). C limita duración solo en cliente.
- **Alternativas:** contar en BD; locks de BD; **Redis** atómico.
- **Decisión:** **Redis** ZSET con TTL (`INCR/DECR`/`ZADD/ZCARD/ZREM`), idealmente en script Lua.
- **Consecuencias:** límite exacto bajo concurrencia; liberación por TTL si el player cae; tolera zapping.
- **Riesgo:** Redis caído → degradación; mitigar con persistencia + PG como auditoría; authorize re-puebla.
- **Plan:** ya implementado (`ConnectionService`); pendiente envolver en Lua para atomicidad total.

## ADR-004 — Signed playback URLs (HMAC), no credenciales en URL

- **Contexto:** A pone `username/password` en cada URL del M3U; C firma con MD5 y secretos default (`supersecret`), sin IP-binding y TTL de 8 h.
- **Alternativas:** credenciales en URL (legacy); cookies de sesión en el edge; **signed URLs HMAC**.
- **Decisión:** **HMAC-SHA256** ligado a content+node+**IP**+sesión+**TTL corto**, secretos en Vault rotables; validado en el edge (Flussonic backend-auth).
- **Consecuencias:** sin credenciales expuestas; tokens no compartibles; replay acotado.
- **Riesgo:** fuga de secreto → rotación + secreto por edge; reloj desincronizado → usar tiempo de servidor.
- **Plan:** validador `/api/stb/auth/validate` ya existe; falta firmar la URL + activar backend-auth + IP-binding.

## ADR-005 — Separación admin / client / stb (superficies distintas)

- **Contexto:** legacy mezcla panel, player API y portal; el panel 2.93 ofusca la ruta admin por "security by obscurity" (8080/`<access_code>`).
- **Alternativas:** API única; ofuscación de rutas; **routers separados con auth distinta**.
- **Decisión:** `/api/admin`, `/api/v1` (admin/compat), `/api/client`, `/api/stb` con tokens y permisos propios.
- **Consecuencias:** menor superficie cruzada; un token de cliente no abre admin; RBAC claro.
- **Riesgo:** duplicación de lógica → compartir servicios de dominio, no endpoints.
- **Plan:** ya adoptado en `nexora_api`. Restricción: **todo flujo nuevo pasa por Client API**.

## ADR-006 — No copiar `player_api` legacy tal cual (compat opcional, read-only)

- **Contexto:** el contrato Xtream es popular pero arrastra credenciales-en-URL, enumeración de IDs, sin rate-limit.
- **Alternativas:** clonar `player_api.php`; ignorar el ecosistema; **adaptador de compatibilidad seguro**.
- **Decisión:** Client API propia (JWT + signed URLs); **opcional** `XtreamCompatService` read-only que traduce el contrato sin reintroducir credenciales en URL.
- **Consecuencias:** alcance de mercado sin heredar vulnerabilidades; IDs opacos (UUID).
- **Riesgo:** mantener compat añade superficie → mantenerlo read-only y detrás del mismo authorize.
- **Plan:** Fase 3 (NX-XC).

## ADR-007 — Flussonic/Astra como motor de streaming, no FFmpeg+Nginx artesanal

- **Contexto:** A/B usan FFmpeg propio + Nginx parcheado + tmpfs 90% RAM (OOM, no escala, posible inyección en FFmpeg). C integra múltiples motores.
- **Alternativas:** FFmpeg+Nginx propio; solo un motor; **Flussonic/Astra gestionados** con adapters.
- **Decisión:** **media server gestionado** (Flussonic ya integrado, Astra como segundo motor) vía `StreamProvider` adapters; estado en Redis, no BD.
- **Consecuencias:** DVR/catch-up/LL-HLS gestionados; escalado horizontal; Nexora **no** procesa video.
- **Riesgo:** dependencia de proveedor → abstracción por adapter + segundo motor (Astra).
- **Plan:** cliente Flussonic read-only ya existe; registry formal + Astra en F2/F3.

## ADR-008 — No exponer la IP/origen real del stream

- **Contexto:** A revela el origen en el M3U (re-stream trivial); C expone `rtp/udp` sin token.
- **Alternativas:** exponer origen; VPN entre cliente y origen; **edge + URLs firmadas**.
- **Decisión:** origen siempre **detrás del edge**; cliente solo ve `https://dominio/stream/<node>/...` firmado.
- **Consecuencias:** anti-hotlink/anti-restream; same-origin HTTPS (sin mixed-content).
- **Riesgo:** el proxy debe escalar → cache en edge/CDN.
- **Plan:** `/stream/*` ya implementado (resuelve mixed-content y oculta origen).

## ADR-009 — Nginx `/stream/*` HTTPS como borde de reproducción

- **Contexto:** el player en HTTPS bloquea HLS servido por `http://IP:puerto` (mixed-content); credenciales/origen no deben viajar.
- **Alternativas:** servir HLS directo del origen; CDN externa desde el día 1; **proxy Nginx `/stream/*` HTTPS**.
- **Decisión:** Nginx reverse-proxy con rutas `/stream/ec-main/*` y `/stream/co-main/*` por HTTPS same-origin.
- **Consecuencias:** player reproduce sin bloqueos; base para insertar firma/validación en el borde.
- **Riesgo:** punto único → escalar/replicar Nginx; preparar CDN.
- **Plan:** ya en producción (`deploy/nginx/nexoraplay.conf`).

## ADR-010 — Auditar y autorizar TODO playback en un punto central

- **Contexto:** el riesgo #1 (Ministra IDOR) viene de que `createLink` no valida entitlements; el gate vivía solo en las listas.
- **Alternativas:** validar en cada endpoint (disperso, frágil); confiar en el cliente; **un único PlaybackAuthorizationService**.
- **Decisión:** **toda** emisión de URL pasa por `authorize` (suscripción+entitlement+parental+device+concurrencia) y queda **auditada**.
- **Consecuencias:** elimina la clase entera de IDOR; trazabilidad completa de quién reprodujo qué.
- **Riesgo:** punto crítico de rendimiento → cachear entitlements en Redis con invalidación.
- **Plan:** `StreamAuthService.authorize` ya es el punto central; añadir entitlement por paquete + parental + registro en `audit_log`.

---

## Tabla resumen

| ADR | Decisión | Estado en Nexora |
|---|---|---|
| 001 | FastAPI | ✅ |
| 002 | PostgreSQL | ✅ |
| 003 | Redis concurrencia | ✅ (mejorar Lua) |
| 004 | Signed URLs HMAC+IP | 🟡 (activar) |
| 005 | Separación admin/client/stb | ✅ |
| 006 | XtreamCompat opcional | ⬜ F3 |
| 007 | Flussonic/Astra | ✅ Flussonic / ⬜ Astra |
| 008 | Origen oculto | ✅ |
| 009 | Nginx /stream/* HTTPS | ✅ |
| 010 | Authorize central + audit | ✅🟡 |
