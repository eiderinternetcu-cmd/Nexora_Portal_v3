# TODO_NEXT.md — Próximos Pasos
_Last updated: 2026-05-19_

---

## COMPLETADO — Fase 1 ✅
## COMPLETADO — Fase 2 ✅
## COMPLETADO — Fase 3 (3a → 3f) ✅
## COMPLETADO — Fase 4 Bloque 0: M3U Real + Multi-nodo ✅ (2026-05-18)
## COMPLETADO — Fase 4 Bloque 1: Observabilidad base + hls.js hardening ✅ (2026-05-18)

## EN PROGRESO — Deploy servidor remoto 45.184.225.4 (2026-05-19)

**Estado:** Ubuntu 24.04.3 autoinstall en curso. Servidor reiniciado a las ~19:31 UTC.

**Qué se hizo:**
- Diagnóstico Ubuntu 14.04 32-bit: Docker imposible, Python 3.11 sin SSL, pip bloqueado
- Decisión: formatear e instalar Ubuntu 24.04.4 Server AMD64
- iLO (10.3.0.17) no accesible (puerto dedicado sin cable al switch)
- Deploy 100% remoto via GRUB + autoinstall + CIDATA:
  - ISO Ubuntu 24.04.3 descargado (~3.3GB desde USTC mirror)
  - ISO dd'd a `/dev/sdb` (disco secundario, 465GB vacío)
  - Partición CIDATA (`/dev/sdb4`, 50MB FAT) con `user-data` + `meta-data`
  - Kernel + initrd extraídos a `/boot/ubuntu-installer/` en sda
  - GRUB entry "Ubuntu 24.04 Autoinstall" + `grub-reboot` configurado
  - `sudo reboot` ejecutado
- Ver `deploy_ubuntu.sh` para el script completo

**Autoinstall config:**
- Hostname: nexora, User: internet
- eth0 (MAC e8:39:35:b0:56:d6): 45.184.225.4/29 gw 45.184.225.1
- eth1 (MAC e8:39:35:b0:56:d7): 10.3.0.16/22
- Storage: /dev/sda GPT — 1MB bios_grub + 8GB swap + resto /
- SSH + sudo NOPASSWD habilitados

**Próximo paso tras reconectar:**
```bash
ssh internet@45.184.225.4    # password: igual que antes
sudo apt update && sudo apt upgrade -y
# Instalar Docker + Docker Compose
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker internet
# Deploy Nexora API (desde repo local, docker compose up -d)
```

---

### Qué entregó el Bloque 0 (Canales M3U reales)

- **migration 004**: columnas `flussonic_node` + `hls_path` en `channels`
- **`scripts/import_m3u_channels.py`**: UPSERT idempotente de 24 canales reales
- **Routing multi-nodo**: `get_flussonic_node_client(node_id)` → ec-main / co-main
- **`_resolve_playback_url`**: usa `channel.flussonic_node` para generar URL del nodo correcto
- **`config.py`**: lee `FLUSSONIC_CO_MAIN_*` del `.env`
- **Validado**:
  - `GET /api/client/catalog/channels` → 24 canales ✅
  - `canal-1` (co-main) → `http://38.210.187.13:8002/TeleNostalgia/index.m3u8` ✅
  - `canal-5` (ec-main) → `http://181.78.246.211:8002/GOLDEN_PLUS/index.m3u8` ✅

### Qué entregó el Bloque 1 (Observabilidad + Hardening)

**Backend:**
- `GET /api/admin/metrics` — sesiones activas, Redis latencia, Postgres OK, Flussonic reachable
- `GET /api/admin/sessions/live` — todas las sesiones IPTV activas con username, device, IP, heartbeat
- `GET /api/admin/nodes/health` — health por nodo Flussonic (latencia ms, stream_count)
- Background task `_cleanup_expired_sessions` — corre cada 15 min, marca expiradas como revocadas

