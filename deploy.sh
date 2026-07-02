#!/usr/bin/env bash
# deploy.sh — pull latest code and restart the trading bot service
# Usage: bash deploy.sh

set -euo pipefail

SERVICE="trading_bot"
BOT_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "==> Pulling latest code..."
git -C "$BOT_DIR" pull origin main

echo "==> Restarting $SERVICE service..."
systemctl restart "$SERVICE"

echo "==> Waiting for service to come up..."
sleep 3

STATUS=$(systemctl is-active "$SERVICE" 2>/dev/null || echo "unknown")
if [ "$STATUS" = "active" ]; then
    echo ""
    echo "✓ $SERVICE is running."
    echo ""
    echo "  Logs  : journalctl -u $SERVICE -f"
    echo "  Status: systemctl status $SERVICE"
else
    echo ""
    echo "✗ Service did not start (status: $STATUS). Check logs:"
    echo "  journalctl -u $SERVICE -n 50 --no-pager"
    exit 1
fi
