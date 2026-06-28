# Runbook — Despliegue P0 en STAGING (no producción)

> Rama: `feat/p0-auth-playback-entitlements` (commit `2d84e84`).
> **Solo STAGING.** No producción · no modificar Flussonic · activar flags **gradualmente** · todo reversible.
> Flags por defecto OFF/seguro: `ENTITLEMENT_ENFORCE=false`, `JWT_REQUIRE_AUD=false`, `SIGNED_URL_ENFORCE=false`, `PLAYBACK_IP_BINDING_MODE=off`.
> Validado en lab/CI: **61 tests** unit/integración + 18/18 E2E + migración 005 real (CI verde con PostgreSQL+Redis).
>
> **Estado de los bloqueadores de producción (resueltos a nivel CÓDIGO en `2d84e84`):**
> - **C-PROD-1 (gating de segmentos HLS):** ✅ resuelto en código. El manifest (con token) siembra un **grant en Redis** (`nexora:stream_grant:{node}:{stream_key}:{ip_hash}`, TTL `stream_auth_cache_ttl_seconds`≈180s, renovado por segmento). Los segmentos sin token del mismo `node+stream_key+IP` pasan; sin grant / otro stream / otro node / otra IP → **401/403**. **Pendiente: validar contra Nginx `auth_request` real + Flussonic en staging.**
> - **C-PROD-2 (IP-binding del token):** ✅ resuelto en código. El token lleva `cip` (hash de la IP); `PLAYBACK_IP_BINDING_MODE` = `off` (default) | `soft` (warn+permite) | `strict` (mismatch→403). **Pendiente: probar `soft` en staging antes de `strict` (clientes móviles).**
>
> Lo que queda **no es código** → es **validación en staging real**: Nginx `auth_request`, manifest 200, segmentos 200 por grant, `/stream/*` sin token/grant → 401/403, prueba larga de continuidad, logs sin token completo, rollback probado.

---

## Pre-requisitos
- Acceso a un host/entorno **staging** separado de producción (DB, Redis, Nginx, backend propios).
- `alembic.ini` presente como **archivo** (ya restaurado en la rama).
- Backend desplegado desde la rama P0.
- **No** se toca Flussonic (read-only). El edge solo proxya.

> ⚠️ Si no existe un staging dedicado: crear uno (DB/Redis/api/nginx aislados). **No** usar la BD ni el Nginx de producción.

---

## 1. Backup DB staging
```bash
# en el host de staging
pg_dump -h <staging_pg> -U <user> -d <staging_db> -Fc -f /backup/nexora_staging_$(date +%Y%m%d_%H%M).dump
# verificar que el archivo existe y pesa > 0
```

## 2. Aplicar migración 005
```bash
cd /opt/nexora_api            # checkout de la rama P0 en staging
test -f alembic.ini || { echo "alembic.ini NO es archivo — abortar"; exit 1; }
# dentro del contenedor api de staging:
docker exec nexora_api_staging alembic current
docker exec nexora_api_staging alembic upgrade head
docker exec nexora_api_staging alembic current        # debe mostrar 005
# verificar tabla:
docker exec nexora_pg_staging psql -U <user> -d <staging_db> -c "\d plan_channels" | grep -E "uq_plan_channels|ix_plan_channels"
```

## 3. Seed plan_channels
```bash
# importar catálogo (si la BD staging está vacía) + sembrar el plan con sus canales
docker exec nexora_api_staging python scripts/import_m3u_channels.py
docker exec -e SEED_PLAN_NAME="Plan Anual 365 dias" nexora_api_staging python scripts/seed_plan_channels.py
# confirmar: el plan incluye los N canales (debe ser >0 antes de activar ENTITLEMENT_ENFORCE)
```
> **Crítico:** el seed debe ejecutarse **antes** de activar `ENTITLEMENT_ENFORCE=true`, o los suscriptores perderán acceso a todos los canales.

## 4. Desplegar backend en staging
```bash
docker compose -f docker-compose.staging.yml up -d --build api
curl -fsS http://<staging>:8000/health    # redis ok
# flags en .env.staging (estado inicial seguro):
#   ENTITLEMENT_ENFORCE=false
#   JWT_REQUIRE_AUD=false
#   SIGNED_URL_ENFORCE=false
#   PLAYBACK_IP_BINDING_MODE=off          # off | soft | strict
#   STREAM_AUTH_CACHE_TTL_SECONDS=180     # TTL del grant de segmentos (C-PROD-1)
```

