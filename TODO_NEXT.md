# TODO_NEXT.md — Próximos Pasos
_Last updated: 2026-05-18_

---

## COMPLETADO — Fase 1 ✅
## COMPLETADO — Fase 2 ✅
## COMPLETADO — Fase 3a: StreamAuthService ✅
## COMPLETADO — Fase 3b: IPTV DB Sessions ✅
## COMPLETADO — Fase 3b.2: Subscription CRUD ✅
## COMPLETADO — Fase 3c: Modern Client API ✅ (2026-05-17)
## COMPLETADO — Fase 3c.1: Catálogo real de canales ✅ (2026-05-17)
## COMPLETADO — Fase 3d: Flussonic Integration ✅ (2026-05-18)

### Qué entregó la Fase 3d

- `app/integrations/flussonic_client.py` — cliente HTTP read-only con `_WriteBlocker`, autenticación Basic privada (`__auth`), singleton `get_flussonic_client()`
- `app/config.py` — `flussonic_base_url`, `flussonic_readonly_user`, `flussonic_readonly_password`, `flussonic_readonly`
- `.env` — credenciales Flussonic (solo backend, en .gitignore)
- `app/api/client/playback.py` — `playback_url` construida desde Flussonic HLS URL (`http://HOST/{stream}/index.m3u8`), fallback a `source_url` en DB
- `app/schemas/client.py` — `PlaybackResponse.playback_url` (renombrado desde `stream_key`)
- `app/api/admin/channels.py` — `GET /{id}/stream-status`: estado live en Flussonic
- `app/api/admin/flussonic.py` — `/health`, `/streams`, `/streams/{name}`: inspección read-only
- `app/api/admin/router.py` — router Flussonic montado
- `scripts/map_flussonic_channels.py` — mapeo de 21 canales DB a stream names Flussonic reales
- `web_player/vite.config.ts` — proxy `/api/* -> http://localhost:8000`
- `web_player/.env` — vars VITE_* (sin credenciales)
- 96 rutas totales

### Flujo validado (curl 2026-05-18)

```
login -> tokens
GET /api/client/catalog/channels -> 21 canales (stream_key NO expuesto)
POST /api/client/playback/authorize canal-1
  -> playback_url: http://181.78.246.211:8002/ECUADOR_TV/index.m3u8
POST /api/client/profile/devices/heartbeat
  -> subscription_active: true, active_connections: 1
```

---

## FASE 4 — PRÓXIMOS BLOQUES

### 4.1 Reproducción hls.js en navegador (PRIORIDAD)

Verificar end-to-end en navegador con Vite dev server:

1. `npm run dev` en `web_player/`
2. Login desde UI con `testuser1 / NexoraTest123!`
3. Seleccionar canal → `POST /api/client/playback/authorize`
4. hls.js recibe `playback_url` y carga `ECUADOR_TV/index.m3u8`
5. Confirmar video reproduciéndose (cuando Flussonic tenga fuentes activas)

Pendiente en código:
- Manejo de error cuando Flussonic retorna 404/stream DOWN
- Mensaje al usuario "señal no disponible" vs error técnico
- Retry automático de HLS con backoff

### 4.2 Manejo de errores HLS

```typescript
// HlsController: manejar MEDIA_ERROR, NETWORK_ERROR
// - Stream DOWN: mostrar "Canal no disponible"
// - 401 en HLS URL: solicitar nuevo token (re-authorize)
// - Timeout: retry con backoff exponencial
```

### 4.3 Signed URLs / Backend-auth formal para Flussonic

Flussonic soporta backend-auth via callback HTTP:
- Configurar en Flussonic: `auth_backend = http://nexora-api:8000/api/stb/auth/validate`
- El endpoint `POST /api/stb/auth/validate` ya existe y valida el playback JWT
- Eliminar URLs sin firma — solo URLs con token en query param o header

Refs: [Flussonic backend auth docs]

### 4.4 Multi-Flussonic node registry

```python
# config.py: FLUSSONIC_NODES = [
#   {"url": "http://181.78.246.211:8002", "region": "quito", "weight": 1},
#   {"url": "http://...", "region": "guayaquil", "weight": 1},
# ]
# FlussonicRouter: selecciona nodo por región del subscriber o round-robin
```

### 4.5 Geo-routing / fallback por país

- Detectar IP del cliente en `/authorize`
- Seleccionar nodo Flussonic más cercano (por región configurada)
- Fallback automático si nodo primario DOWN

### 4.6 Android TV / Mobile

- Confirmar que `/api/client/*` funciona desde APK Android TV (mismas rutas)
- Agregar `device_type: "android_tv"` en login para analytics
- Push notifications via FCM para expiración de suscripción
- Deep links para canales (`nexora://canal/canal-1`)

### 4.7 Observabilidad

- Structured logging (structlog o loguru) con `correlation_id` por request
- Métricas Prometheus: latencia `/authorize`, tasa de error HLS, conexiones activas
- Alertas: si `active_connections > max_connections * 0.9` por suscriptor
- Dashboard Grafana: canales más vistos, errores por región
- Sentry para errores no capturados en producción

### 4.8 EPG real

Reemplazar `_MOCK_EPG` en `catalog.py`:
- Opción A: tabla `epg_entries` (migración 004) + ingest XMLTV periódico
- Opción B: proxy a proveedor externo (Gracenote, EPGDB, etc.)
- Cache Redis con TTL 1h por canal

### 4.9 Admin: Write para canales

`POST/PATCH/DELETE /api/admin/channels` cuando se necesite gestión desde UI.
Por ahora: actualizaciones de `stream_key` via `scripts/map_flussonic_channels.py`.

---

## COMANDOS PARA EL SIGUIENTE AGENTE

```bash
# Verificar entorno
docker-compose up -d
curl http://localhost:8000/health

# Levantar servidor (Windows)
python scripts/dev_server.py

# Levantar web player
cd web_player && npm run dev

# Login test subscriber
curl -X POST http://localhost:8000/api/client/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"testuser1","password":"NexoraTest123!","device_id":"test-device-001"}'

# Playback authorize (retorna playback_url Flussonic)
curl -X POST http://localhost:8000/api/client/playback/authorize \
  -H "Authorization: Bearer <access_token>" \
  -H "Content-Type: application/json" \
  -d '{"channel_id":"canal-1","device_id":"test-device-001"}'
# playback_url -> http://181.78.246.211:8002/ECUADOR_TV/index.m3u8

# Admin: inspeccionar Flussonic
curl http://localhost:8000/api/admin/flussonic/health \
  -H "Authorization: Bearer <admin_token>"

curl http://localhost:8000/api/admin/flussonic/streams \
  -H "Authorization: Bearer <admin_token>"

# Mapear canales adicionales (editar CHANNEL_MAP primero)
python scripts/map_flussonic_channels.py

# Admin login
curl -X POST http://localhost:8000/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"Admin1234!"}'
```

---

## REGLAS DEL PROYECTO (no cambiar sin discusión)

- No usar PHP para módulos nuevos
- No usar MySQL en módulos nuevos
- No usar python-jose, usar PyJWT[crypto]
- No usar asyncpg, usar psycopg[binary]
- No usar MAG/Stalker/Xtream en flujo nuevo — Client API es el camino
- No exponer credenciales Flussonic en ninguna respuesta
- Flussonic es READ ONLY desde Nexora
- Nexora no hace proxy de video — cliente reproduce directo desde Flussonic
- No empezar UI en este repo — está en `e:/WEBSITE/nexora_app`
