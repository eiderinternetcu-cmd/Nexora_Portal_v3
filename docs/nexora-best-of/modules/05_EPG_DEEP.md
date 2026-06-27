# Módulo 5 — EPG real / XMLTV / Guía de programación (investigación profunda)

> Investigación defensiva (solo lectura del legacy, sin copiar código) + estado real de `nexora_api`. Profundiza la **Fase 2 de EPG** ya prevista en el Módulo 4 ([04_CATALOG_DEEP](04_CATALOG_DEEP.md) §3, [08_CATALOG_FINAL](../08_CATALOG_FINAL.md)). Estado: ✅ existe · 🟡 parcial · ⬜ por construir.
> **Lote 1** = §1–§7 (estado, comparativa, modelo, reglas, integración, riesgos, decisiones). **§8 (servicios/endpoints detallados), backlog, pruebas y checklist → Lote 2 tras revisar el modelo.** Sin código, sin rama, sin commit. No tocar PR #1 / producción / Flussonic / Nginx / flags. Módulo 4 aún no implementado.

---

## 1. Estado actual Nexora

| Pieza | Realidad | Estado |
|---|---|---|
| Endpoint EPG cliente | `GET /api/client/catalog/channels/{channel_key}/epg` → `list[EpgEntry]` | 🟡 **mock** |
| Fuente de datos | `_MOCK_EPG` (dict hardcodeado en `catalog.py`, solo canal-1/2/3); horas relativas a `now` | ⬜ |
| `EpgEntry` (schema) | `channel_id`·`title`·`description`·`start_at`·`end_at` | ✅ (forma usable) |
| `channels.epg_id` | `String(128)`, nullable — **declarado pero sin uso** (semilla de mapeo XMLTV) | 🟡 |
| Identificador de canal | `channel_key` (opaco) para el endpoint; `epg_id` para futuro XMLTV | ✅ |
| Tablas EPG | **ninguna** (`epg_sources`/`epg_channels`/`epg_programs`/`channel_epg_map` no existen) | ⬜ |
| Importador XMLTV | **no existe** (no hay parser, ni cron, ni descarga) | ⬜ |
| Cache EPG | ninguno (el mock se arma en memoria por request) | ⬜ |
| Entitlement en EPG | el endpoint valida que el canal exista/activo (`get_active_by_key` → 404), **no** filtra por plan | 🟡 |
| Endpoints “now”/“grid” | no existen (solo EPG por canal) | ⬜ |

**Resumen:** Nexora tiene el **contrato cliente** del EPG (forma `EpgEntry` + ruta por `channel_key`) pero **sin datos reales**: todo es mock. Falta el pipeline completo (fuentes XMLTV → parse seguro → almacenamiento dedupe → servicio filtrado por entitlement → cache), más los endpoints `now`/`grid` y el CRUD admin.

---

## 2. Comparativa legacy