## 5. Aplicar Nginx auth_request (SOLO staging)
```bash
# Copia de seguridad del conf actual de staging:
cp deploy/nginx/nexoraplay.conf /backup/nginx_staging_$(date +%s).conf
# Integrar el bloque de deploy/nginx/nexoraplay.stream-auth.example.conf:
#   - location = /__stream_auth  (internal → /internal/stream-auth/validate)
#   - reemplazar las location /stream/ec-main|co-main por las versiones con auth_request
# Validar y recargar (staging):
docker exec nexora_nginx_staging nginx -t
docker exec nexora_nginx_staging nginx -s reload
```
> No tocar el Nginx de producción. Flussonic intacto.

## 6. `/stream/*` sin token → 401/403
```bash
# Con SIGNED_URL_ENFORCE aún false el manifest puede no llevar token; este test
# valida el gate Nginx→FastAPI: pedir un stream SIN token debe ser rechazado.
curl -s -o /dev/null -w "%{http_code}\n" "https://<staging>/stream/co-main/<stream>/index.m3u8"
# esperado: 401 (o 403)
```

## 7. `/stream/*` con token válido → manifest 200
```bash
# obtener token (no imprimir completo):
TOK=$(curl -s -X POST https://<staging>/api/client/auth/login -H 'Content-Type: application/json' \
  -d '{"username":"<sub>","password":"<pass>","device_id":"<dev>","device_type":"web_player","model":"x","brand":"Nexora","os_version":"x"}' \
  | jq -r .access_token)
PURL=$(curl -s -X POST https://<staging>/api/client/playback/authorize -H "Authorization: Bearer $TOK" \
  -H 'Content-Type: application/json' -d '{"channel_id":"canal-1","device_id":"<dev>"}' | jq -r .playback_url)
# PURL contiene ?token= (enmascarar al loguear). Pedir el manifest:
curl -s -I "$PURL" | grep -iE "^HTTP|content-type"   # → 200 application/vnd.apple.mpegurl
```

## 8. Segmentos HLS — grant Redis (C-PROD-1, ✅ resuelto en código)
Los segmentos relativos (`tracks-.../seg.ts`) **NO** llevan token: el manifest del
paso 7 (con token) **sembró un grant** en Redis (`nexora:stream_grant:{node}:{stream_key}:{ip_hash}`)
que el `auth_request` consume para los segmentos del mismo `node+stream_key+IP`.
```bash
# (a) Segmento del MISMO stream tras pedir el manifest (paso 7) → 200 (usa grant)
curl -s -o /dev/null -w "%{http_code}\n" "https://<staging>/stream/co-main/<stream>/<segmento>.ts"
# esperado: 200

# (b) Segmento sin manifest previo / otro stream / otra IP → 401/403 (sin grant)
curl -s -o /dev/null -w "%{http_code}\n" "https://<staging>/stream/co-main/<otro_stream>/<segmento>.ts"
# esperado: 401

# Verificar el grant en Redis (sin imprimir valores sensibles):
docker exec nexora_redis_staging redis-cli --scan --pattern 'nexora:stream_grant:*' | head
```
> Invariante: el **primer** request debe ser el manifest (con token) para sembrar el grant.
> El TTL del grant (`STREAM_AUTH_CACHE_TTL_SECONDS`, ~180s) se **renueva en cada segmento**;
> verificar continuidad (paso 10) en sesiones largas (TTL grant 180s vs token 60s + heartbeat).

