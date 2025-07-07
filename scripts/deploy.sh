#!/bin/bash
# scripts/deploy.sh - Deployment script for updates

echo "🔄 Deploying Telegram Bot updates..."

# Configuration
BOT_DIR="/opt/telegram-bot"
SERVICE_NAME="telegram-bot"

# Function to check if service exists
service_exists() {
    systemctl list-units --full -all | grep -Fq "$1"
}

# Check if we're in the right directory or need to change
if [ "$(pwd)" != "$BOT_DIR" ]; then
    cd "$BOT_DIR" || exit 1
fi

# Stop the service if it's running
if service_exists "$SERVICE_NAME"; then
    echo "🛑 Stopping bot service..."
    sudo systemctl stop "$SERVICE_NAME"
    sleep 2
fi

# Backup current database
if [ -f "employee_tracker.db" ]; then
    echo "💾 Backing up database..."
    mkdir -p backups
    cp employee_tracker.db "backups/employee_tracker_$(date +%Y%m%d_%H%M%S).db"
    echo "✅ Database backed up"
fi

# Pull latest changes
echo "📥 Pulling latest changes from Git..."
sudo -u botuser git fetch origin
sudo -u botuser git reset --hard origin/main

# Update virtual environment and dependencies
echo "📦 Updating dependencies..."
sudo -u botuser bash -c "source venv/bin/activate && pip install --upgrade pip && pip install -r requirements.txt"

# Update systemd service file if it changed
if [ -f "scripts/telegram-bot.service" ]; then
    echo "🔧 Updating systemd service..."
    cp scripts/telegram-bot.service /etc/systemd/system/
    systemctl daemon-reload
fi

# Set correct permissions
chown -R botuser:botuser "$BOT_DIR"

# Test configuration before starting
echo "🧪 Testing configuration..."
sudo -u botuser bash -c "cd $BOT_DIR && source venv/bin/activate && python -c 'from config import config; print(\"✅ Configuration is valid\")'" || {
    echo "❌ Configuration test failed!"
    exit 1
}

# Start the service
if service_exists "$SERVICE_NAME"; then
    echo "🚀 Starting bot service..."
    systemctl start "$SERVICE_NAME"
    sleep 3
    
    # Check if service started successfully
    if systemctl is-active --quiet "$SERVICE_NAME"; then
        echo "✅ Bot service started successfully!"
        systemctl status "$SERVICE_NAME" --no-pager -l
    else
        echo "❌ Bot service failed to start!"
        echo "📋 Recent logs:"
        journalctl -u "$SERVICE_NAME" --no-pager -n 20
        exit 1
    fi
else
    echo "⚠️  Service not configured. Run setup.sh first."
    exit 1
fi

echo ""
echo "🎉 Deployment completed successfully!"
echo ""
echo "📊 Useful commands:"
echo "  systemctl status telegram-bot    # Check service status"
echo "  journalctl -u telegram-bot -f    # Follow logs"
echo "  ./scripts/check-bot.sh           # Run health check"