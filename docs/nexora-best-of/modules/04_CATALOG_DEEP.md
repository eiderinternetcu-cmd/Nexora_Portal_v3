# Módulo 4 — Catálogo / Canales / Categorías / EPG / Stream Sources (investigación profunda)

> Investigación defensiva (solo lectura del legacy) + estado real de `nexora_api`. Sin copiar código legacy, sin secretos. Estado: ✅ existe · 🟡 parcial · ⬜ por construir.
> **Lote 1** = §1–§7 (estado actual, comparativa, modelo, reglas, integración, riesgos, decisiones). **§8 (servicios detallados), backlog, pruebas y checklist → Lote 2 tras revisar el modelo.** PR #1 **congelado**; este módulo no toca código.

---

## 1. Estado actual Nexora

| Pieza | Realidad | Estado |
|---|---|---|
| `channels` | id·channel_key UNIQUE·number·name·**category (String, no FK)**·logo_url·**stream_key (interno)**·flussonic_node (String)·hls_path·source_type·source_url·epg_id (String, **sin uso**)·is_active·requires_subscription·created/updated | ✅ inline |
| `channel_categories` | **no existe** (category es texto libre en `channels`) | ⬜ |
| Stream source | **inline**: 1 sola fuente por canal (`stream_key`+`flussonic_node`+`hls_path`+`source_url`). Sin multi-calidad/multi-fuente | 🟡 |
| `stream_nodes` | **en `.env`** (`FLUSSONIC_BASE_URL`, `FLUSSONIC_CO_MAIN_BASE_URL`, …) + `channel.flussonic_node` (String). Sin tabla, sin health/failover | 🟡 |
| EPG | **MOCK** (`_MOCK_EPG` dict en `catalog.py`); `channel.epg_id` declarado pero sin uso | ⬜ |
| Logos | `channel.logo_url` (String, URL externa o null). Sin multi-resolución | 🟡 |
| Orden | `channel.number` (int, indexado); `list_active` ordena por number | ✅ |
| live/vod/series | solo **live**; `source_type` = manual/flussonic. **No hay VOD ni series** | ⬜ (VOD/series = módulo aparte) |
| Catálogo cliente | `GET /api/client/catalog/channels` → `ChannelPublic` (id·channel_key·number·name·category·logo_url·requires_subscription; **stream_key NO expuesto** ✅). Devuelve **todos los activos** (NO filtra por entitlement) | ✅🟡 |
| EPG cliente | `GET /api/client/catalog/channels/{key}/epg` → mock | 🟡 |
| Admin canales | `/api/admin/channels` (list/get/stream-status) **solo lectura**; sin CRUD write | 🟡 |
| Routing/playback | `_resolve_playback_url` usa `channel.flussonic_node` → `get_flussonic_node_client` → `stream_hls_url(stream_key, hls_path)` o `source_url` | ✅ |
| Entitlement por canal | `plan_channels` (Módulo 3) ↔ `channels.id`; el catálogo **no** lo usa todavía | 🟡 |
| Seed | `import_m3u_channels.py` (24 canales, UPSERT por channel_key; `source_url` de `FLUSSONIC_PUBLIC_*` o IP) · `seed_plan_channels.py` | ✅ |
| Cache | ninguna (catálogo va a DB en cada request) | ⬜ |

**Resumen:** el catálogo funciona para **live** con un modelo **plano** (todo en `channels`). Falta normalizar (categorías, fuentes/nodos), EPG real, logos multi-res, health y caché — y conectar el catálogo con el entitlement (`plan_channels`).

---

## 2. Comparativa legacy

