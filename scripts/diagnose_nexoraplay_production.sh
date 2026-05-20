#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPOSE_FILE="$PROJECT_DIR/docker-compose.production.yml"
ENV_FILE="$PROJECT_DIR/.env.production"

cd "$PROJECT_DIR"

echo "== Host =="
hostname -f 2>/dev/null || hostname
date -Is

echo
echo "== DNS =="
getent ahostsv4 nexoraplay.net || true
getent ahostsv4 www.nexoraplay.net || true

echo
echo "== Ports listening =="
ss -ltnp | grep -E ':(22|80|443)\b' || true

echo
echo "== UFW =="
ufw status verbose 2>/dev/null || true

echo
echo "== Certificates =="
certbot certificates 2>/dev/null || true
ls -la /etc/letsencrypt/live/nexoraplay.net 2>/dev/null || true

echo
echo "== Docker Compose =="
if [ -f "$ENV_FILE" ]; then
  docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" ps || true
  docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" config --quiet || true
else
  echo "Missing $ENV_FILE"
fi

echo
echo "== Container logs: nginx =="
docker logs --tail 120 nexora_nginx 2>&1 || true

echo
echo "== Container logs: api =="
docker logs --tail 120 nexora_api 2>&1 || true

echo
echo "== Local HTTP checks =="
curl -fsSI -H "Host: nexoraplay.net" http://127.0.0.1/health 2>&1 || true
curl -kfsSI -H "Host: nexoraplay.net" https://127.0.0.1/health 2>&1 || true
