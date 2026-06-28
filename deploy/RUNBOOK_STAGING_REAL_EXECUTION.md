# Runbook — Ejecución de STAGING REAL (PR #1)

> Para correr en el **host staging** (modelo A: tú ejecutas; sin SSH para Claude). Valida el P0 completo con Nginx real + Redis/PG reales + backend de PR #1 + orígenes Flussonic/Astra reales (read-only).
>
> **No producción · no DB de prod · no modificar Flussonic/Astra · no Nginx en prod · no merge de PR #1.** Secretos solo en `.env.staging` (gitignored). No imprimir tokens/credenciales.
>
> Artefactos: `docker-compose.staging.yml`, `deploy/nginx/nexoraplay.staging.conf`, `.env.staging.example`.
> Datos asumidos: dominio `staging.nexoraplay.net`, SSL Let's Encrypt, nodos ec-main/co-main/ec-quito. `ec-quito` **sin canales** todavía (location preparada).

## 0. Pre-requisitos (tus pendientes)
- [ ] IP/host staging definido (separado de prod).
- [ ] DNS `A staging.nexoraplay.net → <IP staging>` creado y propagado.
- [ ] Egress del host a `181.78.246.211:8002`, `38.210.187.13:8002`, `45.70.202.171:8002`.
- [ ] **Credencial `ec-quito` rotada**; credenciales read-only de los 3 nodos listas para `.env.staging`.
- [ ] Firewall: abrir 80/443; no exponer 8000/5432/6379 (el compose ya los liga a `127.0.0.1`).

## 1. Instalar Docker (si falta)
```bash
# Ubuntu 24.04
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER && newgrp docker
docker --version && docker compose version
```

## 2. Clonar repo + checkout PR #1
```bash
git clone https://github.com/eiderinternetcu-cmd/Nexora_Portal_v3.git nexora
cd nexora
git checkout feat/p0-auth-playback-entitlements      # PR #1
```

## 3. Crear `.env.staging` (NO commitear)
```bash
cp .env.staging.example .env.staging
# editar .env.staging y completar:
#   SECRET_KEY (aleatorio fuerte)
#   POSTGRES_PASSWORD
#   FLUSSONIC_READONLY_USER/PASSWORD, FLUSSONIC_CO_MAIN_USER/PASSWORD,
#   FLUSSONIC_EC_QUITO_USER/PASSWORD  (credencial ROTADA)
grep -q '^\.env\.staging$' .gitignore || echo '.env.staging' >> .gitignore   # asegurar gitignore
```

## 4. SSL (Let's Encrypt) — obtener cert ANTES de levantar Nginx con SSL
```bash
sudo mkdir -p deploy/letsencrypt deploy/certbot-www
# opción standalone (puerto 80 libre):
sudo docker run --rm -p 80:80 \
  -v "$PWD/deploy/letsencrypt:/etc/letsencrypt" \
  certbot/certbot certonly --standalone -d staging.nexoraplay.net \
  --agree-tos -m admin@nexoraplay.net --no-eff-email
# verifica: deploy/letsencrypt/live/staging.nexoraplay.net/fullchain.pem
```

## 5. Levantar servicios (flags OFF)
```bash
docker compose -f docker-compose.staging.yml --env-file .env.staging up -d --build
curl -fsS http://127.0.0.1:8000/health          # API ok
docker compose -f docker-compose.staging.yml exec nginx nginx -t   # conf ok
# (opcional) web player:
docker compose -f docker-compose.staging.yml --profile webplayer up -d
```

