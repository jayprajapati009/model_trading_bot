#!/usr/bin/env bash
# ── Digital Ocean droplet setup script ────────────────────────────────────────
# Run once as root:  bash setup.sh
# Sets up Python venv, installs deps, enables systemd service.

set -euo pipefail

BOT_DIR="$(cd "$(dirname "$0")" && pwd)"
SERVICE_FILE="/etc/systemd/system/trading_bot.service"
VENV="$BOT_DIR/venv"
PYTHON=python3

echo "==> Setting up trading bot in $BOT_DIR"

# System deps
apt-get update -qq && apt-get install -y -qq python3 python3-pip python3-venv

# Virtual environment
$PYTHON -m venv "$VENV"
source "$VENV/bin/activate"
pip install --upgrade pip -q
pip install -r "$BOT_DIR/requirements.txt" -q
deactivate

echo "==> Python environment ready"

# Data directories
mkdir -p "$BOT_DIR/data/notes" "$BOT_DIR/logs"

# Systemd service
cat > "$SERVICE_FILE" <<EOF
[Unit]
Description=Trading Bot (model portfolio)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=root
WorkingDirectory=$BOT_DIR
ExecStart=$VENV/bin/python $BOT_DIR/main.py
Restart=always
RestartSec=15
Environment=PYTHONUNBUFFERED=1
Environment=DASHBOARD_PORT=8080
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable trading_bot
systemctl start trading_bot

echo ""
echo "==> Trading bot service started!"
echo ""
echo "    Check status : systemctl status trading_bot"
echo "    View logs    : journalctl -u trading_bot -f"
echo "    Dashboard    : http://$(curl -s ifconfig.me):8080"
echo ""
echo "    To access via SSH tunnel from your laptop:"
echo "    ssh -L 8080:localhost:8080 root@<YOUR_DROPLET_IP>"
echo "    Then open: http://localhost:8080"
