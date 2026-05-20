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

echo "[1/7] Installing certbot and ufw..."
apt-get update
apt-get install -y certbot ufw ca-certificates curl

echo "[2/7] Preparing ACME webroot..."
mkdir -p /var/www/certbot

if [ ! -f "/etc/letsencrypt/live/$DOMAIN/fullchain.pem" ]; then
  echo "[3/7] Requesting first Let's Encrypt certificate..."
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
  echo "[3/7] Certificate already exists, skipping first issuance."
fi

echo "[4/7] Installing renewal hooks for standalone renewal..."
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

echo "[5/7] Building and starting production containers..."
docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" up -d --build

echo "[6/7] Running database migrations and channel sync..."
docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" exec -T api alembic upgrade head
docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" exec -T api python scripts/import_m3u_channels.py

echo "[7/7] Running quick checks..."
docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" ps
curl -fsSI "https://$DOMAIN" | head -20
curl -fsS "https://$DOMAIN/health"

echo
echo "Production edge is up for https://$DOMAIN and https://$WWW_DOMAIN"
echo "After login/catalog/playback validation, run:"
echo "  sudo bash scripts/lockdown_ufw_production.sh"
