# 04 — Modelo de datos final (PostgreSQL)

> Esquema moderno por dominios. Convención: PK `uuid` (IDs opacos en API), `created_at/updated_at`, FK reales, enums explícitos, `jsonb` solo como cache/metadata, particiones en logs. 🟢 ya existe en `nexora_api` · 🟡 parcial · ⬜ nuevo.
>
> **Equivalencias legacy** citadas para trazabilidad (Xtream `xtream_iptvpro` / Ministra `stalker`). **No** se copian tipos ni nombres legacy.

---

## Convenciones globales

- **Esquemas lógicos** (namespaces): `auth`, `subscribers`, `devices`, `plans`, `catalog`, `streaming`, `epg`, `vod`, `playback`, `billing`, `audit`. (En PostgreSQL: schemas o prefijos de tabla.)
- **IDs:** `uuid` PK (no secuenciales → sin enumeración).
- **Tiempos:** `timestamptz` siempre; nada de epoch int.
- **Secretos:** **ninguno** en tablas (van a Vault); solo `*_hash`/`*_fingerprint`.
- **Multi-tenant:** `reseller_id` + **RLS** donde aplique.
- **Retención:** logs/sesiones **particionadas por mes** + purga.

---

## Dominio Auth / Admin

### `auth.admins` 🟡 (hoy `users`)
`id uuid PK` · `username citext UNIQUE` · `password_hash text` (Argon2id) · `email citext` · `role_id → roles` · `reseller_id → resellers NULL` · `status enum(active,disabled)` · `mfa_secret_ref text NULL` (referencia Vault) · `last_login_at` · `created_at`.
- Índices: `username`, `(reseller_id,status)`. Legacy: `reg_users`(A) / `administrators`(C, MD5 → migrar con reset).

### `auth.roles` ⬜ · `auth.permissions` ⬜ · `auth.role_permissions` ⬜
RBAC normalizado. `roles(id,name)` · `permissions(id,code)` · `role_permissions(role_id,permission_id)`. Legacy: `member_groups`(A) / `adm_grp_action_access`(C). **Sin** superuser-bypass implícito.

### `auth.resellers` ⬜
`id` · `name` · `status` · `created_at`. + `reseller_ip_ranges(reseller_id, cidr)`. RLS: cada operador solo ve su `reseller_id`.

> Refresh tokens y sesiones admin **NO** en BD → Redis (`nexora:client_refresh:{jti}` ✅).

---

## Dominio Subscribers

### `subscribers.subscribers` 🟢
`id uuid PK` · `username citext UNIQUE` · `password_hash text` (Argon2id) · `full_name` · `email citext` · `status enum(active,suspended,expired)` · `reseller_id NULL` · `max_devices int` · `created_at`.
- **Enum explícito** (no el `status` invertido de C).
- Legacy: `users`(A líneas) / `users`(C cuentas STB).

### `subscribers.status_history` ⬜
`id` · `subscriber_id FK` · `old_status` · `new_status` · `reason` · `actor_id` · `at`. (auditoría de suspensión/reactivación).

### `subscribers.features` ⬜ (feature-flags)
`subscriber_id FK` · `feature_code` · `enabled bool`. Legacy: `user_modules`(C).

---

## Dominio Devices

### `devices.devices` 🟢🟡
`id uuid PK` · `subscriber_id FK` · `device_id text UNIQUE` (externo) · `device_type` · `model` · `brand` · `os_version` (max 512) · `mac NULL` · `serial_hash NULL` · `android_id NULL` · `device_fingerprint` · `cert_fingerprint NULL` ⬜ · `device_secret_ref NULL` ⬜ (Vault) · `status enum(active,blocked,pending)` · `block_reason NULL` · `registered_at` · `last_seen_at`.
- **Identidad fuerte:** MAC **+** serial **+** secret/cert; `status=pending` hasta activación (no auto-add).
- Índices: `device_id`(unique), `(subscriber_id,status)`. Legacy: `mag_devices`/`enigma2_devices`(A) / `users.mac+serial`(C).

