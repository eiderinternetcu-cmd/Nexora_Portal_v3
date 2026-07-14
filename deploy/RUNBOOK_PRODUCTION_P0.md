# Runbook — P0 rollout en PRODUCCIÓN (nexoraplay.net)

> Servidor `45.184.225.4`. Stack vivo = **`docker-compose.production.yml`** (NO `docker-compose.yml`).
> Activación **gradual, un flag por vez**, con ventana de observación y **rollback por flag**.
> Flussonic/Astra son **read-only**: nunca se tocan. No imprimir secretos ni tokens completos.

Flags P0 (en `.env.production`), por orden de activación:

1. `ENTITLEMENT_ENFORCE=true` — playback niega canales fuera del plan (`plan_channels`). **Activo.**
2. `JWT_REQUIRE_AUD=true` — iss/aud/type estrictos por superficie. **Activo.**
3. `SIGNED_URL_ENFORCE=true` — `playback_url` lleva `?token=` y `/stream/*` lo exige. **Activo (Fase 2C).**
4. `PLAYBACK_IP_BINDING_MODE=off|soft|strict` — binding IP del token. **off (Fase 2D pendiente).**

`STREAM_AUTH_CACHE_TTL_SECONDS` (default 180) = TTL del grant de segmentos.

---

## FASE 2C — Signed URLs + Nginx `auth_request`

### Pre-requisitos
- `playback_url` **same-origin**: en `.env.production`, `FLUSSONIC_BASE_URL=https://nexoraplay.net/stream/ec-main`
  y `FLUSSONIC_CO_MAIN_BASE_URL=https://nexoraplay.net/stream/co-main`. Así `stream_hls_url()` ya
  emite `https://nexoraplay.net/stream/<node>/<stream>/index.m3u8` y `_maybe_sign` le añade `?token=`.
- Endpoint `/internal/stream-auth/validate` desplegado (router `app/api/internal/stream_auth.py`).
- Backup obligatorio de `.env.production` y de `deploy/nginx/nexoraplay.conf` antes de tocar nada.
- Cutover preferentemente **sin sesiones activas** (verificar Redis `active_conns` / logs `/stream`).

### Orden correcto (CRÍTICO)
**Primero el flag, después el gate Nginx.** Razón: con Nginx aún en plain-proxy, activar
`SIGNED_URL_ENFORCE` sólo añade `?token=` a la URL; Flussonic ignora el query → **no rompe**.
Si se activara `auth_request` *antes* del flag, el manifest sin token daría 401 → corta playback.

```bash
# 0) Backups
TS=$(date +%Y%m%d_%H%M%S)
sudo cp /opt/nexora_api/.env.production /opt/backups/env.production.bak-2c-$TS
sudo cp /opt/nexora_api/deploy/nginx/nexoraplay.conf /opt/backups/nexoraplay.conf.bak-2c-$TS

# 1) Flag primero
#    añadir/poner SIGNED_URL_ENFORCE=true en .env.production
cd /opt/nexora_api
sudo docker compose -f docker-compose.production.yml up -d --force-recreate --no-deps api
#    validar: authorize → playback_url con ?token= ; manifest vía Nginx (aún plain-proxy) → 200

# 2) Gate Nginx después
#    desplegar deploy/nginx/nexoraplay.conf con auth_request (ver abajo)
sudo docker exec nexora_nginx nginx -t        # debe decir "test is successful"
sudo docker exec nexora_nginx nginx -s reload # reload SOLO si nginx -t pasa
```

### El gate Nginx (qué hace `nexoraplay.conf`)
- `location = /__stream_auth` (internal) → `proxy_pass .../internal/stream-auth/validate`.
- `location ^~ /stream/{ec-main,co-main}/` → `auth_request /__stream_auth;` + `error_page 401/403 = @stream_denied;`
  antes de proxiar a Flussonic.
- `log_format stream_safe` + `access_log /dev/stdout stream_safe;` en las locations `/stream/*` y en
  `@stream_denied` → el `?token=` no aparece en logs.

### ⚠️ Hallazgo Nginx (costó horas — documentar)
Dentro del subrequest de `auth_request` (a `/__stream_auth`), las variables
`$request_uri` / `$args` / `$arg_token` / `$uri` resuelven al **subrequest**
(`/__stream_auth`), **no** al request original → `X-Original-URI`/`X-Playback-Token`
llegaban vacíos → 401 con token válido.