| Tema | Xtream/XUI (A/B) | Ministra (C) | **Nexora actual** | Mejor idea | Decisión Nexora |
|---|---|---|---|---|---|
| Canal | `streams` (type=1 live, 2 movie) + estado en `streams_sys` | `itv` (id, name, number, censored, tv_genre_id, xmltv_id) | `channels` plano ✅ | C (canal lógico limpio) | mantener `channels` + normalizar |
| Categorías | `stream_categories`/`movie_categories` (FK) | `tv_genre` (FK) | **String** 🟡 | A/C (tabla FK) | **`channel_categories` + FK** |
| Canal↔plan/bouquet | `users.bouquet` (JSON de ids) | `services_package`/`service_in_package` | `plan_channels` (FK) ✅ | Nexora | usar `plan_channels` en catálogo |
| Stream URLs | `clients_live.php` (creds en URL); `streams_sys` PID | **`ch_links`** (N URLs por canal, priority, ua_filter) + `ch_link_on_streamer` | inline (1 fuente) 🟡 | **C (canal→links→streamer)** | `stream_sources` + `channel_stream_sources` (F2) |
| Nodos/LB | `streaming_servers` (status/total_clients/latency/geo) | `streaming_servers` (address, max_sessions) + `stream_zones` | `.env` + String 🟡 | A/C (tabla de nodos) | **`stream_nodes`** (registry formal) |
| EPG/XMLTV | `epg`(fuentes)+`epg_data`(programas); `xmltv.php` | `epg`(programas, KEY ch_id_time)+`epg_setting`(uri, etag, id_prefix, lang); `xmltv_id` por canal | **mock** ⬜ | C (xmltv_id + id_prefix + dedup) | `epg_sources`+`epg_channels`+`epg_programs`+`channel_epg_map` (F2) |
| Logos | `tvg-logo` en M3U / `stream_icon` | `itv.logo` | `logo_url` String 🟡 | — | `logo_url` MVP; `channel_logos` multi-res (F2) |
| Orden | `streams.order`/`num` | `itv.number` | `channel.number` ✅ | igual | mantener |
| live/vod/series | `streams type` + `series`/`series_episodes` | `itv` vs `video`/`video_season` | solo live | C (separación clara) | live aquí; **VOD/series = módulo aparte** |
| Health/estado | `streams_sys.pid` + `pid_monitor.php` (polling BD) | `monitoring_url`/`ch_links.status` | `get_stream_status` ad-hoc 🟡 | — | `channel_health_checks` + ChannelHealthService (F2) |
| M3U/Player API | `get.php?user=&pass=` (creds en URL) | n/a | Client API JWT ✅ | Nexora | nunca creds en URL; M3U firmado opcional |

---

## 3. Modelo de datos propuesto

> Prioridad: 🔴 MVP · 🟠 Fase 2. Reconciliado con `channels` actual (no romper playback). Migración Alembic dedicada (006+), **separada del PR #1 (congelado)**.

### 🔴 MVP

**`channel_categories`** ⬜
`id uuid PK · key citext UNIQUE (peliculas, deportes, …) · name · sort_order int · is_adult bool · created_at`
- `channels.category_id uuid FK → channel_categories` (nullable durante transición; se mantiene `category` String como legacy hasta migrar). Legacy: `stream_categories`/`tv_genre`.

**`stream_nodes`** 🟡→⬜ (formalizar `.env`)
`id · node_id citext UNIQUE (ec-main, co-main) · name · engine enum(flussonic,astra) · base_url · public_base_url (proxy HTTPS) · region · priority int · max_sessions int · is_healthy bool · created_at`
- **Sin secretos** (credenciales en Vault/env). `channels.flussonic_node` (String) → `channels.node_id FK → stream_nodes` (transición). Legacy: `streaming_servers`. Lo necesita el failover del Módulo 2.

**`channels`** ✅ (ajustes)
Mantener; añadir `category_id FK` ⬜ y `node_id FK` ⬜ (conviven con los String actuales en transición). Resto igual. **`content_type` enum(live)** reservado para futura separación (default live).

### 🟠 Fase 2

**`stream_sources`** ⬜ — una fuente reproducible
`id · node_id FK · stream_key · hls_path · protocol enum(hls,dash) · quality enum(auto,sd,hd,fhd) · is_active · created_at`
- Reemplaza el `stream_key`/`hls_path` inline cuando haya **multi-calidad/multi-fuente**. Legacy: `ch_links`.

**`channel_stream_sources`** ⬜ — M:N canal ↔ fuente
`channel_id FK · stream_source_id FK · priority int · ua_filter NULL · PK(channel_id, stream_source_id)`
- Permite varias fuentes por canal (calidad/fallback). Legacy: `ch_link_on_streamer`.

**`epg_sources`** ⬜ — feeds XMLTV
`id · uri · id_prefix · lang_code · etag NULL · enabled bool · last_run_at · created_at`. Legacy: `epg_setting`.

**`epg_channels`** ⬜ — ids XMLTV de la fuente
`id · source_id FK · xmltv_id · display_name · icon_url NULL · UNIQUE(source_id, xmltv_id)`.

