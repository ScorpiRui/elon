#!/bin/bash

# Elon Bot Startup Script
# This script ensures the bot starts properly with all dependencies

# Set working directory
cd /root/elon

# Activate virtual environment if it exists
if [ -d "venv" ]; then
    source venv/bin/activate
    echo "Virtual environment activated"
fi

# Install/update dependencies
echo "Installing dependencies..."
pip3 install -r req.txt --quiet

# Check if config file exists
if [ ! -f "config.json" ]; then
    echo "ERROR: config.json not found!"
    exit 1
fi

# Check if main.py exists
if [ ! -f "main.py" ]; then
    echo "ERROR: main.py not found!"
    exit 1
fi

# Start the bot
echo "Starting Elon Bot..."
python3 main.py
