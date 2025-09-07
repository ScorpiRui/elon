#!/bin/bash

# Elon Bot Deployment Script
# Run this script to deploy the bot as a systemd service

echo "🚀 Deploying Elon Bot..."

# Check if we're in the right directory
if [ ! -f "main.py" ]; then
    echo "❌ Error: main.py not found. Please run this script from the bot directory."
    exit 1
fi

# Create directory if it doesn't exist
BOT_DIR="/home/ubuntu/elon"
if [ ! -d "$BOT_DIR" ]; then
    echo "📁 Creating bot directory: $BOT_DIR"
    sudo mkdir -p "$BOT_DIR"
fi

# Copy files to the correct directory
echo "📋 Copying files to $BOT_DIR..."
sudo cp -r . "$BOT_DIR/"

# Make scripts executable
chmod +x start_bot.sh
chmod +x deploy.sh
chmod +x "$BOT_DIR/start_bot.sh"
chmod +x "$BOT_DIR/deploy.sh"

# Install systemd services
echo "📦 Installing systemd services..."
sudo cp elon-bot.service /etc/systemd/system/
sudo cp watchdog.service /etc/systemd/system/
sudo systemctl daemon-reload

# Enable services to start on boot
echo "⚙️ Enabling services..."
sudo systemctl enable elon-bot.service
sudo systemctl enable watchdog.service

# Install Python dependencies
echo "📚 Installing Python dependencies..."
pip3 install -r req.txt

# Create logs directory
mkdir -p /var/log/elon-bot

# Set proper permissions
sudo chown -R ubuntu:ubuntu "$BOT_DIR"
sudo chmod -R 755 "$BOT_DIR"

echo "✅ Deployment completed!"
echo ""
echo "📋 Available commands:"
echo "  sudo systemctl start elon-bot        # Start the bot"
echo "  sudo systemctl stop elon-bot         # Stop the bot"
echo "  sudo systemctl restart elon-bot      # Restart the bot"
echo "  sudo systemctl status elon-bot       # Check bot status"
echo "  sudo journalctl -u elon-bot -f       # View live logs"
echo ""
echo "🔍 Health monitoring:"
echo "  python3 $BOT_DIR/health_check.py check        # Run health check"
echo "  python3 $BOT_DIR/health_check.py monitor      # Continuous monitoring"
echo "  python3 $BOT_DIR/bot_watchdog.py --once       # One-time health check"
echo ""
echo "🎯 To start the bot now, run: sudo systemctl start elon-bot"
echo "🔍 To start watchdog, run: sudo systemctl start watchdog"