**`channel_epg_map`** ⬜ — canal ↔ xmltv_id
`channel_id FK · epg_channel_id FK · PK(channel_id, epg_channel_id)`. Seed: `channel.epg_id` (String) → map. Legacy: `itv.xmltv_id`.

**`epg_programs`** ⬜ **(particionada por fecha)**
`id · epg_channel_id FK · start_at · end_at · title · descr · lang · **UNIQUE(epg_channel_id, start_at)**` (cierra duplicados de C). Índice `(epg_channel_id, start_at)`. Legacy: `epg`/`epg_data`.

**`channel_logos`** ⬜ — logos multi-resolución
`id · channel_id FK · url · width int · height int · is_default bool`. MVP usa `channels.logo_url`.

**`channel_health_checks`** ⬜ **(particionada)**
`id · channel_id FK · node_id FK · alive bool · client_count int · latency_ms int · checked_at`. Alimenta ChannelHealthService + Monitoring (Módulo 2). Legacy: `streams_sys`/`monitoring_url`.

### Servicios (mapa; detalle en Lote 2)
| Servicio | Responsabilidad | Estado |
|---|---|---|
| ChannelCatalogService | listar/CRUD canales, filtrar por entitlement, orden | ✅(ChannelService)🟡 |
| ChannelCategoryService | CRUD categorías + asignación | ⬜ MVP |
| StreamNodeService | registry de nodos (de `.env` a DB) + salud | 🟡→MVP |
| StreamSourceService | fuentes por canal (multi-calidad/fallback) | ⬜ F2 |
| EPGSourceService | CRUD fuentes XMLTV | ⬜ F2 |
| EPGImportService | worker ingest async (sin XXE, dedup, cron) | ⬜ F2 |
| ChannelHealthService | health por canal/nodo (read-only Flussonic) | ⬜ F2 |
| CatalogCacheService | caché Redis del catálogo + invalidación | ⬜ F2 |

---

## 4. Reglas de negocio

1. **`stream_key` jamás al cliente** — el catálogo expone solo `channel_key` (+ logo/categoría/número). ✅ (mantener).
2. **Catálogo = solo canales `is_active`**; orden por `number` (luego categoría/favoritos en F2).
3. **Catálogo cliente = solo canales AUTORIZADOS por default (APROBADO).** `GET /api/client/catalog/channels` lista únicamente los canales del plan activo (entitled). Para ver los bloqueados: `GET /api/client/catalog/channels?include_locked=true` → cada canal trae `entitled` (bool), `locked` (bool) y `reason_code` (si aplica, p.ej. `CHANNEL_NOT_INCLUDED`). **Admin** (`/api/admin/channels`) ve **todos** sin filtro. El catálogo **no es autoridad**: el playback revalida (regla 13).
4. **Parental:** categoría `is_adult` / canal `censored` → requiere PIN server-side en playback (no se oculta del catálogo salvo preferencia). (F2 con VOD.)
5. **Una categoría por canal** (MVP, `category_id`); multi-género opcional F2.
6. **Multi-fuente (F2):** un canal puede tener varias `stream_sources` (calidad/fallback) con `priority`; selección en playback/FlussonicIntegration.
7. **Nodos read-only:** `stream_nodes` describe nodos; **nunca** se modifica Flussonic. `is_healthy` lo actualiza ChannelHealthService.
8. **EPG (F2):** ingest async con parser **seguro** (sin XXE), límite de tamaño/tiempo, **dedup por `UNIQUE(epg_channel_id,start_at)`**, mapeo por `xmltv_id`+`id_prefix`; servido filtrado por entitlement y cacheado.
9. **Logos:** `logo_url` MVP; si hay `channel_logos`, elegir `is_default`/resolución.
10. **Caché (F2):** el catálogo y las categorías se cachean en Redis con **invalidación** al editar (admin) o al cambiar estado/entitlement; PG es la fuente de verdad.
11. **Compat M3U/Xtream (opcional):** si se ofrece M3U, **sin credenciales en URL**, con signed URLs y logos `tvg-logo`.
12. **VOD/series fuera de alcance** de este módulo (módulo aparte); `content_type` reservado.
13. **El catálogo NO es autoridad de seguridad:** `PlaybackAuthorizationService` revalida con `EntitlementService.can_watch_channel` **antes** de generar `playback_url`. Un canal listado como `entitled` puede igualmente ser denegado en playback (suscripción vencida, concurrencia, etc.).
14. **Admin sin filtro de entitlement:** `/api/admin/channels` lista **todos** los canales (activos e inactivos) sin aplicar `plan_channels`.

