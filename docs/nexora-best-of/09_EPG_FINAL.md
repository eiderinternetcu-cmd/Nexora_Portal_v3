# 09 — EPG real / XMLTV / Guía de programación (FINAL)

> Diseño consolidado del Módulo 5 (lo mejor de Xtream/XUI + Ministra/Stalker + estado real de `nexora_api`, sin copiar código legacy). Investigación + diseño aprobado. **No implementado aún.** Detalle completo: [`modules/05_EPG_DEEP.md`](modules/05_EPG_DEEP.md).
> Restricciones vigentes: no tocar PR #1 (congelado), no producción, no Flussonic, no Nginx, sin secretos. EPG va **después del MVP del Módulo 4**.

## Decisión central
Nexora hoy sirve EPG **mock** (`_MOCK_EPG` en `catalog.py`, sin datos reales, sin filtrar por entitlement). El Módulo 5 diseña el **EPG real**: ingesta **XMLTV segura** (anti-XXE/SSRF/DoS), **import desacoplado** del request (CLI + cron + admin manual), **deduplicación**, **cache Redis** y **filtrado por entitlement** — reutilizando el set de “canales autorizados” del Módulo 4.

## Alcance MVP
- Tablas: `epg_sources` · `epg_channels` · `channel_epg_map` · `epg_programs` · `epg_import_runs`.
- Servicios: `EPGSourceService` · `EPGImportService` · `EPGProgramService` · `EPGMappingService` · `EPGCacheService`.

## Modelo aprobado
- **`channel_epg_map` (N:1):** `channel_id` **PK/UNIQUE**, `epg_channel_id` **FK (sin UNIQUE)**.
  - Un canal Nexora tiene **máximo 1** mapping EPG activo.
  - Un `epg_channel` puede ser **compartido por varios** canales (p.ej. HD y SD con el mismo `xmltv_id`).
- **`epg_programs` particionada mensual** = recomendación final para producción desde el inicio.
- **`UNIQUE(epg_channel_id, start_at)`** (dedup) + índice `(epg_channel_id, start_at)`.
- Programas en **UTC**; `epg_channels` con `UNIQUE(source_id, xmltv_id)` + `id_prefix` por fuente.

## Seguridad del import (crítico)
- **anti-XXE**: parser sin DTD/ENTITY, profundidad/expansión acotadas, parse **streaming**.
- **anti-SSRF**: **allowlist de dominios** + **bloqueo de IPs privadas/loopback/link-local/metadata** (tras resolver DNS); sin redirects fuera de la allowlist.
- **anti-DoS**: **límite de tamaño** (MAX_BYTES) + **timeout** de descarga; una fuente que falla no aborta las demás.
- **Import fuera del request del usuario** (worker/CLI/cron); nunca lo dispara el cliente.
- **Logs sin secretos** (`epg_import_runs.error_message` sanitizado; sin credenciales de fuentes).

## API cliente
- `GET /api/client/catalog/channels/{channel_key}/epg` (reemplaza el mock; ventana `?from=&to=` opcional)
- `GET /api/client/epg/now`
- `GET /api/client/epg/grid?from=&to=`
- Respuesta **UTC**, **sin base64**, **filtrada por entitlement**.
- **Si no hay EPG → 200 con lista vacía** (no 404/500; catálogo intacto).

## API admin
- **CRUD** de `epg_sources` (valida allowlist).
- **Listar `epg_channels`** (`?source_id`, `?unmapped`).
- **Map/unmap** canal ↔ `xmltv_id`/epg_channel.
- **Import manual** (dispara `EPGImportService`).
- **Ver `epg_import_runs`** (estado de última importación + errores sanitizados).

## Reglas
- **EPG no autoriza playback**; es informativo.
- **Catálogo/entitlement filtra la visibilidad** del EPG (mismo set que el catálogo del Módulo 4).
- **El playback sigue siendo la autoridad** (`EntitlementService`/`PlaybackAuthorizationService`).
- **Import idempotente** (UPSERT por `UNIQUE(epg_channel_id, start_at)`; `etag` igual → skip).
- **Retención**: pasado > **7 días**, futuro > **14 días** (configurable).
- **Cache Redis** con invalidación por import / cambio de mapeo / cambio de `plan_channels`.
- `stream_key`/orígenes **nunca** se exponen en respuestas EPG.

## Backlog
- **MVP:** `EPG-001…014` (migración → modelos → parser seguro → downloader seguro → EPGImportService → CLI+admin import → Source/Mapping services → ProgramService+entitlement → endpoints cliente → cache → retención → runs admin → tests).
- **Fase 2:** `EPG-020…024` (fuentes con credenciales, scheduler robusto, `grid` paginado/perf, multi-idioma/iconos, resolución de conflictos de mapeo).
- Ver `modules/05_EPG_DEEP.md` §10.

## Estado
✅ Investigación + diseño (Lote 1) y servicios/endpoints/backlog/pruebas/checklist (Lote 2) completos.
⏳ Implementación **pendiente** de autorización: va **después del MVP del Módulo 4**, en **rama nueva**, **sin tocar PR #1**.
