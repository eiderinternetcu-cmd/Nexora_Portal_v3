# Runbook — Despliegue P0 en PRODUCCIÓN (PLAN, no ejecución)

> **SOLO PLAN.** Este documento NO ejecuta nada. Define el camino para activar el P0
> (PR #1) en producción **después** de la validación en staging real
> (ver `deploy/STAGING_REAL_VALIDATION_REPORT.md` → veredicto OK).
>
> Producción: `nexoraplay.net` (host `45.184.225.4`). Flags por defecto **OFF**; activación
> **gradual** y **reversible**. Flussonic/Astra **read-only**. Nada se aplica sin aprobación
> explícita del responsable y rollback listo.

## ⛔ Gates previos (bloqueantes — no iniciar sin esto)
- [ ] **Password root del host staging ROTADO** (y, en general, acceso **solo por clave SSH**; `PasswordAuthentication no`). El password apareció en transcript/IDE local.
- [ ] **PR #1 mergeado a `main`** y **CI verde** en `main`.
- [ ] Validación staging real **OK** (reporte firmado).
- [ ] Ventana de mantenimiento acordada + responsable on-call + plan de comunicación a usuarios.

## 1. Prechecks (producción)
- [ ] **Backup DB producción**: `pg_dump` completo + verificar restaurabilidad.
- [ ] **Backup Nginx producción**: copiar `nexoraplay.conf` y sitios habilitados a `/backup/nginx_prod_$(date +%s)/`.
- [ ] **`.env` producción sin secretos expuestos**: confirmar gitignored, no commiteado, permisos `600`; nada de credenciales en archivos versionados ni logs.
- [ ] **Rotar/confirmar credenciales**: read-only por nodo (`ec-main`, `co-main`, `ec-quito`) separadas; `SECRET_KEY` prod fuerte y distinto de dev/staging.
- [ ] **Auditor `source_url` en producción (SOLO LECTURA)** → **0 RISK**:
      `DATABASE_URL='postgresql://USER:***@PROD:5432/<db>' ALLOWED_STREAM_ORIGINS=https://nexoraplay.net ALLOWED_STREAM_NODES=ec-main,co-main,ec-quito python scripts/audit_channel_source_urls.py`
      Si hay RISK → `backfill_channel_source_urls_same_origin.py` **primero en dry-run**, revisar, y aplicar en ventana.
- [ ] Confirmar **bases same-origin** en `.env` prod (`FLUSSONIC_*_BASE_URL=https://nexoraplay.net/stream/<node>`).

## 2. Deploy con flags OFF (cero cambio de comportamiento)
- [ ] Desplegar el código de PR #1 (desde `main`) con **todos los flags OFF**:
  - `ENTITLEMENT_ENFORCE=false`
  - `JWT_REQUIRE_AUD=false`
  - `SIGNED_URL_ENFORCE=false`
  - `PLAYBACK_IP_BINDING_MODE=off`
- [ ] **No** aplicar el Nginx `auth_request` todavía (se aplica en el paso 6.3).

## 3. Migración
- [ ] `alembic upgrade head`.
- [ ] Confirmar **versión 005** (`alembic current` + existe `plan_channels`).

## 4. Seed
- [ ] `python scripts/seed_plan_channels.py` (plan anual con sus canales) **antes** de cualquier enforcement.
- [ ] Confirmar que el **plan anual incluye los canales** esperados (los suscriptores no deben perder acceso al activar entitlement).

## 5. Verificación con flags OFF (no regresión)
- [ ] `/health` 200.
- [ ] login OK · catálogo OK · `playback/authorize` OK (comportamiento idéntico al actual).
- [ ] Reproducción normal de un canal real (sin cambios para el usuario).
- [ ] **Criterio:** cero regresión con flags OFF antes de avanzar.

## 6. Activación gradual (uno por uno, validando + ventana de observación entre cada uno)
1. **`ENTITLEMENT_ENFORCE=true`**
   - [ ] canal en plan → 200; **fuera de plan → 403 `CHANNEL_NOT_INCLUDED`**.
   - [ ] usuarios reales con plan vigente siguen reproduciendo (seed aplicado).
2. **`JWT_REQUIRE_AUD=true`**
   - [ ] **tokens cruzados** (cliente↔admin/stb) → **401**; login/refresh OK.
   - [ ] ⚠️ tokens legacy previos quedan inválidos → re-login esperado.
3. **`SIGNED_URL_ENFORCE=true`** + **Nginx `auth_request` en producción**
   - [ ] `playback_url` lleva `?token=` same-origin.
   - [ ] Integrar bloque `auth_request` en `nexoraplay.conf` (locations `/stream/{ec-main,co-main,ec-quito}/` + `/__stream_auth` internal + `log_format` sin token). **Hacer la location del web player resiliente** (resolver+variable, ver fix de staging) si aplica.
   - [ ] `nginx -t` + reload. **Backup previo obligatorio.**
   - [ ] Validar: manifest sin token → 401; con token → 200; **segmentos reales → 200 por grant**; cross-stream → 401.
4. **`PLAYBACK_IP_BINDING_MODE=soft`**
   - [ ] IP distinta → 200 + warning (no rompe). Observar métricas de mismatch (clientes móviles).
   - [ ] **NO activar `strict`** todavía (solo tras evidencia de red estable).

## 7. Rollback (probar el procedimiento antes de confiar)
| Nivel | Acción |
|---|---|
| **Flags (preferente)** | poner todos a `false`/`off` + reiniciar api → revierte **sin** redeploy |
| **Nginx** | restaurar conf de backup (`/backup/nginx_prod_*`) + `nginx -t` + reload |
| **Grants Redis** | `redis-cli --scan --pattern 'nexora:stream_grant:*' \| xargs -r redis-cli del` (caducan solos por TTL) |
| **DB** | `alembic downgrade -1` (005 reversible) o restaurar dump (según el caso) |
| **Deploy** | `git revert` del rango P0 / volver a la imagen anterior |

## 8. Monitoreo (durante y después)
- [ ] **Logs Nginx sin tokens** (`grep -c token=` → 0; formato `stream_safe`).
- [ ] **401/403 esperados** vs anómalos (entitlement/device/cross-token/cross-stream).
- [ ] **Errores HLS** (manifest/segmentos 4xx/5xx anómalos, cortes).
- [ ] **Usuarios afectados** (re-login tras JWT estricto; quejas de acceso).
- [ ] **Recursos**: CPU/RAM del host, Redis (memoria/conexiones, claves `stream_grant`), PostgreSQL (conexiones, locks, `plan_channels`).
- [ ] Métricas de **IP mismatch** (modo soft) para decidir si algún día `strict`.

## Criterio de éxito en producción
- [ ] No regresión con flags OFF.
- [ ] Cada flag activado sin incidentes; 403/401 solo donde corresponde.
- [ ] Segmentos reales servidos por gate vía grant; sin cortes; logs sin tokens.
- [ ] Rollback probado y disponible en cada nivel.

> Este runbook es **plan**. La ejecución en producción requiere aprobación explícita,
> ventana acordada, backups verificados y el gate de seguridad (password rotado) cumplido.