**Frontend:**
- `hlsController.ts` hardened: MEDIA_ERROR → `recoverMediaError()` una vez, luego fatal;
  NETWORK_ERROR → retry x3 con backoff 1s/2s/4s; callbacks `onRetrying` / `onRecovered`
- `playbackRenewal.ts` (nuevo): renueva JWT de playback `renewSkewSeconds` antes de expirar;
  se conectará con signed URLs en Fase 4.2

---

## FASE 4 — BLOQUES PENDIENTES

### Bloque 2: Signed Playback URLs (Fase 4.2)

**Objetivo:** que nadie pueda ver un stream sin token válido de Nexora.

El endpoint `/api/stb/auth/validate` ya existe y valida el JWT de playback.
Lo que falta es activar el backend-auth en Flussonic y endurecer la validación.

```
Flujo objetivo:
  1. POST /api/client/playback/authorize → { playback_url: ".../{stream}?token={jwt}", token, expires_in }
  2. hls.js carga playback_url con el token en la URL
  3. Flussonic llama a POST /api/stb/auth/validate?token={jwt}
  4. Nexora valida: firma JWT + Redis nexora:playback:{jti} + ZSET activo + sesión no revocada
  5. 200 → Flussonic sirve el segmento | 401 → Flussonic bloquea

Pasos:
  a. Configurar en Flussonic: auth_backend = http://nexora_api:8000/api/stb/auth/validate
  b. Actualizar stream_hls_url() para append ?token={jwt} cuando backend-auth está activo
  c. playbackRenewal.ts ya existe — al renovar JWT, llamar hls.reload() con nueva URL
  d. Validar que sin token la URL devuelve 401 desde Flussonic
```

### Bloque 3: Pruebas de stress Playback (Fase 4.1 restante)

Escenarios pendientes de validar con métricas encendidas (`/admin/metrics`):

```
□ Cambio rápido de canales (5 canales en 30s) — verificar sesiones zombie en /sessions/live
□ Playback continuo 3-6 horas — verificar memory leaks browser, ZSET limpieza
□ Reconexión internet: desconectar WiFi 30s, reconectar — hls.js retry debe recuperar
□ Reinicio backend: docker compose restart api — cliente debe reconectar solo
□ Reinicio Redis: docker compose restart redis — ZSET vacío, authorize debe funcionar igual
□ 3 usuarios simultáneos mismo suscriptor — validar device limit + ZSET concurrencia
□ Token expiration: esperar 60s sin renovar — validar que playbackRenewal.ts renueva OK
□ Heartbeat timeout: parar heartbeat 3 min — ZSET debe expirar, conexión cortada
```

Comandos para monitorear durante stress:
```bash
# Sesiones activas en tiempo real
curl -s http://localhost:8000/api/admin/sessions/live -H "Authorization: Bearer {admin_token}"

# Métricas del sistema
curl -s http://localhost:8000/api/admin/metrics -H "Authorization: Bearer {admin_token}"

# Logs del API
docker logs -f nexora_api

# ZSET Redis directamente
docker exec nexora_redis redis-cli ZRANGE "nexora:active_conns:{subscriber_uuid}" 0 -1 WITHSCORES
```

### Bloque 4: Observabilidad extendida (Fase 4.3)

Métricas adicionales pendientes:
```
□ Contador de playback_failures (incrementar en validate() cuando retorna 401/403)
□ Alertas streams DOWN: tarea periódica que llama get_stream_status() y notifica
□ /admin/streams mejorado: incluir is_active de DB vs alive de Flussonic
□ Logs estructurados con correlation_id por request (structlog)
□ Evento buffering en hls.js → POST a Nexora para analytics (opcional)
```

### Bloque 5: Multi-Flussonic Registry formal (Fase 4.4)

El `.env` ya tiene ec-main y co-main. La función `get_flussonic_node_client()` es el stub.
Falta la estructura formal:

