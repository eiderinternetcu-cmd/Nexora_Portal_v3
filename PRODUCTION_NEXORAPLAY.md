# Nexora production: nexoraplay.net

Target server: `45.184.225.4`

## DNS

Point both records to the server before issuing TLS:

```text
nexoraplay.net      A 45.184.225.4
www.nexoraplay.net  A 45.184.225.4
```

## Files

- `docker-compose.production.yml`: production stack. Only `nginx` publishes ports `80` and `443`.
- `deploy/nginx/nexoraplay.conf`: reverse proxy and TLS config.
- `.env.production.example`: copy to `.env.production` and fill production secrets.
- `scripts/provision_nexoraplay_production.sh`: installs Certbot/UFW, issues the cert, starts the stack.
- `scripts/lockdown_ufw_production.sh`: closes UFW after manual verification.

## Routing

- `/` -> `nexora_web_player:80`
- `/api/` -> `nexora_api:8000`
- `/docs`, `/redoc`, `/openapi.json` -> `nexora_api:8000`
- `/health` and `/api/health` -> `nexora_api:8000/health`
- `/stream/ec-main/*` -> `181.78.246.211:8002` (gated by `auth_request`)
- `/stream/co-main/*` -> `38.210.187.13:8002` (gated by `auth_request`)

The `/stream/*` routes keep HLS playback on HTTPS/same-origin so browsers do
not block the player as mixed content.

### Stream auth gate (FASE 2C — active in production)

With `SIGNED_URL_ENFORCE=true`, `playback_url` carries `?token=<jwt>` and every
`/stream/*` request is validated by FastAPI (`/internal/stream-auth/validate`)
via Nginx `auth_request` before proxying to Flussonic (read-only, untouched):

- Manifest with token → full token validation + seeds a short-lived Redis grant
  (`nexora:stream_grant:{node}:{stream_key}:{ip_hash}`, TTL ~180s, renewed per request).
- Tokenless segments/variant/manifest reloads of the **same** node+stream+client-IP
  pass via that grant; cross-stream / other-node / other-IP without a grant → 401.
- `log_format stream_safe` strips the `?token=` from `/stream` access logs.

> **Nginx finding (important):** inside an `auth_request` subrequest,
> `$request_uri` / `$args` / `$arg_token` resolve to the **subrequest**
> (`/__stream_auth`), NOT the original request. The original URI + token are
> captured in the `/stream/*` location (`set $stream_orig_uri $request_uri;`
> `set $stream_token $arg_token;` — subrequests share the parent's `set` vars),
> node/stream_key derived via `map`, and the token passed to the backend via the
> `X-Playback-Token` header (kept out of logs). See `deploy/RUNBOOK_PRODUCTION_P0.md`.

## Deploy

On the server, from the project directory:

```bash
cp .env.production.example .env.production
nano .env.production
sudo bash scripts/provision_nexoraplay_production.sh
```

The first certificate uses Certbot standalone, so ports `80` and `443` must be free during first issuance.
The script also runs Alembic migrations and syncs the 24-channel catalog with `scripts/import_m3u_channels.py`.

If the site does not answer on `80` or `443`, run:

```bash
sudo bash scripts/diagnose_nexoraplay_production.sh
```

The common failure modes are:

- DNS still has parking/default A records instead of only `45.184.225.4`.
- UFW or the host provider blocks `80`/`443`.
- The certificate was not issued, so the Nginx container exits at startup.
- The production stack was not started with `--env-file .env.production`.

## Verify before firewall lockdown

```bash
curl -I https://nexoraplay.net
curl https://nexoraplay.net/health
curl https://nexoraplay.net/api/health
docker compose --env-file .env.production -f docker-compose.production.yml ps
```

Then verify in the UI:

- Admin login: `POST /api/admin/auth/login`
- Client login: `POST /api/client/auth/login`
- Catalog: `GET /api/client/catalog/channels` returns the 24 expected channels
- Playback authorize: `POST /api/client/playback/authorize`

After those checks pass:

```bash
sudo bash scripts/lockdown_ufw_production.sh
```
