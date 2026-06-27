# Checklist — Staging REAL (antes de ejecutar el runbook P0)

> Qué falta para correr `deploy/RUNBOOK_STAGING_P0.md` en un **host staging real** (Nginx real + Flussonic/Astra real + HLS real), no en el ensayo Docker/local con stub ya realizado (ver `deploy/STAGING_REHEARSAL_REPORT.md`).
>
> **Alcance: solo preparación.** No mergear PR #1 · no deploy · no producción · no Flussonic · no Nginx en producción · no flags en producción · Módulo 4 no implementado. Backend = **PR #1** (`feat/p0-auth-playback-entitlements`), ya validado en ensayo (CI PASS).
>
> Convención: `[ ]` pendiente · `[~]` parcial · valores reales/secretos **nunca** en archivos versionados (solo en `.env` gitignored / vault).

---

## 1. Infraestructura base
- [ ] **Host staging** separado de producción (CPU/RAM/disco para PG+Redis+API+Nginx).
- [ ] **Dominio staging** dedicado (p.ej. `staging.nexoraplay.net`) — **no** usar `nexoraplay.net` (producción).
- [ ] **DNS** del dominio staging → IP del host staging.
- [ ] **Certificado SSL** del dominio staging (Let's Encrypt / `certbot`) — HTTPS obligatorio para HLS same-origin.
- [ ] Firewall: exponer solo 80/443; **no** exponer 8000 (API), 5432 (PG), 6379 (Redis) al público; `/internal/*` solo alcanzable desde Nginx.

## 2. Servicios
- [ ] **PostgreSQL staging** (DB `nexora_staging` propia; credenciales solo en `.env`).
- [ ] **Redis staging** (instancia/db propia; aislada de prod).
- [ ] **Backend desde PR #1** (`feat/p0-auth-playback-entitlements`), imagen construida de esa rama.
- [ ] **Nginx real** con el bloque `auth_request` (ver §5).
- [ ] Healthchecks: `GET /health` (API), `nginx -t` OK.

## 3. Variables `.env` staging (gitignored — nunca commitear valores)
- [ ] `SECRET_KEY` (aleatorio fuerte, distinto de dev/prod).
- [ ] `POSTGRES_HOST/PORT/DB/USER/PASSWORD` (staging).
- [ ] `REDIS_HOST/PORT/DB` (staging).
- [ ] **Flags en estado inicial seguro**: `ENTITLEMENT_ENFORCE=false`, `JWT_REQUIRE_AUD=false`, `SIGNED_URL_ENFORCE=false`, `PLAYBACK_IP_BINDING_MODE=off` (se activan **gradualmente** en el runbook).
- [ ] `STREAM_AUTH_CACHE_TTL_SECONDS=180`.
- [ ] **Bases SAME-ORIGIN** de cada nodo (clave para que el `playback_url` pase por el gate):
  - [ ] `FLUSSONIC_BASE_URL=https://staging.nexoraplay.net/stream/ec-main`
  - [ ] `FLUSSONIC_CO_MAIN_BASE_URL=https://staging.nexoraplay.net/stream/co-main`
  - [ ] `FLUSSONIC_EC_QUITO_BASE_URL=https://staging.nexoraplay.net/stream/ec-quito`
- [ ] **Credenciales read-only** por nodo (solo en `.env`): `FLUSSONIC_READONLY_USER/PASSWORD`, `FLUSSONIC_CO_MAIN_USER/PASSWORD`, `FLUSSONIC_EC_QUITO_USER/PASSWORD`.
- [ ] `RATE_LIMIT_PER_MINUTE` razonable (no 60 si vas a hacer pruebas rápidas).
- [ ] Confirmar `.env.production.example` (versionado) **solo con placeholders** (sin secretos reales).

## 4. Upstreams Flussonic/Astra (origen real, read-only)
| node | origen (proxy_pass) | base same-origin (app) |
|---|---|---|
| `ec-main` | `http://181.78.246.211:8002/` (Esmeraldas Astra) | `…/stream/ec-main` |
| `co-main` | `http://38.210.187.13:8002/` (Colombia) | `…/stream/co-main` |
| `ec-quito` | `http://45.70.202.171:8002/` (Quito Astra) | `…/stream/ec-quito` |

- [ ] Conectividad host-staging → cada origen (`:8002`) verificada (read-only, sin modificar Flussonic/Astra).
- [ ] Credenciales read-only de cada Astra válidas (status/API), colocadas solo en `.env`.

## 5. Nginx real (solo staging)
- [ ] Integrar el bloque `auth_request` de `deploy/nginx/nexoraplay.stream-auth.example.conf`:
  - [ ] `location = /__stream_auth` (internal) → `/internal/stream-auth/validate` con headers `X-Original-URI`, `X-Original-Args`, `X-Real-IP`, `X-Forwarded-For`, `X-Playback-Token`.
  - [ ] `location ^~ /stream/ec-main/` → origen ec-main, con `auth_request`.
  - [ ] `location ^~ /stream/co-main/` → origen co-main, con `auth_request`.
  - [ ] `location ^~ /stream/ec-quito/` → origen ec-quito, con `auth_request`.
  - [ ] `log_format stream_safe` (oculta `?token=` en access logs).
- [ ] `nginx -t` OK + reload. **No** tocar el Nginx de producción.

## 6. Migraciones y seed (en staging)
- [ ] `alembic upgrade head` (incluye 005 `plan_channels`).
- [ ] Cargar/Importar canales (`scripts/import_m3u_channels.py`, default `relative` → `source_url` same-origin).
- [ ] `scripts/seed_plan_channels.py` **ANTES** de activar `ENTITLEMENT_ENFORCE` (o los suscriptores pierden acceso).
- [ ] **Auditar** con `scripts/audit_channel_source_urls.py` (DB staging, read-only) → **0 RISK**; si hay RISK → `backfill_channel_source_urls_same_origin.py --apply`.

## 7. Backup y rollback
- [ ] **Backup DB staging** antes de cambios (`pg_dump`).
- [ ] Backup del conf Nginx staging antes de integrar `auth_request`.
- [ ] Rollback probado:
  - Flags → `false`/`off` + reiniciar API (sin redeploy).
  - Nginx → restaurar conf de backup + `nginx -t` + reload.
  - Migración → `alembic downgrade -1` (005 reversible).
  - Grant cache → `redis-cli --scan --pattern 'nexora:stream_grant:*' | xargs redis-cli del` (caducan solos).

## 8. Smoke test (staging real — enmascarar tokens en logs)
- [ ] `/stream/<node>/<key>/index.m3u8` **sin token** → **401/403**.
- [ ] `playback/authorize` (canal en plan) → `playback_url` **HTTPS same-origin** `https://staging…/stream/<node>/<key>/index.m3u8?token=…`.
- [ ] Manifest con token → **200** (real, desde Flussonic/Astra).
- [ ] Segmento `.ts/.m4s` **sin token** del mismo stream → **200** (grant Redis).
- [ ] Segmento de otro stream / otra IP → **401**.
- [ ] Canal **fuera de plan** → **403 `CHANNEL_NOT_INCLUDED`** (con `ENTITLEMENT_ENFORCE=true`).
- [ ] Device **no registrado** → **403 `DEVICE_NOT_REGISTERED`**.
- [ ] Cross-token admin↔client → **401** (`JWT_REQUIRE_AUD=true`).
- [ ] **Continuidad larga**: reproducir > 3 min (TTL token 60s vs grant 180s + heartbeat) sin cortes.
- [ ] Logs Nginx **sin** `token=` completo; backend **sin** JWT completo.
- [ ] `PLAYBACK_IP_BINDING_MODE=soft` primero (mismatch → 200 + warning); `strict` solo si la red del cliente es estable.

## 9. Rotación de credencial `ec-quito` (obligatoria antes de producción)
- [ ] **Rotar** la credencial real de `ec-quito` que apareció en transcript/IDE local (nunca llegó a git, pero se considera potencialmente expuesta).
- [ ] Generar credencial nueva en el panel Astra de Quito (acción manual del operador).
- [ ] Colocar el valor nuevo **solo** en `.env` (gitignored) de staging/prod; **nunca** en `.env.production.example` ni en ningún archivo versionado.
- [ ] Verificar que `.env.production.example` mantiene `FLUSSONIC_EC_QUITO_USER=`/`PASSWORD=` **vacíos** (placeholders).
- [ ] No imprimir la credencial en logs, PRs, ni documentos.

---

## Qué falta EXACTAMENTE antes de ejecutar el runbook real
1. **Host + dominio + SSL staging** provisionados (§1) — bloqueante #1 (el ensayo previo fue Docker/local).
2. **Nginx real con `auth_request`** y las **3 locations** (`ec-main`, `co-main`, `ec-quito`) apuntando a los orígenes reales (§5).
3. **`.env` staging** con bases **same-origin** + credenciales read-only por nodo (§3) — la credencial `ec-quito` **rotada** (§9).
4. **DB staging migrada + canales importados + seed `plan_channels`**, y **auditoría 0 RISK** (§6).
5. **Backups + rollback probados** (§7).
6. Recién entonces: ejecutar `RUNBOOK_STAGING_P0.md` con **flags graduales** y correr el **smoke test** (§8) contra Flussonic/Astra real.

> Estado del código: **listo para revisión humana y staging real** (no “listo para producción”). El gap restante es **infraestructura + datos + rotación de credencial**, no cambios de código.
