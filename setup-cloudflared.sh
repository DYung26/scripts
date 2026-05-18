#!/usr/bin/env bash
set -euo pipefail

read -rp "Tunnel name: " TUNNEL_NAME
read -rp "Hostname (e.g. relay.dyung.me): " TUNNEL_HOSTNAME
read -rp "Local port: " LOCAL_PORT
read -rp "Protocol [http/tcp] (default: http): " PROTOCOL
PROTOCOL="${PROTOCOL:-http}"

CLOUDFLARED_DIR="$HOME/.cloudflared"

echo ">>> Adding Cloudflare GPG key and apt source..."
sudo mkdir -p --mode=0755 /usr/share/keyrings
curl -fsSL https://pkg.cloudflare.com/cloudflare-main.gpg \
  | sudo tee /usr/share/keyrings/cloudflare-main.gpg >/dev/null
echo "deb [signed-by=/usr/share/keyrings/cloudflare-main.gpg] https://pkg.cloudflare.com/cloudflared jammy main" \
  | sudo tee /etc/apt/sources.list.d/cloudflared.list

echo ">>> Installing cloudflared..."
sudo apt-get update && sudo apt-get install -y cloudflared

echo ">>> Logging in to Cloudflare (browser will open)..."
cloudflared tunnel login

echo ">>> Creating tunnel: $TUNNEL_NAME..."
cloudflared tunnel create "$TUNNEL_NAME"

echo ">>> Routing DNS: $TUNNEL_HOSTNAME -> $TUNNEL_NAME..."
cloudflared tunnel route dns "$TUNNEL_NAME" "$TUNNEL_HOSTNAME"

TUNNEL_ID=$(cloudflared tunnel list --output json | grep -o '"id":"[^"]*"' | head -1 | cut -d'"' -f4)

echo ">>> Detected tunnel ID: $TUNNEL_ID"

CONFIG_FILE="$CLOUDFLARED_DIR/config.yml"
echo ">>> Writing config to $CONFIG_FILE..."

cat > "$CONFIG_FILE" <<EOF
tunnel: $TUNNEL_ID
credentials-file: $CLOUDFLARED_DIR/$TUNNEL_ID.json

ingress:
  - hostname: $TUNNEL_HOSTNAME
    service: ${PROTOCOL}://localhost:$LOCAL_PORT

  - service: http_status:404
EOF

echo ">>> Config written:"
cat "$CONFIG_FILE"

echo ">>> Installing cloudflared as a system service..."
sudo cloudflared service install
sudo systemctl enable --now cloudflared

echo ">>> Service status:"
systemctl status cloudflared --no-pager

echo ""
echo "Done. Tunnel '$TUNNEL_NAME' is running at $TUNNEL_HOSTNAME -> localhost:$LOCAL_PORT"
echo "To follow logs: journalctl -u cloudflared -f"
