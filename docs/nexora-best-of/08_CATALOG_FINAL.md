# 08 — Catálogo / Canales / Categorías / EPG / Stream Sources (FINAL)

> Diseño consolidado del Módulo 4 (lo mejor de auditorías legacy + estado real de `nexora_api`). Investigación + diseño aprobado. **No implementado aún.** Detalle completo: [`modules/04_CATALOG_DEEP.md`](modules/04_CATALOG_DEEP.md).
> Restricciones vigentes: no tocar PR #1 (congelado), no producción, no Flussonic, sin secretos.

## Decisión central
El catálogo actual de Nexora **funciona para live** pero es **plano** (todo inline en `channels`, categoría como String, una sola fuente, nodos en `.env`, **EPG mock**) y está **desconectado del entitlement**. El Módulo 4 lo **normaliza** y lo **conecta a `plan_channels`**, sin romper playback y por **fases**.

## Alcance aprobado

### 🔴 MVP
- `channel_categories` + `channels.category_id` (FK).
- `stream_nodes` + `channels.node_id` (FK) — de `.env` a DB (habilita failover del Módulo 2).
- **CRUD admin básico** de canales y categorías.
- **Catálogo cliente conectado a entitlement** (`plan_channels`).
- **Compatibilidad** con `category` String y nodos actuales durante la migración (backfill → deprecar).

### 🟠 Fase 2
- `stream_sources` + `channel_stream_sources` (multi-calidad/fallback avanzado).
- **EPG real**: `epg_sources` / `epg_channels` / `channel_epg_map` / `epg_programs` (particionada, `UNIQUE(epg_channel_id,start_at)`).
- `channel_logos` (multi-resolución).
- `channel_health_checks` + ChannelHealthService.
- `CatalogCacheService` (Redis + invalidación).

## Regla de oro: catálogo ≠ autoridad
- **Cliente** `GET /api/client/catalog/channels` → **default: solo canales autorizados** (entitled).
- **Bloqueados:** `GET /api/client/catalog/channels?include_locked=true` → cada canal con `entitled` (bool), `locked` (bool) y `reason_code` (p.ej. `CHANNEL_NOT_INCLUDED`).
- **Admin** `GET /api/admin/channels` → **todos**, sin filtro de entitlement.
- El catálogo **refleja** el entitlement para la UI; **`PlaybackAuthorizationService` + `EntitlementService` siguen siendo el único guardián** y revalidan antes de emitir `playback_url`. Un canal `entitled=true` puede ser denegado en playback (suscripción vencida, concurrencia…).
- **`stream_key`/`source_url` jamás** se exponen al cliente.

## Servicios
| Servicio | Fase | Rol |
|---|---|---|
| ChannelCatalogService | MVP | listar/CRUD canales + filtrado por entitlement |
| ChannelCategoryService | MVP | categorías (FK) |
| StreamNodeService | MVP | nodos `.env`→DB (sin secretos) |
| StreamSourceService | F2 | fuentes multi-calidad/fallback |
| EPGSourceService / EPGImportService | F2 | feeds XMLTV + ingest (anti-XXE/SSRF, dedup, cron) |
| ChannelHealthService | F2 | health canal/nodo (read-only Flussonic) |
| CatalogCacheService | F2 | caché Redis + invalidación |

## Integración con playback (Módulos 2/3)
- El catálogo entrega el `node_id`/source que **debe casar** con el `stream_key`+`node` del **playback token** y con el **grant de segmentos** (C-PROD-1). Si no casan, el token/grant fallan.
- `_resolve_playback_url` migra de `channel.flussonic_node` (String) a `channel.node_id` (FK), con fallback durante la transición.
- Invalidar caché de catálogo al cambiar `plan_channels` (un canal nuevo en el plan cambia `entitled`).

## Riesgos priorizados
- 🔴 Exponer `stream_key`/origen al cliente → catálogo solo `channel_key`.
- 🔴 EPG ingest XXE/SSRF/DoS (F2) → parser sin entidades externas, límites, lista blanca de `uri`.
- 🟠 `category` String / nodos sin health → normalizar (`channel_categories`, `stream_nodes`).
- 🟠 Source/node del catálogo que no casa con token/grant → contrato de `node_id` compartido con Módulo 2.

## Rollout
- Migraciones Alembic **006+** en **rama nueva** (no el PR #1 congelado), con **backfill idempotente** y compat de los campos String legacy.
- VOD/series **fuera de alcance** (módulo dedicado); `content_type` reservado en `channels`.
- Backlog: **CAT-001…010 (MVP)** → **CAT-020…027 (F2)** — ver `modules/04_CATALOG_DEEP.md` §9.

## Estado
✅ Investigación + diseño (Lote 1) y servicios/backlog/pruebas/checklist (Lote 2) completos. ⏳ Implementación pendiente de autorización (rama nueva, sin tocar PR #1).
