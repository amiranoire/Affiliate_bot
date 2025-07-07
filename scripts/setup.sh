#!/bin/bash
# scripts/setup.sh - Initial server setup script for your config style

echo "🚀 Setting up Telegram Employee Communication Bot on Digital Ocean..."

# Check if running as root
if [ "$EUID" -ne 0 ]; then
    echo "❌ Please run as root: sudo ./setup.sh"
    exit 1
fi

# Update system
echo "📦 Updating system packages..."
apt update && apt upgrade -y

# Install required system packages
echo "🔧 Installing system dependencies..."
apt install python3 python3-pip python3-venv git sqlite3 curl unzip -y

# Install Python dependencies for better performance
apt install python3-dev build-essential -y

# Create bot user for security
echo "👤 Creating bot user..."
if ! id "botuser" &>/dev/null; then
    useradd -m -s /bin/bash botuser
    echo "✅ Bot user created"
else
    echo "ℹ️  Bot user already exists"
fi

# Create bot directory structure
echo "📁 Setting up directories..."
mkdir -p /opt/telegram-bot/{logs,backups,scripts}
chown -R botuser:botuser /opt/telegram-bot

# Switch to bot user for setup
echo "🔄 Setting up bot as botuser..."
sudo -u botuser bash << 'EOF'
cd /opt/telegram-bot

# Clone repository (update with your actual GitHub username)
echo "📥 Cloning repository..."
if [ -d ".git" ]; then
    echo "ℹ️  Repository already cloned, pulling latest changes..."
    git pull origin main
else
    # Replace amiranoire with your actual GitHub username
    git clone https://github.com/amiranoire/Affiliate_bot.git .
fi

# Create virtual environment
echo "🐍 Setting up Python virtual environment..."
python3 -m venv venv
source venv/bin/activate

# Upgrade pip
pip install --upgrade pip

# Install dependencies
echo "📦 Installing Python dependencies..."
pip install -r requirements.txt

# Create .env from template if it doesn't exist
if [ ! -f ".env" ]; then
    echo "📝 Creating .env file from template..."
    cp .env.template .env
    echo "⚠️  Please edit /opt/telegram-bot/.env with your actual configuration!"
fi

# Set up log rotation
touch logs/bot.log
chmod 664 logs/bot.log

echo "✅ Bot setup completed successfully!"
EOF

# Install systemd service
echo "🔧 Installing systemd service..."
cp /opt/telegram-bot/scripts/telegram-bot.service /etc/systemd/system/
systemctl daemon-reload

# Enable service but don't start it yet
systemctl enable telegram-bot

echo ""
echo "🎉 Setup completed successfully!"
echo ""
echo "📝 Next steps:"
echo "1. Edit configuration: nano /opt/telegram-bot/.env"
echo "2. Add your bot token and admin chat ID"
echo "3. Test configuration: sudo -u botuser bash -c 'cd /opt/telegram-bot && source venv/bin/activate && python -c \"from config import config; print(config)\"'"
echo "4. Start the service: systemctl start telegram-bot"
echo "5. Check status: systemctl status telegram-bot"
echo "6. View logs: journalctl -u telegram-bot -f"
echo ""
echo "🔍 Configuration file location: /opt/telegram-bot/.env"