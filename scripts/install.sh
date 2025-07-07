#!/bin/bash
# scripts/install.sh - One-command installer

echo "🚀 Telegram Employee Bot - One-Click Installer"
echo "=============================================="

if [ "$EUID" -ne 0 ]; then
    echo "❌ Please run as root"
    exit 1
fi

echo "📥 Downloading setup script..."
curl -sSL https://raw.githubusercontent.com/amiranoire/Affiliate_bot/main/scripts/setup.sh -o /tmp/setup.sh

chmod +x /tmp/setup.sh
/tmp/setup.sh

echo ""
echo "🎉 Installation completed!"
echo ""
echo "⚡ Quick start commands:"
echo "  sudo nano /opt/telegram-bot/.env     # Configure bot"
echo "  sudo systemctl start telegram-bot    # Start bot"
echo "  sudo systemctl status telegram-bot   # Check status"
echo "  /opt/telegram-bot/scripts/logs.sh    # View logs"