**Solución aplicada (real en prod):**
```nginx
# en la location ^~ /stream/<node>/ (fase rewrite, ANTES del subrequest):
set $stream_orig_uri $request_uri;   # los subrequests COMPARTEN el array de vars `set` del padre
set $stream_token    $arg_token;

# maps a nivel http{} para derivar node/stream_key del URI capturado:
map $stream_orig_uri $stream_node_v { ~^/stream/(?<n>[^/]+)/        $n; default ""; }
map $stream_orig_uri $stream_key_v  { ~^/stream/[^/]+/(?<k>[^/?]+)  $k; default ""; }

# en location = /__stream_auth: node+stream_key por query (no secretos), token por header
proxy_pass http://nexora_api:8000/internal/stream-auth/validate?node=$stream_node_v&stream_key=$stream_key_v;
proxy_set_header X-Playback-Token $stream_token;
proxy_set_header X-Real-IP        $remote_addr;   # IP real del cliente (grant + IP-binding)
proxy_set_header X-Forwarded-For  $remote_addr;
```
> Nota: NO editar la app en producción para depurar (queda bloqueado y es lo correcto).
> Toda la investigación se hizo **config-only** (probes token-safe vía `proxy_pass` query + access log).

### Validación del gate (request-level)
```
manifest + token                         -> 200  (siembra grant)
segmento/variant/manifest tokenless,
  mismo node+stream+IP                    -> 200  (grant; se renueva por request)
sin grant / otro stream / otro node      -> 401
logs Nginx /stream con token=            -> 0
```

### Validación de continuidad (larga, ~10–15 min)
Con navegador real o cliente HLS simulado contra un canal **ec-main** funcional:
- manifest+token 200; tokenless posteriores 200 por grant; **grant TTL se mantiene ~180 (renovado)**;
- el **token de 60 s queda superado sin corte** (primer tokenless 2xx tras 60 s);
- cross-stream / otro-node sin grant → 401; logs sin tokens; `/health` y site 200.

Resultado 2026-06-28 (canal-10 / ec-main / Cine_Infantil, 13 min, 396 req válidas):
**0 fallos en peticiones válidas, grant TTL constante 180, negativos 401, logs limpios → CONTINUIDAD OK.**

### Comportamientos a tener en cuenta
- Flussonic **eco-ea** el token Nexora a las sub-playlists (`tracks-*/mono.m3u8?token=<jwt>`):
  esos fetches re-validan el JWT (ok mientras no expire); el player renueva (~45 s,
  `reissuePlayback` + `hls.reload`) refrescando el token en la URL.
- El **grant es auto-renovable** mientras fluyan requests del mismo IP+stream → desacopla el
  stream en curso del ciclo de sesión/heartbeat. Implica **latencia de revocación**: revocar una
  sesión no corta de inmediato un stream con grant vivo (se corta en cambio de canal, gap >180 s,
  o cuando una renovación con token falla). *Hardening sugerido (no hecho):* que el gate, ante un
  token presente-pero-expirado, caiga al grant en vez de 401 duro, y/o que el grant re-valide la
  sesión periódicamente o tenga vida máxima independiente de la renovación.

---

## Rollback (por flag — preferente, sin redeploy)

```bash
# Quitar la línea del flag de .env.production (o restaurar el backup) y recrear api:
cd /opt/nexora_api
sudo docker compose -f docker-compose.production.yml up -d --force-recreate --no-deps api

# Rollback del gate Nginx (restaurar conf + reload):
sudo cp /opt/backups/nexoraplay.conf.bak-2c-<TS> /opt/nexora_api/deploy/nginx/nexoraplay.conf
sudo docker exec nexora_nginx nginx -t
sudo docker exec nexora_nginx nginx -s reload
# validar playback normal y reportar.
```

| Nivel | Acción |
|---|---|
| **Flag** | `SIGNED_URL_ENFORCE=false` → recrear api. Revierte comportamiento sin redeploy. |
| **Nginx** | Restaurar `nexoraplay.conf.bak-2c-*` + `nginx -t` + `nginx -s reload`. |
| **Grant cache** | Caduca solo (TTL). `redis-cli --scan --pattern 'nexora:stream_grant:*' | xargs redis-cli del` si urge. |

---

## FASE 2D — IP binding (pendiente, requiere autorización)
`PLAYBACK_IP_BINDING_MODE=soft` (warn+permite; observar mismatches por clientes móviles antes de
`strict`). El token ya lleva `cip`; el gate ya pasa `X-Real-IP=$remote_addr` real al endpoint.
Activar con ventana/observación y rollback por flag.
