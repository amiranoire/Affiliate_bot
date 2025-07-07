#!/bin/bash
# scripts/update-bot.sh - Quick update script

echo "🔄 Quick bot update..."

BOT_DIR="/opt/telegram-bot"
SERVICE_NAME="telegram-bot"

cd "$BOT_DIR" || exit 1

echo "🛑 Stopping bot..."
systemctl stop "$SERVICE_NAME"

echo "📥 Pulling latest changes..."
sudo -u botuser git pull origin main

echo "📦 Updating dependencies..."
sudo -u botuser bash -c "source venv/bin/activate && pip install -r requirements.txt"

echo "🚀 Starting bot..."
systemctl start "$SERVICE_NAME"

sleep 2
if systemctl is-active --quiet "$SERVICE_NAME"; then
    echo "✅ Bot updated and running successfully!"
else
    echo "❌ Bot failed to start. Check logs:"
    journalctl -u "$SERVICE_NAME" --no-pager -n 10
fi