---

## 5. Integración con `plan_channels` y playback

- **Entitlement (lectura):** el catálogo anota por canal `entitled = canal ∈ plan_channels(plan activo del suscriptor)` con **una query** (join `subscriptions`→`plan_channels`). **Default = solo `entitled`**; con `?include_locked=true` se listan también los bloqueados con `entitled`/`locked`/`reason_code`. No duplica la lógica de autorización: solo la **refleja** para la UI.
- **Playback (autoridad):** `PlaybackAuthorizationService`/`EntitlementService.can_watch_channel` (Módulo 3) sigue siendo el **único** que decide; el catálogo nunca autoriza.
- **Stream source/node:** `_resolve_playback_url` hoy usa `channel.flussonic_node`+`stream_key`+`hls_path`. Con `stream_nodes` (MVP) pasa a `channel.node_id`; con `stream_sources` (F2) elige la mejor fuente por `priority`/calidad. El **playback token** (Módulo 2) ya liga `stream_key`+`node` → el catálogo debe entregar el `node_id`/source correcto para que el token y el grant de segmentos casen.
- **EPG (F2):** `channel_epg_map` conecta canal↔programas; el cliente pide EPG por `channel_key`; se filtra por entitlement y se cachea.
- **Caché:** invalidar caché de catálogo al editar `plan_channels` (un canal nuevo en el plan cambia `entitled`).

---

## 6. Riesgos

### 🔴 Crítico
| Riesgo | Origen | Mitigación |
|---|---|---|
| Exponer `stream_key`/`source_url` (origen) al cliente | A (creds en URL/M3U) | catálogo solo `channel_key`; nunca source/stream_key ✅ |
| EPG ingest con **XXE/SSRF/DoS** (feed gigante) | C (sin límites) | parser seguro sin entidades externas, límite tamaño/tiempo, lista blanca de `uri` |

### 🟠 Alto
| Riesgo | Origen | Mitigación |
|---|---|---|
| `category` como String libre (typos, sin integridad) | Nexora actual | `channel_categories` + FK |
| Nodos en `.env` sin health/failover | Nexora | `stream_nodes` + ChannelHealthService |
| EPG sin `UNIQUE` → duplicados en concurrencia | C | `UNIQUE(epg_channel_id,start_at)` + partición |
| Source/node del catálogo no casa con el token/grant de segmentos | integración M2 | el catálogo debe entregar `node_id`/source que coincida con lo que firma el playback |

### 🟡 Medio
| Riesgo | Mitigación |
|---|---|
| Catálogo a DB en cada request (escala) | CatalogCacheService (Redis) F2 |
| `epg_id`/`flussonic_node`/`category` String quedan huérfanos tras normalizar | migración con backfill + transición (mantener String hasta migrar) |
| Path traversal si se sirven logos/archivos por path | servir por id/URL controlada, nunca por path de input |
| `logo_url` externos (mixed-content/HTTP) | normalizar a HTTPS / proxiar logos |

### 🔵 Bajo
| Riesgo | Mitigación |
|---|---|
| `pid_monitor`-style polling de BD (C/A) | health por métricas/Redis, no polling de BD |
| Enumeración de canales por número secuencial | `channel_key` opaco para acciones; número solo display |

---

## 7. Decisiones técnicas (para tu revisión antes del Lote 2)

