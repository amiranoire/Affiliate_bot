#!/bin/bash
# scripts/check-bot.sh - Health check script

echo "🔍 Checking bot health..."

BOT_DIR="/opt/telegram-bot"
SERVICE_NAME="telegram-bot"

if systemctl is-active --quiet "$SERVICE_NAME"; then
    echo "✅ Service is running"
else
    echo "❌ Service is not running"
    systemctl status "$SERVICE_NAME" --no-pager
    exit 1
fi

if [ -f "$BOT_DIR/employee_tracker.db" ]; then
    echo "✅ Database file exists"
    cd "$BOT_DIR"
    sudo -u botuser bash -c "source venv/bin/activate && python3 -c 'import sqlite3; conn = sqlite3.connect(\"employee_tracker.db\"); conn.close(); print(\"✅ Database is accessible\")'"
else
    echo "⚠️  Database file not found"
fi

if [ -f "$BOT_DIR/bot.log" ]; then
    echo "✅ Log file exists"
    echo "📝 Last 5 log entries:"
    tail -5 "$BOT_DIR/bot.log"
else
    echo "⚠️  Log file not found"
fi

cd "$BOT_DIR"
sudo -u botuser bash -c "source venv/bin/activate && python3 -c 'from config import config; print(\"✅ Configuration loaded successfully\")'" 2>/dev/null || echo "❌ Configuration error"

echo "✅ Health check completed"