## 6. DB: migraciones + import + seed + auditoría
```bash
C="docker compose -f docker-compose.staging.yml exec -T api"
$C alembic upgrade head
$C python scripts/import_m3u_channels.py          # CHANNEL_SOURCE_URL_MODE=relative → source_url same-origin
$C python scripts/seed_plan_channels.py
# Auditoría: DEBE dar 0 RISK (exit 0)
$C sh -c 'ALLOWED_STREAM_ORIGINS=https://staging.nexoraplay.net \
  ALLOWED_STREAM_NODES=ec-main,co-main,ec-quito \
  python scripts/audit_channel_source_urls.py'
# Si hay RISK → backfill y re-auditar:
#   $C python scripts/backfill_channel_source_urls_same_origin.py --apply
```

## 7. Smoke tests (enmascarar tokens; no pegar URLs con token completo)
> Sustituye `<key>` por un stream_key real (lo ves en la DB; nunca se expone al cliente).
```bash
# 7.1 sin token → 401
curl -sk -o /dev/null -w "%{http_code}\n" https://staging.nexoraplay.net/stream/co-main/<key>/index.m3u8   # 401

# 7.2 login → authorize → playback_url (con SIGNED_URL_ENFORCE ON, ver paso 8)
#   POST /api/client/auth/login  → access_token (no imprimir completo)
#   POST /api/client/playback/authorize {channel_id, device_id} → playback_url same-origin con ?token=
#   GET  playback_url            → manifest .m3u8 200 (real Flussonic/Astra)
#   GET  <mismo dir>/<segmento>.ts (sin token) → 200 (grant Redis)
#   GET  /stream/co-main/<otro>/<segmento>.ts (sin token) → 401 (cross-stream)

# 7.3 catálogo: stream_key NO expuesto
curl -sk https://staging.nexoraplay.net/api/client/catalog/channels -H "Authorization: Bearer <tok>" | grep -c stream_key   # 0

# 7.4 logs sin token
docker compose -f docker-compose.staging.yml exec nginx sh -c "grep -c 'token=' /var/log/nginx/staging.access.log"   # 0
```

## 8. Activación gradual de flags (re-test entre cada uno)
Editar `.env.staging` y `docker compose ... up -d api` tras cada cambio:
1. `ENTITLEMENT_ENFORCE=true` → canal en plan 200; **fuera de plan 403 `CHANNEL_NOT_INCLUDED`**; device no registrado **403 `DEVICE_NOT_REGISTERED`**.
2. `JWT_REQUIRE_AUD=true` → token cliente en `/api/admin` → 401; login/refresh OK.
3. `SIGNED_URL_ENFORCE=true` → `playback_url` lleva `?token=`; sin token 401; con token 200; segmentos por grant 200.
4. `PLAYBACK_IP_BINDING_MODE=soft` → IP distinta 200 + warning en logs (no rompe). `strict` solo si la red del cliente es estable.
> Validar **continuidad larga** (> 3 min: TTL token 60s vs grant 180s + heartbeat) sin cortes.

## 9. Rollback (probar antes de confiar)
| Nivel | Acción |
|---|---|
| Flags | poner `false`/`off` en `.env.staging` + `docker compose ... up -d api` (sin redeploy) |
| Nginx | restaurar conf de backup + `nginx -t` + reload (contenedor nginx) |
| Migración | `docker compose ... exec -T api alembic downgrade -1` (005 reversible) |
| Grants | `docker compose ... exec redis redis-cli --scan --pattern 'nexora:stream_grant:*' \| xargs redis-cli del` |
| Todo | `docker compose -f docker-compose.staging.yml down` (volúmenes persisten salvo `-v`) |

## 10. Criterio de aprobación staging → (futuro) producción
- [ ] Auditoría source_url **0 RISK**.
- [ ] Smoke tests §7 OK; flags graduales §8 OK; continuidad larga OK.
- [ ] Logs sin token (Nginx + backend).
- [ ] Rollback probado.
- [ ] Credencial `ec-quito` rotada y solo en `.env.staging`.
- [ ] **Nada** aplicado a producción / Flussonic / Nginx-prod durante el proceso.

> Producción será un runbook separado (gradual, seed primero, rollback por flag). Este runbook **no** despliega a producción ni mergea PR #1.