1. **Normalización incremental, sin romper playback:** `channels` se mantiene; se añaden `category_id` y `node_id` (FK) que **conviven** con los String actuales durante la transición; backfill + luego deprecar los String.
2. **MVP del módulo:** `channel_categories` (FK) + `stream_nodes` (registry de `.env`→DB) + **filtro/anotación de entitlement en el catálogo** + CRUD admin de canales/categorías. (Cierra deudas inmediatas y habilita el failover del Módulo 2.)
3. **Fase 2:** `stream_sources`/`channel_stream_sources` (multi-calidad/fallback), **EPG real** (`epg_sources`/`epg_channels`/`channel_epg_map`/`epg_programs` particionada), `channel_logos`, `channel_health_checks`, `CatalogCacheService`.
4. **Entitlement en catálogo (APROBADO):** default = **solo autorizados** (`GET /catalog/channels`); `?include_locked=true` añade bloqueados con `entitled`/`locked`/`reason_code`. **Admin** ve todos sin filtro. El catálogo **no autoriza**; el playback revalida con `EntitlementService` antes de `playback_url`.
5. **Una categoría por canal** en MVP (`category_id`); multi-género a F2 si se necesita.
6. **`stream_nodes` sin secretos** (Vault/env); el catálogo entrega `node_id`/source que **debe casar** con el `stream_key`+`node` del playback token y el grant de segmentos (Módulo 2).
7. **VOD/series**: fuera de este módulo (módulo dedicado); `content_type` reservado en `channels`.
8. **Migración** en rama nueva (no el PR #1 congelado); Alembic 006+ con backfill idempotente.

---

## 8. Servicios detallados (Lote 2)

> Cada servicio: responsabilidad · endpoints · tablas · entradas/salidas · reglas · errores · prioridad. **Sin código** — diseño. Surface = `app/services/*` + `app/api/{client,admin,internal}/*`.

### 8.1 ChannelCatalogService 🔴 MVP (extiende `ChannelService`)
- **Responsabilidad:** listar/obtener canales, **filtrado por entitlement** (cliente), orden por `number`, y servir de base al CRUD admin.
- **Endpoints:**
  - Cliente: `GET /api/client/catalog/channels` → **default solo `entitled`**; `?include_locked=true` → todos los activos con `entitled`/`locked`/`reason_code`. `?category=<key>` opcional.
  - Cliente: `GET /api/client/catalog/channels/{channel_key}` → detalle (sin `stream_key`).
  - Admin: `GET /api/admin/channels` (todos, sin filtro entitlement) — ya existe en read-only.
- **Entrada:** `subscriber` (plan activo), filtros. **Salida:** `ChannelPublic` (+ `entitled`/`locked`/`reason_code` si `include_locked`).
- **Lógica entitlement:** una query `subscriptions(active) → plan_channels → channel_id`; set de ids autorizados; anota/filtra en memoria. `reason_code` = `CHANNEL_NOT_INCLUDED` (reusa códigos de `EntitlementService`, Módulo 3).
- **Reglas:** `stream_key`/`source_url` NUNCA expuestos; catálogo no autoriza (regla 13).
- **Errores:** 404 canal inexistente/inactivo (cliente, sin filtrar info); 401 sin sesión.
- **Depende de:** `EntitlementService` (códigos), `ChannelCategoryService` (nombre categoría).

### 8.2 ChannelCategoryService 🔴 MVP ⬜
- **Responsabilidad:** CRUD de `channel_categories`; asignación a canales; orden (`sort_order`).
- **Endpoints:**
  - Cliente: `GET /api/client/catalog/categories` → lista (key, name, sort_order, is_adult).
  - Admin: `GET/POST/PATCH/DELETE /api/admin/categories`.
- **Reglas:** `key` único (citext); no borrar categoría con canales (o reasignar a `null`/“sin categoría”); `is_adult` marca parental (playback exige PIN en F2).
- **Errores:** 409 key duplicada; 409 borrado con canales asociados; 404.

### 8.3 StreamNodeService 🔴 MVP 🟡→⬜
- **Responsabilidad:** registry de nodos (de `.env` a `stream_nodes`), exponer `node_id`→base/public URL al resolver playback, marcar `is_healthy`.
- **Endpoints:** Admin `GET /api/admin/streams/nodes` (sin secretos), `POST/PATCH` (alta/edición node_id, urls, priority, max_sessions). Interno: `get_node(node_id)` usado por `_resolve_playback_url`.
- **Reglas:** **sin credenciales** en respuestas; `node_id` citext único; `public_base_url` HTTPS. El `node_id` entregado al playback **debe casar** con el `node` del token y el grant de segmentos (Módulo 2).
- **Errores:** 404 node; 409 node_id duplicado.

### 8.4 StreamSourceService 🟠 F2 ⬜
- **Responsabilidad:** fuentes reproducibles por canal (`stream_sources` + `channel_stream_sources`), selección por `priority`/calidad, base de multi-source/fallback.
- **Endpoints:** Admin CRUD de fuentes y asignación canal↔fuente. Interno: `best_source(channel, ctx)`.
- **Reglas:** una fuente “primaria” por canal (compat con `channel.stream_key` actual durante transición); `ua_filter` opcional.

### 8.5 EPGSourceService 🟠 F2 ⬜
- **Responsabilidad:** CRUD de feeds XMLTV (`epg_sources`: uri, id_prefix, lang, etag, enabled).
- **Reglas:** **lista blanca de `uri`** (anti-SSRF); `id_prefix` para desambiguar ids entre fuentes.

### 8.6 EPGImportService 🟠 F2 ⬜ (worker async + cron)
- **Responsabilidad:** descargar + parsear XMLTV → `epg_channels`/`epg_programs`; mapear por `xmltv_id` (`channel_epg_map`); dedup.
- **Reglas de seguridad:** parser **sin entidades externas (anti-XXE)**, límite de tamaño/tiempo, streaming parse; **dedup por `UNIQUE(epg_channel_id,start_at)`** (UPSERT); respetar `etag`/`If-Modified-Since`; cron desacoplado (no en request).
- **Errores:** feed inválido/oversize → abortar fuente, no la corrida; registrar `last_run_at`/estado.

### 8.7 ChannelHealthService 🟠 F2 ⬜
- **Responsabilidad:** health por canal/nodo (lee `get_stream_status` de Flussonic **read-only**) → `channel_health_checks`; actualiza `stream_nodes.is_healthy`; alimenta Monitoring (Módulo 2) y futura selección de fuente.
- **Reglas:** **nunca** modifica Flussonic; muestreo por intervalo, no polling de BD.

### 8.8 CatalogCacheService 🟠 F2 ⬜
- **Responsabilidad:** caché Redis del catálogo/categorías/EPG; **invalidación** en edición admin, cambio de estado de canal o de `plan_channels`.
- **Reglas:** PG es la fuente de verdad; TTL + invalidación por evento; clave por (versión-catálogo) para invalidación masiva barata.

---

## 9. Backlog Módulo 4

> Orden = dependencias. **MVP (CAT-001…010)** primero; **F2 (CAT-020…027)** después. Migraciones en **rama nueva** (Alembic 006+), **fuera del PR #1 congelado**. Flags donde aplique.

### 🔴 MVP
| ID | Tarea | Archivos | Tablas/Endpoints | Pruebas clave | Riesgo |
|---|---|---|---|---|---|
| **CAT-001** | Migración `channel_categories` + `channels.category_id` (FK nullable) | `migrations/006_*` | DDL + UNIQUE(key)+idx sort_order | upgrade/downgrade en DB limpia | bajo |
| **CAT-002** | Migración `stream_nodes` + `channels.node_id` (FK nullable) | `migrations/007_*` | DDL + UNIQUE(node_id) | upgrade/downgrade | bajo |
| **CAT-003** | Modelos `ChannelCategory`, `StreamNode` + relaciones en `Channel` | `app/models/*` | — | import + mapping | bajo |
| **CAT-004** | Backfill idempotente: `category` String→`channel_categories`+`category_id`; `.env` nodos→`stream_nodes`+`node_id` | `scripts/backfill_catalog.py` | — | re-ejecución sin duplicar | medio |
| **CAT-005** | ChannelCategoryService + endpoints (cliente list, admin CRUD) | `app/services/channel_category_service.py`, `app/api/{client,admin}/categories.py` | `/catalog/categories`, `/admin/categories` | CRUD, 409 dup, 409 borrado con canales | bajo |
| **CAT-006** | StreamNodeService + admin nodes (sin secretos) + `_resolve_playback_url` usa `node_id` (fallback a String) | `app/services/stream_node_service.py`, `app/api/admin/stream_nodes.py`, `playback.py` | `/admin/streams/nodes` | node match con token; sin creds en salida | medio |
| **CAT-007** | **Catálogo entitlement**: default solo `entitled`; `?include_locked=true` con `entitled/locked/reason_code`; admin sin filtro | `app/services/channel_service.py`, `app/api/client/catalog.py` | `/catalog/channels` | default filtra; include_locked anota; admin todos | **alto** |
| **CAT-008** | Admin channel CRUD (create/update/activate/deactivate) read-only→write | `app/api/admin/channels.py`, `channel_service.py` | `/admin/channels` POST/PATCH | crear/editar/activar; validaciones | medio |
| **CAT-009** | Schemas: `ChannelPublic` (+category name, +entitled/locked/reason_code opcionales); `ChannelAdminOut` (category_id, node_id) | `app/schemas/channel.py` | — | serialización; stream_key ausente en público | medio |
| **CAT-010** | Tests Módulo 4 MVP (ver §10) + CI verde | `tests/test_catalog_*.py` | — | suite completa | bajo |

### 🟠 Fase 2
| ID | Tarea | Notas |
|---|---|---|
| **CAT-020** | `stream_sources` + `channel_stream_sources` (migración+modelos+StreamSourceService) | multi-fuente |
| **CAT-021** | Playback elige fuente por `priority`/calidad (+ fallback) | integra M2 |
| **CAT-022** | EPG: `epg_sources`/`epg_channels`/`channel_epg_map`/`epg_programs` (particionada, UNIQUE) | migración |
| **CAT-023** | EPGSourceService + **EPGImportService** (anti-XXE/SSRF, dedup, cron, etag) | worker |
| **CAT-024** | EPG cliente real (reemplaza `_MOCK_EPG`) filtrado por entitlement + caché | quita mock |
| **CAT-025** | `channel_logos` (multi-resolución) + selección default | logos |
| **CAT-026** | `channel_health_checks` + ChannelHealthService → Monitoring (M2) | health |
| **CAT-027** | CatalogCacheService (Redis) + invalidación por evento | escala |

---

## 10. Plan de pruebas (MVP)

**Catálogo / entitlement (CAT-007, núcleo):**
- `GET /catalog/channels` (default) → **solo** canales del plan activo; un canal fuera de plan **no** aparece.
- `?include_locked=true` → aparecen todos los activos; cada uno con `entitled` (bool), `locked` (bool), y `reason_code=CHANNEL_NOT_INCLUDED` en los bloqueados.
- Suscriptor sin suscripción activa → default lista vacía (o solo free si existieran); include_locked muestra todos como `locked`.
- `stream_key`/`source_url` **nunca** presentes en respuestas cliente.
- **Admin** `GET /admin/channels` → todos (activos+inactivos), **sin** filtro de entitlement.

**Categorías (CAT-005):**
- CRUD admin; `key` duplicada → 409; borrar categoría con canales → 409 (o reasigna).
- Cliente `GET /catalog/categories` ordenado por `sort_order`.
- Canal serializa `category` (nombre) desde `category_id`; backfill desde String coincide.

**Nodos (CAT-006):**
- `stream_nodes` sembrados desde `.env`; `_resolve_playback_url` resuelve por `node_id` y **coincide** con el `node` del playback token.
- Respuesta admin de nodos **sin** credenciales.

**Integración (catálogo no es autoridad):**
- Canal mostrado `entitled=true` pero **suscripción vencida** → `playback/authorize` → **403** (EntitlementService manda).
- Migración 006/007 `upgrade head` + `downgrade` en DB limpia; backfill idempotente (2ª corrida sin duplicar).

**No-regresión:** las 61 pruebas P0 siguen verdes; el catálogo nuevo no rompe playback ni el PR #1.

---

## 11. Checklist Módulo 4 (MVP)
- [ ] Migraciones 006 (`channel_categories`+`category_id`) y 007 (`stream_nodes`+`node_id`) — upgrade/downgrade OK.
- [ ] Backfill idempotente: categorías y nodos poblados; `category_id`/`node_id` seteados; String legacy conservado.
- [ ] ChannelCategoryService + endpoints (cliente/admin).
- [ ] StreamNodeService + admin (sin secretos) + `_resolve_playback_url` por `node_id` (con fallback).
- [ ] **Catálogo cliente: default solo autorizados; `?include_locked=true` con `entitled/locked/reason_code`; admin sin filtro.**
- [ ] Admin channel CRUD (write) con validaciones.
- [ ] `stream_key`/`source_url` jamás expuestos al cliente (test).
- [ ] Catálogo **no autoriza**: playback revalida con EntitlementService (test de suscripción vencida).
- [ ] `node_id` del catálogo casa con `node` del playback token / grant de segmentos.
- [ ] Suite (P0 61 + nuevas) verde en CI; sin tocar PR #1 ni producción ni Flussonic.
- [ ] Sin secretos en código/respuestas; compat String+nodos legacy durante transición.
