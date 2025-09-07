#!/bin/bash

# Quick fix script for deployment issues
# Run this if you're having directory or permission issues

echo "🔧 Fixing deployment issues..."

# Stop services first
echo "⏹️  Stopping services..."
sudo systemctl stop elon-bot 2>/dev/null || true
sudo systemctl stop watchdog 2>/dev/null || true

# Create the correct directory
BOT_DIR="/home/ubuntu/elon"
echo "📁 Creating directory: $BOT_DIR"
sudo mkdir -p "$BOT_DIR"

# Copy current files to the correct location
echo "📋 Copying files to $BOT_DIR..."
sudo cp -r . "$BOT_DIR/"

# Set correct permissions
echo "🔐 Setting permissions..."
sudo chown -R ubuntu:ubuntu "$BOT_DIR"
sudo chmod -R 755 "$BOT_DIR"

# Make scripts executable
chmod +x "$BOT_DIR"/*.sh
chmod +x "$BOT_DIR"/*.py

# Reload systemd
echo "🔄 Reloading systemd..."
sudo systemctl daemon-reload

# Check if files exist
echo "✅ Checking deployment..."
if [ -f "$BOT_DIR/main.py" ]; then
    echo "✅ main.py found"
else
    echo "❌ main.py not found"
fi

if [ -f "$BOT_DIR/config.json" ]; then
    echo "✅ config.json found"
else
    echo "❌ config.json not found"
fi

echo ""
echo "🎯 Now you can start the services:"
echo "  sudo systemctl start elon-bot"
echo "  sudo systemctl start watchdog"
echo ""
echo "📊 Check status:"
echo "  sudo systemctl status elon-bot"
echo "  sudo systemctl status watchdog"
