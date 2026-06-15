#!/usr/bin/env bash
#
# setup-nginx-domain.sh
#
# Sets up an nginx reverse-proxy site for a given domain, pointing at a
# local backend port, and provisions an SSL certificate via certbot.
#
# Usage:
#   ./setup-nginx-domain.sh <domain> <proxy_port> [email]
#
# Example:
#   ./setup-nginx-domain.sh api.assessly.dyung.me 8000 you@example.com
#
# Must be run as root (or with sudo) on the target server.

set -e
set -o pipefail

DOMAIN="$1"
PROXY_PORT="$2"
EMAIL="$3"

if [[ -z "$DOMAIN" || -z "$PROXY_PORT" ]]; then
  echo "Usage: $0 <domain> <proxy_port> [email]"
  exit 1
fi

if [[ "$EUID" -ne 0 ]]; then
  echo "❌ This script must be run as root (use sudo)."
  exit 1
fi

SITES_AVAILABLE="/etc/nginx/sites-available/${DOMAIN}"
SITES_ENABLED="/etc/nginx/sites-enabled/${DOMAIN}"

SERVER_IP=$(curl -s -4 https://ifconfig.me || curl -s -4 https://api.ipify.org)

echo "🌐 This machine's public IP appears to be: ${SERVER_IP}"
echo "   Please ensure the DNS A record for ${DOMAIN} points to ${SERVER_IP}."
read -rp "Has the DNS A record been set and propagated? [y/N] " dns_confirm
if [[ ! "$dns_confirm" =~ ^[Yy]$ ]]; then
  echo "Aborted. Set the DNS A record and re-run this script."
  exit 1
fi

echo "📦 Installing nginx and certbot (if not already installed)..."
apt-get update -y
apt-get install -y nginx certbot python3-certbot-nginx

echo "📝 Writing initial HTTP-only nginx config for ${DOMAIN}..."
cat > "$SITES_AVAILABLE" <<EOF
server {
    listen 80;
    server_name ${DOMAIN};

    location / {
        proxy_pass http://localhost:${PROXY_PORT};
        proxy_http_version 1.1;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection 'upgrade';
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_cache_bypass \$http_upgrade;
    }

    client_max_body_size 10M;
}
EOF

echo "🔗 Symlinking into sites-enabled..."
ln -sf "$SITES_AVAILABLE" "$SITES_ENABLED"

echo "🧪 Testing nginx config..."
nginx -t

echo "🔄 Reloading nginx..."
systemctl reload nginx

echo "🔐 Requesting SSL certificate via certbot..."
if [[ -n "$EMAIL" ]]; then
  certbot --nginx -d "$DOMAIN" --non-interactive --agree-tos -m "$EMAIL" --redirect
else
  certbot --nginx -d "$DOMAIN" --non-interactive --agree-tos --register-unsafely-without-email --redirect
fi

echo "🧪 Re-testing nginx config after certbot edits..."
nginx -t

echo "🔄 Reloading nginx..."
systemctl reload nginx

echo "✅ Setup complete for https://${DOMAIN} -> http://localhost:${PROXY_PORT}"
echo "ℹ️  Config file: ${SITES_AVAILABLE}"
echo "ℹ️  Certbot auto-renewal is handled by its systemd timer (certbot.timer)."