### `devices.commands` ⬜
`id` · `device_id FK` · `type enum(cut_off,cut_on,reboot,message,update_epg)` · `payload jsonb` · `status enum(pending,sent,acked)` · `created_at` · `acked_at`. Legacy: `events`(C).

---

## Dominio Plans / Subscriptions

### `plans.plans` 🟢
`id` · `name` · `description` · `max_connections int` · `max_devices int` · `duration_days int` · `price numeric` · `is_active bool`. (Ya existe.)

### `plans.packages` ⬜
`id` · `name` · `type enum(tv,vod,radio,module)` · `is_active`. Legacy: `services_package`(C).

### `plans.plan_packages` ⬜
`plan_id FK` · `package_id FK` · `optional bool`. Legacy: `package_in_plan`(C).

### `plans.package_contents` ⬜
`package_id FK` · `content_type enum(channel,vod,series)` · `content_id uuid`. **FK polimórfica por enum** (reemplaza `service_id varchar` de C y el bouquet-JSON de A).

### `subscriptions.subscriptions` 🟢
`id` · `subscriber_id FK` · `plan_id FK` · `starts_at` · `expires_at` · `is_active bool` · `created_by` · `renewal_note`. Índices: `(subscriber_id, is_active, expires_at)`. (Ya existe.)

> **Entitlements efectivos** = `subscriptions activas` → `plan_packages` → `package_contents`. Resuelto en `PlaybackAuthorizationService`.

---

## Dominio Catalog (Live TV)

### `catalog.channels` 🟢🟡
`id` · `channel_key text UNIQUE` (público) · `number int` · `name` · `category` 🟡→`genre_id` ⬜ · `logo_url` · `stream_key text` (**interno, nunca al cliente**) · `flussonic_node text` · `hls_path text` · `source_type` · `source_url NULL` · `requires_subscription bool` · `censored bool` ⬜ · `is_active bool`. (Base ya existe.)

### `catalog.genres` ⬜ · `catalog.channel_genres` ⬜
`genres(id,name,censored bool)` · `channel_genres(channel_id,genre_id)`. Legacy: `tv_genre`(C). N:M.

### `streaming.channel_streams` ⬜ (separar link físico)
`id` · `channel_id FK` · `node_id FK` · `url text` · `priority int` · `ua_filter NULL` · `status enum(active,down)`. Legacy: `ch_links`+`ch_link_on_streamer`(C). Permite varios orígenes/calidades por canal.

---

## Dominio Streaming (infra)

### `streaming.nodes` 🟡 (hoy `.env`)
`id` · `node_id text UNIQUE` (ec-main, co-main) · `base_url` · `public_base_url` (proxy HTTPS) · `region` · `priority int` · `is_healthy bool` · `max_sessions int`. **Sin secretos** (van a Vault). Legacy: `streaming_servers`(A/C) — **sin** `ssh_password`.

### `streaming.zones` ⬜
`id` · `name` · `region`; + `zone_ip_ranges(zone_id, cidr)`. Legacy: `stream_zones`/`ips_in_zone`(C).

---

## Dominio EPG

### `epg.sources` ⬜
`id` · `uri` · `etag NULL` · `id_prefix` · `lang_code` · `enabled bool` · `last_run_at`. Legacy: `epg_setting`(C).

### `epg.programmes` ⬜  **(particionada por rango de fecha)**
`id` · `channel_id FK` · `start_at` · `end_at` · `title` · `descr` · `lang`. **`UNIQUE(channel_id,start_at)`** (cierra el dup de C). Índice `(channel_id,start_at)`. Legacy: `epg`(C).

---

## Dominio VOD / Series

### `vod.videos` ⬜
`id` · `title` · `is_series bool` · `category_id` · `censored bool` · `hd bool` · `cost numeric NULL` · `tmdb_id NULL` · `year` · `status`. Legacy: `video`(C).

### `vod.video_genres` ⬜ (N:M)
`video_id` · `genre_id`. Reemplaza `genre_id_1..4`(C).

