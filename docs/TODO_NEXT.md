# TODO_NEXT.md — Foco actual
_Last updated: 2026-07-14_

> 📍 **El roadmap completo de lo pendiente vive en [`ROADMAP.md`](ROADMAP.md).**
> Este archivo es solo el **foco inmediato** + reglas del proyecto. Si hay conflicto, manda `ROADMAP.md`.

---

## Estado actual (resumen)

**Producción `nexoraplay.net` (45.184.225.4) — P0 desplegado, Alembic 005.**

| Flag | Estado |
|---|---|
| `ENTITLEMENT_ENFORCE` | ✅ `true` (PROD-2A) |
| `JWT_REQUIRE_AUD` | ✅ `true` (PROD-2B) |
| `SIGNED_URL_ENFORCE` | ✅ `true` (PROD-2C) + Nginx `auth_request` activo |
| `PLAYBACK_IP_BINDING_MODE` | ⬜ `off` → **siguiente (2D)** |

Continuidad 2C validada: 13 min, 396/396 peticiones válidas 2xx, grant Redis TTL 180 renovado,
token de 60 s superado sin corte, cross-stream/otro-node → 401, logs sin tokens.

⚠️ `co-main` (38.210.187.13) **caído** (fuente externa) → 4 canales sin servicio. `ec-main` OK (39 canales).

---

## Próximos pasos inmediatos (P0)

1. **Mergear PR #9** (`infra: version production auth_request gate`) y pushear el housekeeping `f577b51`.
   El fix de Nginx que corre en producción hoy **solo existe en el servidor y en ramas locales**.
2. **PROD-Fase 2D** — `PLAYBACK_IP_BINDING_MODE=soft` (observar mismatches) → `strict`.
   **Requiere autorización explícita por flag.**
3. **Hardening del grant** (residual de 2C): vida máxima del grant (latencia de revocación) y
   fallback al grant ante token expirado. Cambio de código + tests.
4. **Alerta de nodo/stream caído** (co-main).

Detalle, dependencias y el resto (staging real, stress, Lua, observabilidad, EPG, RBAC, VOD…) → **[`ROADMAP.md`](ROADMAP.md)**.

---

## RESTRICCIONES DEL PROYECTO (no cambiar sin discusión)

- No PHP para módulos nuevos
- No MySQL en módulos nuevos
- No python-jose — usar PyJWT[crypto]
- No asyncpg — usar psycopg[binary]
- **No comenzar Android TV/apps nativas** hasta que playback, sesiones y observabilidad estén estables
- No exponer credenciales Flussonic en ninguna respuesta de API
- **Flussonic es READ ONLY** desde Nexora — nunca crear/modificar/eliminar streams
- Nexora **NO hace proxy de video** — el cliente reproduce directo desde el edge
- No exponer `stream_key` al cliente — solo `channel_key`
- Docker: los servicios usan nombres de contenedor (`redis`, `postgres`), NO `localhost`
- No UI en este repo — está en `e:/WEBSITE/nexora_app`
- Todo flujo nuevo pasa por Client API (`/api/client/*`)
- **Producción:** el stack vivo es `docker-compose.production.yml` (NO `docker-compose.yml`, que expone puertos a 0.0.0.0 y rompe el lockdown UFW)
- **Flags P0:** uno por vez, con ventana de observación y rollback por flag

---

## COMANDOS ÚTILES

```bash
# Local
docker compose up -d
curl http://localhost:8000/health
docker exec nexora_api python -m alembic upgrade head
docker exec nexora_api python scripts/import_m3u_channels.py
docker logs -f nexora_api

# Tests dentro del contenedor (P1.5 — este es el comando correcto, no lo
# hagas contra el venv del host salvo que sepas por que).
#
# `docker exec nexora_api python -m pytest tests/` SOLO valida algo desde que
# docker-compose.yml (dev) monta ./tests dentro del contenedor y construye la
# imagen con `target: dev` (Dockerfile), que instala requirements-dev.txt
# encima de la imagen base. Antes de ese cambio pasaban dos cosas en
# silencio: (1) ./tests no estaba montado, asi que pytest corria la copia
# horneada en el build, no lo que tenias editado; (2) pytest ni siquiera
# estaba instalado en la imagen. Un "0 tests run" o un verde que no reflejaba
# tus cambios pasaba desapercibido. `docker-compose.production.yml` NO monta
# ./tests ni pasa `target: dev`, asi que la imagen de produccion se queda tal
# cual (sin pytest, sin la carpeta de tests).
#
# Si tocas Dockerfile o requirements-dev.txt, reconstruye antes de correr:
docker compose build api
docker exec nexora_api python -m pytest tests/ -q

# Un solo fichero:
docker exec nexora_api python -m pytest tests/test_alerts.py -q

# Dentro del contenedor los hosts son postgres:5432 y redis:6379 (no
# localhost:5433 / localhost:6380, que es como se ven desde el host).
# docker-compose.yml ya exporta TEST_DATABASE_URL y TEST_REDIS_URL apuntando
# a la base nexora_test para que esto funcione sin configuracion adicional.
# conftest.py lee TEST_DATABASE_URL / TEST_REDIS_URL — OJO, no POSTGRES_DB.

# Login admin / suscriptor de prueba
curl -X POST http://localhost:8000/api/v1/auth/login \
  -H "Content-Type: application/json" -d '{"username":"admin","password":"Admin1234!"}'
curl -X POST http://localhost:8000/api/client/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"testuser1","password":"NexoraTest123!","device_id":"test-device-001","device_type":"web_player","model":"test","brand":"Nexora","os_version":"test"}'

# Observabilidad (requiere admin token)
curl http://localhost:8000/api/admin/metrics        -H "Authorization: Bearer {TOKEN}"
curl http://localhost:8000/api/admin/sessions/live  -H "Authorization: Bearer {TOKEN}"
curl http://localhost:8000/api/admin/nodes/health   -H "Authorization: Bearer {TOKEN}"

# Producción (45.184.225.4) — stack = docker-compose.production.yml
ssh internet@45.184.225.4
cd /opt/nexora_api
sudo docker compose -f docker-compose.production.yml ps
sudo docker exec nexora_api python -m alembic current
curl https://nexoraplay.net/health
```

> Runbooks: `deploy/RUNBOOK_PRODUCTION_P0.md` (prod, con rollback por flag) · `deploy/RUNBOOK_STAGING_P0.md` (staging, aún sin ejecutar).