## 9. Activar flags GRADUALMENTE (uno por uno, validando entre cada uno)
```bash
# 9.1 — entitlement primero (con seed YA aplicado)
#   .env.staging: ENTITLEMENT_ENFORCE=true ; reiniciar api
#   verificar: canal en plan → 200 ; canal fuera de plan → 403 CHANNEL_NOT_INCLUDED
#   verificar: testuser de prueba sigue reproduciendo (seed cubre sus canales)
#
# 9.2 — JWT estricto
#   .env.staging: JWT_REQUIRE_AUD=true ; reiniciar api
#   verificar: login emite aud/iss/type ; token cliente en /api/admin → 401 ; refresh OK
#   ⚠️ tokens legacy emitidos antes quedan inválidos → los clientes re-login
#
# 9.3 — signed URLs (gating de segmentos ya resuelto en código, paso 8)
#   .env.staging: SIGNED_URL_ENFORCE=true ; reiniciar api
#   verificar: playback_url con ?token= ; manifest con token 200 ; segmentos 200 (grant);
#              /stream sin token NI grant → 401
#
# 9.4 — IP-binding del playback token (C-PROD-2) — escalonar off → soft → strict
#   .env.staging: PLAYBACK_IP_BINDING_MODE=soft ; reiniciar api
#   verificar: misma IP → 200 ; IP distinta → 200 + WARNING en logs (no rompe)
#   observar métricas/logs de mismatch unos días (clientes móviles cambian de IP)
#   solo si la red del cliente es estable: PLAYBACK_IP_BINDING_MODE=strict
#     verificar: misma IP → 200 ; IP distinta → 403
#   ⚠️ producción: mantener soft salvo evidencia de que strict no rompe clientes
```

## 10. E2E completo en staging
- Login → catálogo → canal incluido → authorize → playback_url firmada → manifest 200 → segmentos.
- Canal no incluido → 403; device no registrado → 403; device cap → register 409 / login OK.
- Token cruzado admin↔client → 401. `/stream/*` sin token → 401.
- (Reusar el patrón del E2E lab; ejecutarlo contra staging real con Nginx.)

## 11. Logs sin tokens completos
```bash
# Nginx: el access_log de /stream debe ocultar el token (ver log_format stream_safe en el ejemplo).
docker exec nexora_nginx_staging sh -c "grep -i 'token=' /var/log/nginx/stream.access.log | head" 
# esperado: sin valores de token (solo la ruta, o nada)
# Backend: confirmar que no se loguea el JWT completo (warnings de entitlement no incluyen token).
```

## 12. Rollback
| Nivel | Acción |
|---|---|
| **Flags (preferente)** | Poner `ENTITLEMENT_ENFORCE` / `JWT_REQUIRE_AUD` / `SIGNED_URL_ENFORCE` = `false` y `PLAYBACK_IP_BINDING_MODE=off` en `.env.staging` + reiniciar api. Revierte comportamiento **sin** redeploy. |
| **Nginx** | Restaurar el conf de backup (`/backup/nginx_staging_*.conf`) + `nginx -t` + `nginx -s reload`. |
| **Migración** | `alembic downgrade -1` (005 es reversible; drop `plan_channels`). Restaurar dump si fuese necesario. |
| **Seed** | `DELETE FROM plan_channels WHERE plan_id = <plan>;` (o restaurar dump). |
| **Grant cache** | `redis-cli --scan --pattern 'nexora:stream_grant:*' | xargs redis-cli del` (los grants caducan solos por TTL). |
| **Código** | `git revert <rango P0>` (commits aislados `3b72af1` … `2d84e84`). |

---

## Criterio de aprobación de staging → producción
- [ ] Migración 005 aplicada + `plan_channels` poblado (seed) **antes** de enforce.
- [x] Gating de **segmentos** HLS resuelto **en código** (grant Redis por node+stream_key+ip, commit `2d84e84`) → **validar en staging** (paso 8).
- [x] **IP-binding** del token resuelto **en código** (`cip` + `PLAYBACK_IP_BINDING_MODE`) → **validar `soft` en staging** antes de `strict` (paso 9.4).
- [ ] Nginx `auth_request` real desplegado en staging (manifest + segmentos).
- [ ] `/stream/*` sin token NI grant → 401/403; con token → manifest 200 **y** segmentos 200 (grant).
- [ ] Los flags activados gradualmente (entitlement → JWT → signed-url → IP-binding soft), E2E verde entre cada activación.
- [ ] **Prueba larga de continuidad** (sesión > TTL token 60s y > TTL grant 180s; heartbeat renueva).
- [ ] Cross-token admin/client rechazado; **logs sin token completo** (Nginx + backend).
- [ ] Plan de **rollback por flag** verificado.
- [ ] **Nada** aplicado a producción ni a Flussonic durante el proceso.

> **No pasar a producción hasta que staging esté aprobado** con todos los criterios anteriores. La activación en producción será un runbook separado, gradual, con el seed aplicado primero y rollback por flag listo.
