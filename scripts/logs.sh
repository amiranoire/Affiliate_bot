#!/bin/bash
# scripts/logs.sh - Log viewing script

BOT_DIR="/opt/telegram-bot"
SERVICE_NAME="telegram-bot"

echo "📋 Bot Logs Viewer"
echo "=================="
echo ""
echo "Choose an option:"
echo "1. View application logs (bot.log)"
echo "2. View system logs (journalctl)"
echo "3. Follow live logs"
echo "4. Search logs"
echo ""
read -p "Enter choice (1-4): " choice

case $choice in
    1)
        echo "📄 Application logs:"
        if [ -f "$BOT_DIR/bot.log" ]; then
            less "$BOT_DIR/bot.log"
        else
            echo "❌ Log file not found"
        fi
        ;;
    2)
        echo "📄 System logs:"
        journalctl -u "$SERVICE_NAME" --no-pager
        ;;
    3)
        echo "📡 Following live logs (Ctrl+C to stop):"
        if [ -f "$BOT_DIR/bot.log" ]; then
            tail -f "$BOT_DIR/bot.log"
        else
            journalctl -u "$SERVICE_NAME" -f
        fi
        ;;
    4)
        read -p "Enter search term: " search_term
        echo "🔍 Searching for '$search_term':"
        if [ -f "$BOT_DIR/bot.log" ]; then
            grep -n "$search_term" "$BOT_DIR/bot.log"
        else
            journalctl -u "$SERVICE_NAME" | grep "$search_term"
        fi
        ;;
    *)
        echo "❌ Invalid choice"
        exit 1
        ;;
esac
