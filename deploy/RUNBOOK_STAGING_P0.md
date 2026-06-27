# Runbook — Despliegue P0 en STAGING (no producción)

> Rama: `feat/p0-auth-playback-entitlements` (commit `3b72af1`).
> **Solo STAGING.** No producción · no modificar Flussonic · activar flags **gradualmente** · todo reversible.
> Flags por defecto OFF: `ENTITLEMENT_ENFORCE`, `JWT_REQUIRE_AUD`, `SIGNED_URL_ENFORCE`.
> Validado en lab: 42 tests unit/integración + 18/18 E2E + migración 005 real. Pendiente en staging: Nginx `auth_request` real + gating de **segmentos** HLS.

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
# flags OFF en .env.staging (estado inicial seguro):
#   ENTITLEMENT_ENFORCE=false
#   JWT_REQUIRE_AUD=false
#   SIGNED_URL_ENFORCE=false
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

## 8. Segmentos HLS con token válido
```bash
# El manifest referencia segmentos relativos (tracks-.../seg.ts). Verificar que
# un segmento responde a través de /stream/* (gateado).
# ⚠️ PENDIENTE de diseño: los segmentos NO llevan ?token= por defecto.
#    Antes de habilitar SIGNED_URL_ENFORCE en serio, resolver UNO de:
#      (a) caché corta del jti validado en Redis (auth_request OK por jti durante TTL), o
#      (b) reescritura del manifest para propagar el token a cada segmento.
# Probar un segmento con token:
curl -s -o /dev/null -w "%{http_code}\n" "https://<staging>/stream/co-main/<stream>/<segmento>.ts?token=$PTOKEN"
```

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
# 9.3 — signed URLs (tras resolver el gating de segmentos, paso 8)
#   .env.staging: SIGNED_URL_ENFORCE=true ; reiniciar api
#   verificar: playback_url con ?token= ; /stream sin token 401 ; con token 200
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
| **Flags (preferente)** | Poner `ENTITLEMENT_ENFORCE` / `JWT_REQUIRE_AUD` / `SIGNED_URL_ENFORCE` = `false` en `.env.staging` + reiniciar api. Revierte comportamiento **sin** redeploy. |
| **Nginx** | Restaurar el conf de backup (`/backup/nginx_staging_*.conf`) + `nginx -t` + `nginx -s reload`. |
| **Migración** | `alembic downgrade -1` (005 es reversible; drop `plan_channels`). Restaurar dump si fuese necesario. |
| **Seed** | `DELETE FROM plan_channels WHERE plan_id = <plan>;` (o restaurar dump). |
| **Código** | `git revert 3b72af1` (commit aislado). |

---

## Criterio de aprobación de staging → producción
- [ ] Migración 005 aplicada + `plan_channels` poblado (seed) **antes** de enforce.
- [ ] Gating de **segmentos** HLS resuelto (caché jti o token en segmentos) — paso 8.
- [ ] `/stream/*` sin token → 401; con token → manifest 200 **y** segmentos 200.
- [ ] Los 3 flags activados gradualmente, E2E verde entre cada activación.
- [ ] Cross-token admin/client rechazado; logs sin tokens.
- [ ] Plan de rollback por flag verificado.
- [ ] **Nada** aplicado a producción ni a Flussonic durante el proceso.

> **No pasar a producción hasta que staging esté aprobado** con todos los criterios anteriores. La activación en producción será un runbook separado, gradual, con el seed aplicado primero y rollback por flag listo.