### `vod.seasons` ⬜ · `vod.episodes` ⬜ · `vod.episode_files` ⬜
`seasons(id,video_id,number)` · `episodes(id,season_id,number,title)` · `episode_files(id,episode_id,kind enum(video,sub),storage_ref)`. Legacy: `video_season(_series/_files)`(C).

### `vod.rentals` ⬜ · `vod.resume_points` ⬜
`rentals(subscriber_id,video_id,starts_at,expires_at)` · `resume_points(subscriber_id,content_id,position_s)`. Legacy: `video_rent`/`played_video`(C).

---

## Dominio Playback (estado + histórico)

### `playback.sessions` 🟡 (hoy `sessions` IPTV)  → histórico ⬜ particionado
`id` · `subscriber_id` · `device_id` · `content_type` · `content_id` · `node_id` · `access_token_jti` · `ip` · `user_agent` · `started_at` · `ended_at NULL` · `revoked_at NULL` · `expires_at`. **Particionada por mes**; retención. Legacy: `user_activity_now`(A) / `played_*`+`playback_sessions`(C).
- **Estado en vivo en Redis** (ZSET), **histórico en PG**.

> Tokens de playback **no** se guardan en PG → Redis `nexora:playback:{jti}` (TTL 60 s) ✅.

---

## Dominio Audit

### `audit.audit_log` 🟡 (hoy `audit_logs`)  **(particionada, append-only)**
`id` · `actor_type enum(admin,subscriber,system)` · `actor_id` · `action` · `target_type` · `target_id` · `details jsonb` · `ip` (validada) · `at`. **Sin update/delete** (inmutable). Cubre login admin, cambios de estado, emisión de tokens.

---

## Dominio Billing (F3)

### `billing.invoices` ⬜ · `billing.payments` ⬜ · `billing.billing_events` ⬜
`invoices(id,subscriber_id,amount,status,due_at)` · `payments(id,invoice_id,provider,amount,status,external_ref)` · `billing_events(id,provider,type,payload jsonb,processed bool)` (idempotencia de webhooks).

---

## Índices y particiones clave

| Tabla | Índices / partición |
|---|---|
| `subscribers` | `username`(unique), `(status)`, `(reseller_id)` |
| `subscriptions` | `(subscriber_id, is_active, expires_at)` |
| `devices` | `device_id`(unique), `(subscriber_id,status)` |
| `catalog.channels` | `channel_key`(unique), `(is_active,number)` |
| `epg.programmes` | `UNIQUE(channel_id,start_at)`; **PARTITION BY RANGE(start_at)** mensual |
| `playback.sessions` | `(subscriber_id, started_at)`; **PARTITION BY RANGE(started_at)** mensual |
| `audit.audit_log` | `(actor_id, at)`, `(action, at)`; **PARTITION BY RANGE(at)** mensual |

## Campos sensibles (tratamiento)

| Campo | Tratamiento |
|---|---|
| passwords | `password_hash` Argon2id (nunca claro) |
| `device_secret`/`mfa_secret` | **referencia a Vault**, no el valor |
| `stream_key`, `source_url` interno | nunca expuesto al cliente (solo `channel_key`+signed URL) |
| credenciales Flussonic | solo en Vault/env del backend |
| `ip` en logs | validada por proxy de confianza (no spoofeable) |

## Reglas de retención

| Datos | Retención |
|---|---|
| `playback.sessions` histórico | 90–180 días (configurable), luego agregado |
| `audit.audit_log` | 1–2 años (cumplimiento) |
| `epg.programmes` | ventana móvil (p.ej. -7d/+14d) + purga |
| `devices.commands` acked | 30 días |

## Estrategia de migración (si se importa de legacy)

- Passwords MD5/salt-fijo **no se migran** (se fuerza reset/onboarding).
- CSV/bouquet-JSON → explotar a filas N:M (`package_contents`).
- `status` invertido(C) → enum explícito.
- `genre_id_1..4`(C) → `video_genres`.
- ETL **idempotente** con reconciliación de conteos + canary + rollback.
