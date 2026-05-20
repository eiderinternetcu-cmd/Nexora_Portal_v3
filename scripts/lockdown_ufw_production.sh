#!/usr/bin/env bash
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
  echo "Run as root: sudo bash scripts/lockdown_ufw_production.sh"
  exit 1
fi

echo "This will allow only SSH(22), HTTP(80), and HTTPS(443) through UFW."
read -r -p "Have you verified admin login, client login, catalog, and playback? [y/N] " answer
case "$answer" in
  y|Y|yes|YES) ;;
  *)
    echo "Cancelled."
    exit 1
    ;;
esac

ufw --force reset
ufw default deny incoming
ufw default allow outgoing
ufw allow 22/tcp
ufw allow 80/tcp
ufw allow 443/tcp
ufw --force enable
ufw status verbose
