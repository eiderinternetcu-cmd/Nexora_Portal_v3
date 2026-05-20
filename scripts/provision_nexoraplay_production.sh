#!/usr/bin/env bash
set -euo pipefail

DOMAIN="nexoraplay.net"
WWW_DOMAIN="www.nexoraplay.net"
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPOSE_FILE="$PROJECT_DIR/docker-compose.production.yml"
ENV_FILE="$PROJECT_DIR/.env.production"

if [ "$(id -u)" -ne 0 ]; then
  echo "Run as root: sudo bash scripts/provision_nexoraplay_production.sh"
  exit 1
fi

if [ ! -f "$ENV_FILE" ]; then
  echo "Missing $ENV_FILE. Copy .env.production.example and fill secrets first."
  exit 1
fi

if ! docker compose version >/dev/null 2>&1; then
  echo "Docker with the Compose plugin is required before running this script."
  exit 1
fi

set -a
# shellcheck disable=SC1090
. "$ENV_FILE"
set +a

if [ -z "${LETSENCRYPT_EMAIL:-}" ]; then
  echo "LETSENCRYPT_EMAIL is required in .env.production"
  exit 1
fi

echo "[1/8] Installing certbot and ufw..."
apt-get update
apt-get install -y certbot ufw ca-certificates curl

echo "[2/8] Preparing ACME webroot..."
mkdir -p /var/www/certbot

echo "[3/8] Ensuring SSH/HTTP/HTTPS are allowed if UFW is active..."
ufw allow 22/tcp >/dev/null || true
ufw allow 80/tcp >/dev/null || true
ufw allow 443/tcp >/dev/null || true

if [ ! -f "/etc/letsencrypt/live/$DOMAIN/fullchain.pem" ]; then
  echo "[4/8] Requesting first Let's Encrypt certificate..."
  systemctl stop nginx 2>/dev/null || true
  docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" stop nginx 2>/dev/null || true
  certbot certonly \
    --standalone \
    --non-interactive \
    --agree-tos \
    --email "$LETSENCRYPT_EMAIL" \
    -d "$DOMAIN" \
    -d "$WWW_DOMAIN"
else
  echo "[4/8] Certificate already exists, skipping first issuance."
fi

echo "[5/8] Installing renewal hooks for standalone renewal..."
install -d /etc/letsencrypt/renewal-hooks/pre /etc/letsencrypt/renewal-hooks/post

cat >/etc/letsencrypt/renewal-hooks/pre/stop-nexora-nginx.sh <<HOOK
#!/usr/bin/env bash
docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" stop nginx >/dev/null 2>&1 || true
HOOK

cat >/etc/letsencrypt/renewal-hooks/post/start-nexora-nginx.sh <<HOOK
#!/usr/bin/env bash
docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" up -d nginx >/dev/null 2>&1 || true
HOOK

chmod +x /etc/letsencrypt/renewal-hooks/pre/stop-nexora-nginx.sh
chmod +x /etc/letsencrypt/renewal-hooks/post/start-nexora-nginx.sh
systemctl enable --now certbot.timer >/dev/null 2>&1 || true

echo "[6/8] Building and starting production containers..."
docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" up -d --build

echo "[7/8] Running database migrations and channel sync..."
docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" exec -T api alembic upgrade head
docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" exec -T api python scripts/import_m3u_channels.py

echo "[8/8] Running quick checks..."
docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" ps
curl -fsSI "https://$DOMAIN" | head -20
curl -fsS "https://$DOMAIN/health"

echo
echo "Production edge is up for https://$DOMAIN and https://$WWW_DOMAIN"
echo "After login/catalog/playback validation, run:"
echo "  sudo bash scripts/lockdown_ufw_production.sh"