```python
# Futuro: app/integrations/flussonic_registry.py
class FlussonicNode(BaseModel):
    node_id: str      # "ec-main", "co-main"
    base_url: str
    region: str       # "EC", "CO"
    priority: int     # 1 = primario, 2 = fallback
    is_healthy: bool  # actualizado por health check periódico

class FlussonicRegistry:
    def get_node(self, node_id: str) -> FlussonicNode | None: ...
    def best_node_for_region(self, region: str) -> FlussonicNode: ...
    def fallback_node(self, failed_node_id: str) -> FlussonicNode | None: ...
```

Sin geo-routing todavía. Solo preparar estructura y health check periódico.

### Bloque 6: Deploy Producción (Fase 4.5)

```
□ SECRET_KEY: reemplazar "CHANGE_ME_64_random_chars_here" por 64 chars reales
  python -c "import secrets; print(secrets.token_hex(32))"
□ DEBUG=false → CORS usa _WEB_ORIGINS explícitos, allow_credentials=True
□ docker-compose.production.yml separado (sin --reload, sin volúmenes de código)
□ nginx externo: reverse proxy HTTPS delante de nexora_api:8000
□ Cloudflare: dominio + SSL
□ Rate limits más estrictos en producción
□ Secure headers: X-Frame-Options, X-Content-Type-Options, HSTS
□ nexora_web_player en puerto 443 o detrás de dominio (no exponer :5173)
```

---

## RESTRICCIONES DEL PROYECTO (no cambiar sin discusión)

- No PHP para módulos nuevos
- No MySQL en módulos nuevos
- No python-jose — usar PyJWT[crypto]
- No asyncpg — usar psycopg[binary]
- No comenzar Android TV hasta que playback, sesiones y observabilidad estén estables
- No exponer credenciales Flussonic en ninguna respuesta de API
- Flussonic es READ ONLY desde Nexora — nunca crear/modificar/eliminar streams
- Nexora NO hace proxy de video — cliente reproduce directo desde Flussonic
- No exponer `stream_key` al cliente — solo `channel_key`
- Docker: servicios usan nombres de contenedor (redis, postgres), NO localhost
- No UI en este repo — está en `e:/WEBSITE/nexora_app`
- Todo flujo nuevo pasa por Client API (`/api/client/*`)

---

## COMANDOS ÚTILES

```bash
# Levantar todos los servicios
docker compose up -d

# Verificar estado
docker ps --filter "name=nexora" --format "{{.Names}}: {{.Status}}"
curl http://localhost:8000/health

# Reiniciar solo la API (tras cambios en código — ya usa --reload pero por si acaso)
docker compose up -d api

# Reconstruir y reiniciar web player (tras cambios React)
docker compose build web_player && docker compose up -d web_player

# Re-importar canales M3U (idempotente)
docker exec nexora_api python scripts/import_m3u_channels.py

# Ejecutar migraciones pendientes
docker exec nexora_api python -m alembic upgrade head

# Login admin
curl -X POST http://localhost:8000/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"Admin1234!"}'

# Login suscriptor de prueba
curl -X POST http://localhost:8000/api/client/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"testuser1","password":"NexoraTest123!","device_id":"test-device-001","device_type":"web_player","model":"test","brand":"Nexora","os_version":"test"}'

# Métricas del sistema (requiere admin token)
curl http://localhost:8000/api/admin/metrics -H "Authorization: Bearer {TOKEN}"

# Sesiones activas en tiempo real
curl http://localhost:8000/api/admin/sessions/live -H "Authorization: Bearer {TOKEN}"

# Health nodos Flussonic
curl http://localhost:8000/api/admin/nodes/health -H "Authorization: Bearer {TOKEN}"

# Ver catálogo de canales (requiere client token)
curl http://localhost:8000/api/client/catalog/channels -H "Authorization: Bearer {TOKEN}"

# Ver logs en tiempo real
docker logs -f nexora_api
docker logs -f nexora_web_player
```