| Tema | Xtream/XUI | Ministra/Stalker | **Nexora actual** | Mejor idea | Decisión Nexora |
|---|---|---|---|---|---|
| Fuentes EPG | `epg` (filas con `epg_url`, `days`, `last_updated`) | `epg_setting` (`uri`, `etag`, `id_prefix`, `lang`, `enabled`) | — | **C** (etag + id_prefix + lang) | `epg_sources` (uri, id_prefix, lang, etag, enabled, last_run) |
| Programas | `epg_data` (`epg_id`, `start`, `stop`, `title`, `descr`, base64) | `epg` (KEY por `ch_id`+`time`) | mock | C (clave natural por canal+tiempo) | `epg_programs` con **UNIQUE(epg_channel_id, start_at)** |
| Canal XMLTV | `epg_channel_id` en `streams` | `xmltv_id` en `itv` (+ `id_prefix` por fuente) | `channels.epg_id` (sin uso) | C (xmltv_id + prefix evita choques) | `epg_channels`(source_id, xmltv_id) + `channel_epg_map` |
| Importación | cron PHP (`xmltv.php`/`epg.php`), descarga + parse a BD | cron + `epg_setting` (incremental por etag) | — | C (incremental + etag) | `EPGImportService` worker + `epg_import_runs` |
| Dedup | frágil (reinserta; duplicados conocidos) | clave compuesta canal+tiempo | — | C | UNIQUE(epg_channel_id, start_at) + UPSERT |
| API cliente | `player_api.php?action=get_short_epg / get_simple_data_table` (base64) | `get_epg_info` / `get_short_epg` | EPG por canal (mock) | mezcla | `…/epg` + `epg/now` + `epg/grid` (JSON limpio, sin base64) |
| Zona horaria | offsets XMLTV → a veces inconsistente | maneja tz | `start_at`/`end_at` (datetime) | — | **Guardar UTC**; parsear offset XMLTV a UTC |
| Retención | crece sin límite (problema) | purga por antigüedad | — | C | purga por retención (config días) |
| Seguridad import | descarga URL arbitraria (riesgo SSRF/XXE) | parser propio | — | — | **anti-XXE/SSRF + allowlist + límites** (ver §6) |

**Riesgos legacy a NO copiar:** descarga de URLs arbitrarias sin allowlist (SSRF), parser XML con entidades externas (XXE/billion-laughs), reinsertar sin dedup (duplicados), EPG sin retención (tabla infinita), base64 en la API (innecesario), import dentro del request del usuario (bloqueo/timeout).

---

## 3. Modelo de datos propuesto

