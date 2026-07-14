# 08 — Flujo EPG / VOD / Series (+ catch-up)

> Guía electrónica, video bajo demanda, series y catch-up. Modelo y flujos derivados sobre todo de Ministra (C), endurecidos. Todo el contenido pasa por la **misma autorización central** y signed URLs. 🟢 existe · 🟡 parcial · ⬜ nuevo.

---

# Parte 1 — EPG

## Modelo (doc 04)
`epg.sources(uri, etag, id_prefix, lang_code, enabled)` · `epg.programmes(channel_id FK, start_at, end_at, title, descr, lang)` con **`UNIQUE(channel_id,start_at)`** y **partición por fecha**. Mapeo XMLTV: `epg_sources.id_prefix` + `<programme channel>` ↔ `channels.xmltv_id`.

## Ingest (worker async + cron) ⬜
```
EPG ingest worker (cron, p.ej. cada 6h)
  por cada source enabled:
    1. GET uri (gzip, ETag, sigue 301) con LÍMITE de tamaño/tiempo/rate   ← evita DoS/disco (fallo C)
    2. parsear XMLTV con parser SEGURO (XXE deshabilitado)                ← evita XXE (fallo C)
    3. normalizar tz/idioma ; mapear channel→channel_id por xmltv_id
    4. UPSERT en epg.programmes  ON CONFLICT(channel_id,start_at)         ← dedup por constraint
    5. registrar last_run_at, etag
```
**Correcciones sobre C:** C no tenía cron (refresco manual), ni límite de descarga, ni unique. Nexora: cron + límites + constraint.

## Consulta (Client/STB API)
```
GET /api/client/catalog/channels/{key}/epg?from&to   🟡(hoy mock)
GET /api/client/epg/now?channels=...
GET /api/admin/epg/sources  (CRUD)                    ⬜
```
- Respuesta cacheada en Redis/CDN (TTL corto) filtrada por entitlements.
- `getCurProgram`, `getCurProgramAndFiveNext` (patrón de C) reimplementados con índice `(channel_id,start_at)`.

## Riesgos cubiertos
- XXE (parser endurecido) · DoS por feed gigante (límites) · duplicados (UNIQUE) · fuentes no confiables (lista blanca de `uri`).

---

# Parte 2 — VOD (películas)

## Modelo (doc 04)
`vod.videos(is_series=false, category_id, censored, hd, cost, tmdb_id)` · `vod.video_genres(N:M)` · `vod.rentals` · `vod.resume_points`. Reemplaza `genre_id_1..4` de C por N:M.

## Catálogo + metadata ⬜
```
GET /api/client/vod?category=&genre=&page=
GET /api/client/vod/{id}
  metadata: enriquecimiento ASYNC (worker) desde TMDB (API key en Vault)   ← no en HTTP claro como C
  saneo de campos (sin XSS al panel)
```

## Reproducción VOD ⬜ (autorización central)
```
POST /api/client/playback/authorize { content_type:"vod", content_id, device_id }
  valida: suscripción a paquete VOD  O  rental vigente (rentals) + parental + device + concurrencia
  → signed URL al fichero (S3/almacenamiento) vía edge ; SIN path traversal (fallo C/A)
GET/PUT /api/client/vod/{id}/resume   → resume_points(position_s)
```

## Alquiler (rental) ⬜
```
POST /api/client/vod/{id}/rent → crea rentals(starts_at, expires_at = now + rent_duration)
  authorize comprueba rental vigente si el contenido es de pago
```

---

# Parte 3 — Series

## Modelo (doc 04)
`vod.videos(is_series=true)` → `vod.seasons` → `vod.episodes` → `vod.episode_files(kind: video|sub, storage_ref)`.

## Flujo ⬜
```
GET /api/client/series/{id}            → temporadas
GET /api/client/series/{id}/seasons/{n}/episodes
POST /api/client/playback/authorize { content_type:"series", content_id:<episode_id>, device_id }
  → misma autorización + signed URL ; resume por episodio
```

---

# Parte 4 — Catch-up / Timeshift

> Idea valiosa de C (catch-up anclado a programa EPG), con TTL corregido (C usaba **8 h** → replay amplio).

## Flujo ⬜
```
POST /api/client/playback/authorize
  { content_type:"archive", channel_id, from, to, device_id }
  1. validar entitlement del canal + parental + device + concurrencia
  2. resolver ventana EPG (epg.programmes) → from/to
  3. FlussonicIntegrationService.getArchiveLink(channel, from, to)
  4. StreamTokenService.mint(archive, node, client_ip, ttl_corto)   ← minutos, no 8h
  → signed URL DVR
```
Soporta los DVR del catálogo de C (Flussonic/Wowza/Nimble) vía adapters; MVP: Flussonic DVR.

---

## Reglas transversales (EPG/VOD/series/catch-up)

- **Autorización central:** todo `authorize` valida entitlement + parental + device + concurrencia antes de firmar.
- **Parental:** contenido `censored` exige **PIN server-side** (no flag de cliente como C).
- **Sin path traversal:** acceso a ficheros por id → storage_ref controlado, nunca por path de input.
- **Metadata async** con keys en Vault y saneo (evita XSS/escala de C).
- **Retención:** `played_*` → `playback.sessions` particionada; EPG ventana móvil.

## Estado vs objetivo
| Capacidad | Hoy | Objetivo |
|---|---|---|
| EPG | 🟡 mock en catalog | ingest async + cron + cache |
| VOD/Series | ⬜ | catálogo + rentals + resume |
| Catch-up | ⬜ | DVR Flussonic + TTL corto |
| Parental PIN | ⬜ | server-side |

## Pruebas mínimas
- Ingest XMLTV con feed de prueba → sin duplicados (UNIQUE), respeta límite de tamaño.
- VOD de pago sin rental → `403`; con rental vigente → signed URL.
- Episodio de serie no suscrita → `403`.
- Catch-up: token expira en minutos; IP distinta → rechazado.
