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
## COMPLETADO — Fase 3e: Web Player Docker + Login fix ✅ (2026-05-18)
## COMPLETADO — Fase 3f: Multi-device LAN + Stream en vivo ✅ (2026-05-18)

### Qué entregó la Fase 3e

**Infraestructura Docker para web player:**
- `web_player/Dockerfile` — multi-stage: `node:22-alpine` build → `nginx:1.27-alpine` serve
- `web_player/nginx.conf` — proxy `/api/` → `api:8000`, SPA fallback para React Router
- `web_player/.dockerignore` — excluye `node_modules`, `dist`, `.env` del build context
- `docker-compose.yml` — nuevo servicio `web_player` en puerto `5173:80`
- `docker-compose.yml` — fix crítico: override `POSTGRES_HOST=postgres`, `REDIS_HOST=redis` en servicio `api`

**Bug fixes:**
1. `app/schemas/client.py` — `os_version` `max_length=32 → 512` en `ClientLoginRequest` y `ClientDeviceRegister`
   - Causa: `navigator.userAgent` supera 32 chars → Pydantic rechazaba todo login desde browser
2. `.env` — agregadas `FLUSSONIC_BASE_URL`, `FLUSSONIC_READONLY_USER`, `FLUSSONIC_READONLY_PASSWORD`
   - Causa: `.env` tenía vars con nombre `FLUSSONIC_EC_MAIN_*` pero `config.py` lee `FLUSSONIC_BASE_URL`
   - Efecto: `is_configured = False` → `playback_url = null` → streams no reproducían

### Flujo validado en browser (2026-05-18)

```
URL: http://127.0.0.1:5173  (Windows)
URL: http://192.168.100.221 (Mac LAN)

1. Login testuser1 / NexoraTest123! → home screen carga
2. CANALES EN VIVO → player abre con 21 canales
3. Seleccionar canal → playback_url = http://181.78.246.211:8002/{stream}/index.m3u8
4. hls.js reproduce video en vivo ✅ (validado con Noticiero 24/7)
5. Múltiples dispositivos simultáneos en LAN ✅

Nota: device limit 5 por suscriptor. Dispositivos activos en testuser1:
- test-device-001 (web_player, localhost)
- web-f28193ee-... (web_player, Windows browser)
+ nuevos que se registren (Mac, etc.)
```

---

## FASE 4 — PRÓXIMOS BLOQUES

### 4.1 Manejo de errores HLS (PRIORIDAD)

Cuando Flussonic retorna error o stream está DOWN, hls.js falla silenciosamente.
Implementar en `web_player/src/player/hlsController.ts`:

```typescript
// Casos a manejar:
// - MEDIA_ERROR: intentar recover() una vez, luego mostrar "Canal no disponible"
// - NETWORK_ERROR / 404: mostrar "Señal no disponible en este momento"
// - 401 en HLS URL: solicitar nuevo token (re-authorize) antes de reintentar
// - Fatal error: limpiar player, mostrar mensaje, permitir reintentar manualmente
```

### 4.2 Signed URLs / Backend-auth formal para Flussonic

Flussonic soporta backend-auth via callback HTTP:
- Configurar en Flussonic: `auth_backend = http://nexora-api:8000/api/stb/auth/validate`
- El endpoint `POST /api/stb/auth/validate` ya existe y valida el playback JWT
- Flujo: cliente pide `playback_url?token={jwt}` → Flussonic valida contra Nexora
- Ventaja: sin este paso cualquiera con la URL puede ver el stream sin suscripción

### 4.3 Multi-Flussonic node routing

El `.env` ya tiene dos nodos (`ec-main`, `co-main`) pero `config.py` solo lee uno (`FLUSSONIC_BASE_URL`).
Implementar selección de nodo:

```python
# config.py: leer FLUSSONIC_NODES=ec-main,co-main
# Para cada nodo leer FLUSSONIC_{NODE}_BASE_URL, _USER, _PASSWORD, _REGION
# FlussonicRouter: selecciona nodo por región del subscriber o round-robin
# Fallback automático si nodo primario no responde
```

### 4.4 Geo-routing / fallback por país

- Detectar IP del cliente en `/authorize`
- Seleccionar nodo Flussonic más cercano (EC → ec-main, CO → co-main)
- Fallback automático si nodo primario DOWN

### 4.5 Android TV / Mobile

- Confirmar que `/api/client/*` funciona desde APK Android TV
- `device_type: "android_tv"` en login para analytics
- Push notifications FCM para expiración de suscripción

### 4.6 Observabilidad

- Structured logging (structlog) con `correlation_id` por request
- Métricas Prometheus: latencia `/authorize`, conexiones activas
- Sentry para errores no capturados en producción

### 4.7 EPG real

Reemplazar `_MOCK_EPG` en `catalog.py`:
- Opción A: tabla `epg_entries` (migración 004) + ingest XMLTV periódico
- Opción B: proxy a proveedor externo con cache Redis TTL 1h

### 4.8 Admin: Write para canales

`POST/PATCH/DELETE /api/admin/channels` cuando se necesite gestión desde UI.
Por ahora: actualizaciones de `stream_key` via `scripts/map_flussonic_channels.py`.

### 4.9 Producción

- `SECRET_KEY` en `.env` cambiar por 64 chars aleatorios reales
- `DEBUG=false` en producción (CORS deja de ser wildcard, usa `_WEB_ORIGINS`)
- HTTPS / reverse proxy (nginx externo) delante de la API
- `nexora_web_player` en puerto 80 o detrás de un dominio

---

## COMANDOS ÚTILES

```bash
# Levantar todos los servicios
docker compose up -d

# Verificar estado
docker ps --filter "name=nexora" --format "{{.Names}}: {{.Status}}"
curl http://localhost:8000/health

# Reiniciar solo la API (tras cambios en .env o código)
docker compose up -d api

# Reconstruir y reiniciar web player (tras cambios en React)
docker compose build web_player && docker compose up -d web_player

# Login test subscriber
curl -X POST http://localhost:8000/api/client/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"testuser1","password":"NexoraTest123!","device_id":"test-device-001"}'

# Playback authorize (verifica playback_url)
curl -X POST http://localhost:8000/api/client/playback/authorize \
  -H "Authorization: Bearer <access_token>" \
  -H "Content-Type: application/json" \
  -d '{"channel_id":"canal-1","device_id":"test-device-001"}'

# Admin login
curl -X POST http://localhost:8000/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"Admin1234!"}'

# Ver logs en tiempo real
docker logs -f nexora_api
docker logs -f nexora_web_player
```

---

## REGLAS DEL PROYECTO (no cambiar sin discusión)

- No usar PHP para módulos nuevos
- No usar MySQL en módulos nuevos
- No usar python-jose, usar PyJWT[crypto]
- No usar asyncpg, usar psycopg[binary]
- Todo flujo nuevo pasa por Client API (`/api/client/*`) — no agregar rutas en `/api/v1/`
- No exponer credenciales Flussonic en ninguna respuesta
- Flussonic es READ ONLY desde Nexora
- Nexora no hace proxy de video — cliente reproduce directo desde Flussonic
- Docker: servicios usan nombres de contenedor (redis, postgres), NO localhost
- No empezar UI en este repo — está en `e:/WEBSITE/nexora_app`