> Todas las tablas en migración dedicada (Alembic, **después** del Módulo 4; rama nueva, no PR #1). Programas en UTC.

**`epg_sources`** ⬜ — feeds XMLTV
`id uuid PK · name · uri text · id_prefix text (desambigua xmltv_id entre fuentes) · lang_code · etag NULL · last_modified NULL · enabled bool · refresh_interval_minutes int · last_run_at NULL · created_at`
- Legacy: `epg_setting`/`epg`. `uri` validado contra **allowlist** (§6).

**`epg_channels`** ⬜ — canales tal como vienen en el XMLTV
`id uuid PK · source_id FK→epg_sources · xmltv_id text · display_name · icon_url NULL · UNIQUE(source_id, xmltv_id)`
- El `id_prefix` de la fuente evita colisión de `xmltv_id` entre feeds.

**`channel_epg_map`** ⬜ — canal Nexora → epg_channel (N:1)
`channel_id FK→channels (**PK / UNIQUE**) · epg_channel_id FK→epg_channels (**sin UNIQUE**) · is_active bool · created_at`
- Un canal tiene **máximo 1** mapping EPG activo (PK/UNIQUE en `channel_id`); un `epg_channel` puede ser **compartido por varios** canales (p.ej. **HD y SD con el mismo `xmltv_id`**). Semilla: `channels.epg_id` (String) → `epg_channels.id`.

**`epg_programs`** ⬜ **(particionada por fecha)** — programación
`id · epg_channel_id FK · start_at (UTC) · end_at (UTC) · title · sub_title NULL · descr NULL · category NULL · lang NULL · **UNIQUE(epg_channel_id, start_at)**`
- Índice `(epg_channel_id, start_at)`; partición mensual/semanal por `start_at`. Cierra el problema de duplicados de A.

**`epg_import_runs`** ⬜ — auditoría/estado de cada importación
`id · source_id FK · started_at · finished_at NULL · status enum(running,ok,failed,skipped) · http_status NULL · bytes_downloaded NULL · channels_seen NULL · programs_upserted NULL · error_message NULL (sin datos sensibles) · created_at`
- Para el admin: “estado de última importación” + errores.

**Opcional (decisión §7): versionado/cache**
- `epg_cache` (Redis, no tabla): `now`/`grid` cacheados por canal+ventana, invalidados por import. **Recomendado en Redis**, no en PG.
- Alternativa de versión: columna `catalog_epg_version` o clave Redis `epg:version` para invalidación masiva barata.

### Mapa de servicios y endpoints (detalle → Lote 2)
| Servicio | Rol | API |
|---|---|---|
| EPGSourceService | CRUD fuentes XMLTV, allowlist, enable/disable | admin |
| EPGImportService | worker: descarga (etag) → parse seguro → upsert dedupe → run log → purga | cron/manual |
| EPGProgramService | consultas now/grid/por-canal, ventana horaria, **filtrado por entitlement** | cliente |
| EPGMappingService | mapear `channel ↔ xmltv_id`/epg_channel; resolver desde `epg_id` | admin |
| EPGCacheService | cache Redis de now/grid + invalidación por import | interno |

Cliente: `GET …/channels/{channel_key}/epg` (reemplaza mock) · `GET /api/client/epg/now` · `GET /api/client/epg/grid` (rango horario `?from=&to=`), **siempre filtrado por entitlement**.
Admin: CRUD `epg_sources` · mapear canal↔xmltv_id · ejecutar import manual · ver estado/errores de última corrida.

---

## 4. Reglas de negocio

1. **EPG no autoriza playback** — es informativo; el playback sigue gobernado por `EntitlementService`/`PlaybackAuthorizationService` (Módulos 2/3).
2. **EPG filtrado por entitlement** — los endpoints EPG solo devuelven programación de canales que el suscriptor puede ver (mismo set que el catálogo del Módulo 4); `now`/`grid` no exponen canales fuera del plan.
3. **Mapeo canal→epg (N:1)** — un canal Nexora tiene **máximo 1** `epg_channel` (PK/UNIQUE en `channel_id`); un `epg_channel` puede ser **compartido por varios** canales (HD+SD con el mismo `xmltv_id`). Un `epg_channel` proviene de **una** fuente XMLTV.
4. **Dedup** por `UNIQUE(epg_channel_id, start_at)` (UPSERT: actualiza título/descr si cambian; nunca duplica).
5. **Zona horaria** — almacenar siempre **UTC**; parsear el offset del XMLTV; el cliente recibe UTC (o con tz explícita).
6. **Retención** — purgar programas más antiguos que `N` días (config) y opcionalmente limitar el futuro a `M` días; tabla acotada.
7. **Sin EPG → vacío, no error** — si un canal no tiene mapeo o programas, responder lista vacía; **nunca** romper el catálogo ni el endpoint.
8. **`now`** = programa cuyo `[start_at, end_at)` contiene el instante actual (por canal entitled); **`grid`** = programas en `[from, to]` por canal (ventana acotada).
9. **Import idempotente y desacoplado** — corre en worker/cron, no en el request del usuario; respeta `etag`/`If-Modified-Since` (skip si no cambió).
10. **`stream_key`/orígenes nunca** aparecen en respuestas EPG (igual que el catálogo).

---

## 5. Integración con Módulo 4, catálogo y entitlement

- **Reutiliza el set de “canales autorizados” del Módulo 4**: el filtrado por entitlement del EPG usa la **misma** consulta (`subscriptions(active) → plan_channels → channel_id`) que `ChannelCatalogService`. Una sola fuente de verdad de “qué puede ver el suscriptor”.
- **Mapeo desde el catálogo**: `channels.epg_id` (existente) es la **semilla** de `channel_epg_map`; al normalizar (Módulo 4) o al importar, se resuelve `epg_id` → `epg_channels.xmltv_id`.
- **`channel_key` como identidad pública**: el endpoint cliente sigue usando `channel_key` (no se expone `xmltv_id` ni `stream_key`).
- **Cache coherente**: invalidar el cache EPG (Redis) cuando (a) termina un import, (b) cambia `channel_epg_map`, (c) cambia `plan_channels` (afecta qué EPG ve el usuario) — alineado con `CatalogCacheService` del Módulo 4.
- **Dependencia de orden**: EPG es **Fase 2 del Módulo 4**; conviene tener primero `channel_categories`/`stream_nodes`/catálogo-entitlement (MVP M4) y `channel_epg_map` apoyado en el catálogo ya normalizado.

---

## 6. Riesgos

### 🔴 Crítico (seguridad del import)
| Riesgo | Mitigación |
|---|---|
| **XXE / billion-laughs** en el parser XMLTV | parser con **entidades externas deshabilitadas** (no DTD/ENTITY), límite de profundidad/expansión, parse **streaming** (no DOM completo) |
| **SSRF** (URL apunta a red interna/metadata) | **allowlist de dominios**; resolver DNS y **rechazar IPs privadas/loopback/link-local** (10/8, 172.16/12, 192.168/16, 127/8, 169.254/16, ::1); sin seguir redirects fuera de la allowlist |
| **DoS por tamaño** (XML gigante) | **límite de bytes** + **timeout** de descarga; abortar la fuente, no la corrida completa |

### 🟠 Alto
| Riesgo | Mitigación |
|---|---|
| Import dentro del request del usuario → timeouts | **worker/cron separado**; el cliente nunca dispara el parse |
| Duplicados / tabla infinita (problema legacy) | UNIQUE(epg_channel_id, start_at) + **retención** + partición |
| Colisión de `xmltv_id` entre fuentes | `id_prefix` por fuente + UNIQUE(source_id, xmltv_id) |
| EPG filtra mal y filtra canales fuera de plan | reutilizar la consulta de entitlement del Módulo 4; tests de fuga |

### 🟡 Medio
| Riesgo | Mitigación |
|---|---|
| Zonas horarias inconsistentes | normalizar a UTC al importar; tests con offsets |
| Carga de `grid` muy amplia | acotar ventana máxima (`to-from`); paginar/limitar canales |
| Logs con URLs/tokens sensibles | `epg_import_runs.error_message` sanitizado; no loguear credenciales de fuentes privadas |
| Mapeos huérfanos (`epg_id` sin epg_channel) | reporte de “no mapeados”; canal sin EPG → vacío |

### 🔵 Bajo
| Riesgo | Mitigación |
|---|---|
| Iconos EPG externos (mixed-content) | normalizar a HTTPS / ignorar |
| Idiomas múltiples en XMLTV | `lang_code` por fuente; elegir preferido |

---

## 7. Decisiones técnicas (a confirmar antes del Lote 2)

1. ✅ **APROBADO (ajuste).** `channel_epg_map` **N:1**: `channel_id` **PK/UNIQUE**, `epg_channel_id` FK **sin UNIQUE** (un epg_channel compartible por varios canales; HD+SD). Un canal = máx. 1 mapping activo.
2. ✅ **APROBADO (ajuste).** `epg_programs` **particionada mensual** es el **diseño preferido para producción desde el inicio** + índice `(epg_channel_id, start_at)`. Si en el backlog la partición complica demasiado el MVP, se documenta una **alternativa temporal sin partición** (misma tabla + UNIQUE + retención), pero la **recomendación final sigue siendo partición mensual**.
3. ✅ **APROBADO.** Retención: pasado > **7 días**, futuro > **14 días**, **configurable**.
4. ✅ **APROBADO.** Cache en **Redis** (no PG) para `now`/`grid`, invalidación por import/mapeo/`plan_channels`.
5. ✅ **APROBADO.** Scheduler = **CLI idempotente** (`epg_import`) por **cron del SO** + **endpoint admin manual**; sin broker pesado en MVP.
6. ✅ **APROBADO.** Allowlist `EPG_ALLOWED_DOMAINS` + bloqueo de IPs privadas; **solo fuentes públicas en MVP** (fuentes con credenciales en fase posterior).
7. ✅ **APROBADO.** Respuesta en **UTC** (contrato actual `EpgEntry`).
8. ✅ **APROBADO.** EPG arranca **después** del MVP del Módulo 4, reutilizando su filtrado por entitlement.

---

## 8. Servicios detallados (Lote 2)

> Diseño, **sin código**. Surface: `app/services/epg/*`, `app/api/{client,admin}/epg*`, `app/integrations/xmltv/*`, `scripts/epg_import.py`. EPG = Fase 2 del Módulo 4 (arranca tras su MVP).

### 8.1 EPGSourceService 🔴 MVP
- **Responsabilidad:** CRUD de `epg_sources`; validar `uri` contra **allowlist** + bloqueo de IPs privadas (no descarga aquí, solo valida y persiste); enable/disable; `refresh_interval_minutes`.
- **Métodos:** `create/update/delete/list/get`, `validate_uri(uri)` (allowlist + DNS→IP privada), `due_sources()` (las que toca refrescar).
- **Tablas:** `epg_sources`. **Errores:** 400 uri no permitida, 409 nombre/uri duplicado, 404.

### 8.2 EPGImportService 🔴 MVP (núcleo del pipeline; worker, NO en request)
- **Responsabilidad:** por fuente → **descargar** (con `etag`/`If-Modified-Since`, límite de bytes, timeout) → **parsear seguro** (anti-XXE, streaming) → **upsert** `epg_channels` + `epg_programs` (dedupe) → registrar `epg_import_runs` → **purgar** por retención → invalidar cache.
- **Métodos:** `run_source(source_id) -> ImportRun`, `run_due()` (para cron), `_download`, `_parse_stream`, `_upsert_programs`, `_purge_retention`.
- **Seguridad (§6):** parser sin DTD/ENTITY, profundidad/expansión acotadas; `_download` con allowlist, sin redirects fuera de allowlist, rechazo de IP privada tras resolver DNS, `MAX_BYTES`/`TIMEOUT`; una fuente que falla **no** aborta las demás.
- **Idempotencia:** UPSERT por `UNIQUE(epg_channel_id, start_at)`; `etag` igual → `status=skipped`.
- **Tablas:** todas. **Salida:** `epg_import_runs` (status/contadores/error sanitizado).

### 8.3 EPGProgramService 🔴 MVP (lectura cliente)
- **Responsabilidad:** servir EPG **filtrado por entitlement**: `by_channel(channel_key, from, to)`, `now(subscriber)`, `grid(subscriber, from, to)`.
- **Lógica:** resuelve `channel_key → channel → channel_epg_map → epg_channel → epg_programs`; **reutiliza el set de canales autorizados del Módulo 4**; ventana acotada (`to-from` ≤ máx); **UTC**; sin EPG → lista vacía.
- **Errores:** 404 canal inexistente/inactivo (by_channel); nunca 500 por falta de EPG.

### 8.4 EPGMappingService 🔴 MVP
- **Responsabilidad:** mapear `channel ↔ epg_channel` (vía `xmltv_id`), **N:1**; resolver semilla `channels.epg_id`; reportar canales **no mapeados** y `epg_channels` huérfanos.
- **Métodos:** `map(channel_id, epg_channel_id|xmltv_id)`, `unmap(channel_id)`, `seed_from_epg_id()`, `unmapped_channels()`.
- **Reglas:** máx. 1 mapping activo por canal (PK/UNIQUE `channel_id`); `epg_channel` compartible. **Errores:** 404 canal/epg_channel, 409 xmltv_id ambiguo entre fuentes (usar `source_id`+`id_prefix`).

### 8.5 EPGCacheService 🟠 (aprobado; core de rendimiento)
- **Responsabilidad:** cache Redis de `now`/`grid`/`by_channel` por (canal/ventana/plan); **invalidación** al terminar import, al cambiar `channel_epg_map`, o al cambiar `plan_channels`.
- **Claves:** `epg:now:{...}`, `epg:grid:{...}`; versión global `epg:version` para invalidación masiva barata. PG es la fuente de verdad.

---

## 9. Endpoints

### Cliente (siempre filtrado por entitlement; UTC; sin base64)
| Método | Ruta | Notas |
|---|---|---|
| GET | `/api/client/catalog/channels/{channel_key}/epg?from=&to=` | reemplaza el **mock**; ventana opcional; vacío si no hay EPG |
| GET | `/api/client/epg/now` | programa actual de **cada canal autorizado** |
| GET | `/api/client/epg/grid?from=&to=` | parrilla por ventana (acotada); solo canales del plan |

### Admin
| Método | Ruta | Acción |
|---|---|---|
| GET/POST/PATCH/DELETE | `/api/admin/epg/sources` | CRUD `epg_sources` (valida allowlist) |
| GET | `/api/admin/epg/channels?source_id=&unmapped=` | listar `epg_channels` (filtros) |
| POST / DELETE | `/api/admin/channels/{id}/epg-map` | mapear / desmapear canal ↔ `xmltv_id`/epg_channel |
| POST | `/api/admin/epg/sources/{id}/import` | **import manual** (dispara EPGImportService) |
| GET | `/api/admin/epg/runs?source_id=` | listar `epg_import_runs` (estado última importación) |
| GET | `/api/admin/epg/runs/{id}` | detalle + **error** de importación (sanitizado) |

---

## 10. Backlog Módulo 5

> Orden por dependencias. EPG corre **tras el MVP del Módulo 4**. Migraciones en rama nueva (no PR #1).

### 🔴 MVP EPG
| ID | Tarea | Notas |
|---|---|---|
| **EPG-001** | Migración: `epg_sources`, `epg_channels` (UNIQUE source_id+xmltv_id), `channel_epg_map` (PK channel_id, FK epg_channel sin unique), `epg_programs` (**partición mensual**, UNIQUE epg_channel_id+start_at), `epg_import_runs` | upgrade/downgrade |
| **EPG-002** | Modelos + relaciones | — |
| **EPG-003** | **Parser XMLTV seguro** (anti-XXE, sin DTD/ENTITY, streaming, límites de profundidad) | `integrations/xmltv` |
| **EPG-004** | **Downloader seguro** (allowlist, bloqueo IP privada, MAX_BYTES, timeout, etag/If-Modified-Since, sin redirect externo) | anti-SSRF/DoS |
| **EPG-005** | EPGImportService (download→parse→upsert dedupe→runs→purga retención→invalida cache) | núcleo |
| **EPG-006** | CLI `scripts/epg_import.py` idempotente + endpoint admin import manual | cron del SO |
| **EPG-007** | EPGSourceService + CRUD admin `epg_sources` | allowlist |
| **EPG-008** | EPGMappingService + admin map/unmap + `seed_from_epg_id` + listar no-mapeados | N:1 |
| **EPG-009** | EPGProgramService (by_channel/now/grid) + **filtrado entitlement** (reusa M4) | UTC, ventana acotada |
| **EPG-010** | Endpoints cliente (reemplaza mock) + `EpgEntry` (+ category/sub_title opcional) | sin base64 |
| **EPG-011** | EPGCacheService (Redis) + invalidación (import/map/plan_channels) | rendimiento |
| **EPG-012** | Retención/purga (7 días pasado / 14 futuro, configurable) en el import | acota tabla |
| **EPG-013** | Admin `epg_import_runs` (estado + errores sanitizados) | observabilidad |
| **EPG-014** | Tests (ver §11) + CI verde | — |

### 🟠 Fase 2 EPG
| ID | Tarea |
|---|---|
| EPG-020 | Fuentes con **credenciales**/privadas (más allá de allowlist pública) |
| EPG-021 | Scheduler robustecido (locking entre corridas, backoff, alertas) |
| EPG-022 | `grid` con paginación/perf para catálogos grandes |
| EPG-023 | Multi-idioma (selección por `lang`), iconos EPG proxied a HTTPS |
| EPG-024 | Resolución de conflictos de mapeo (xmltv_id ambiguo) en admin UI |

> **Alternativa temporal (solo si la partición bloquea el MVP):** crear `epg_programs` **sin partición** (misma UNIQUE + índice + retención) y migrar a partición mensual después. **Recomendación final: partición mensual desde el inicio** (EPG-001).

---

## 11. Plan de pruebas

**Seguridad del import**
- **Anti-XXE**: XMLTV con `<!DOCTYPE>`/`<!ENTITY>` (incl. billion-laughs) → parser **no** expande entidades; import falla controlado, no cuelga.
- **Anti-SSRF**: `uri` a IP privada/loopback/link-local/metadata → **rechazada** (antes de descargar); redirect hacia host fuera de allowlist → **bloqueado**.
- **XML grande / timeout**: feed > `MAX_BYTES` o lento > `TIMEOUT` → **abortado** esa fuente, run `failed`, otras fuentes siguen.

**Datos / pipeline**
- **Dedup**: mismo `(epg_channel_id, start_at)` dos veces → 1 fila (UPSERT actualiza, no duplica).
- **Idempotencia**: correr import dos veces → sin duplicados ni crecimiento; `etag` igual → `skipped`.
- **Retención**: programas fuera de [hoy-7d, hoy+14d] → purgados; dentro → conservados.
- **N:1**: dos canales (HD+SD) mapeados al mismo `epg_channel` → ambos devuelven la misma programación.

**API / negocio**
- **Entitlement**: `now`/`grid`/`by_channel` **no** devuelven canales fuera del plan; suscripción vencida → sin EPG de esos canales.
- **Cache invalidation**: tras import / cambio de mapeo / cambio de `plan_channels` → la respuesta refleja lo nuevo.
- **Endpoint vacío**: canal sin mapeo o sin programas → **lista vacía, 200** (no 404/500), catálogo intacto.
- **Sin base64**: las respuestas EPG son JSON plano (`title`/`descr` en texto, no base64).
- **UTC**: `start_at`/`end_at` en UTC; feed con offset → convertido correctamente.

**No-regresión**: la suite P0 (CI) sigue verde; el EPG nuevo no rompe catálogo ni playback.

---

## 12. Checklist

### 🔴 MVP EPG
- [ ] Migración 5 tablas (partición mensual `epg_programs`, UNIQUE epg_channel_id+start_at, channel_epg_map N:1).
- [ ] Parser XMLTV seguro (anti-XXE, streaming, límites).
- [ ] Downloader seguro (allowlist, bloqueo IP privada, MAX_BYTES, timeout, etag).
- [ ] EPGImportService (dedupe + runs + retención + invalida cache); **fuera del request**.
- [ ] CLI `epg_import` idempotente + endpoint admin import manual.
- [ ] EPGSourceService + CRUD admin; EPGMappingService + map/seed/no-mapeados.
- [ ] EPGProgramService (by_channel/now/grid) **filtrado por entitlement** (reusa M4), UTC, vacío sin EPG.
- [ ] Endpoints cliente (reemplazan mock) + admin runs/errores.
- [ ] EPGCacheService (Redis) + invalidación (import/map/plan_channels).
- [ ] Retención 7/14 configurable.
- [ ] Tests §11 verdes (anti-XXE/SSRF/tamaño, dedup, idempotencia, retención, entitlement, cache, vacío, no-base64, UTC, N:1) + CI.
- [ ] EPG **no autoriza** playback; `stream_key`/orígenes nunca expuestos.

### 🟠 Fase 2 EPG
- [ ] Fuentes con credenciales/privadas.
- [ ] Scheduler robusto (locking/backoff/alertas).
- [ ] `grid` paginado/perf; multi-idioma; iconos proxied.
- [ ] Resolución de conflictos de mapeo en admin.
