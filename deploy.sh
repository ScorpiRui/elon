#!/bin/bash

# Elon Bot Universal Deployment Script
# Combines deployment and fix functionality in one script
# Usage: ./deploy.sh [options]

set -e  # Exit on any error

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Configuration
BOT_DIR="/root/elon"
SERVICE_USER="root"
SERVICE_GROUP="root"

# Function to print colored output
print_status() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

print_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

print_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# Function to check if running as root or with sudo
check_privileges() {
    if [ "$EUID" -eq 0 ]; then
        print_success "Running as root. Perfect for deployment."
    elif ! sudo -n true 2>/dev/null; then
        print_error "This script requires sudo privileges. Please run with sudo or ensure sudo access."
        exit 1
    fi
}

# Function to check prerequisites
check_prerequisites() {
    print_status "Checking prerequisites..."
    
    # Check if we're in the right directory
    if [ ! -f "main.py" ]; then
        print_error "main.py not found. Please run this script from the bot directory."
        exit 1
    fi
    
    # Check if config.json exists
    if [ ! -f "config.json" ]; then
        print_error "config.json not found. Please create it before deployment."
        exit 1
    fi
    
    # Check Python3
    if ! command -v python3 &> /dev/null; then
        print_error "Python3 is not installed. Please install Python3 first."
        exit 1
    fi
    
    # Check pip3
    if ! command -v pip3 &> /dev/null; then
        print_error "pip3 is not installed. Please install pip3 first."
        exit 1
    fi
    
    # Check systemd
    if ! command -v systemctl &> /dev/null; then
        print_error "systemd is not available. This script requires systemd."
        exit 1
    fi
    
    print_success "Prerequisites check passed"
}

# Function to stop services
stop_services() {
    print_status "Stopping existing services..."
    
    if systemctl is-active --quiet elon-bot 2>/dev/null; then
        sudo systemctl stop elon-bot
        print_success "elon-bot service stopped"
    else
        print_warning "elon-bot service was not running"
    fi
    
    if systemctl is-active --quiet watchdog 2>/dev/null; then
        sudo systemctl stop watchdog
        print_success "watchdog service stopped"
    else
        print_warning "watchdog service was not running"
    fi
}

# Function to create directory structure
create_directories() {
    print_status "Creating directory structure..."
    
    # Create bot directory
    if [ ! -d "$BOT_DIR" ]; then
        mkdir -p "$BOT_DIR"
        print_success "Created directory: $BOT_DIR"
    else
        print_warning "Directory already exists: $BOT_DIR"
    fi
    
    # Create logs directory
    mkdir -p /var/log/elon-bot
    print_success "Created logs directory: /var/log/elon-bot"
}

# Function to copy files
copy_files() {
  print_status "Syncing files to $BOT_DIR..."
  apt-get update && apt-get install -y rsync >/dev/null 2>&1 || true

  rsync -a --delete \
    --exclude '.git' \
    --exclude '.github' \
    --exclude '__pycache__' \
    --exclude '*.pyc' \
    --exclude '.venv' \
    --exclude 'venv' \
    --exclude 'logs' \
    ./ "$BOT_DIR/"

  [ -f "$BOT_DIR/main.py" ] || { print_error "main.py missing after sync"; exit 1; }
  [ -f "$BOT_DIR/config.json" ] || { print_error "config.json missing after sync"; exit 1; }
  print_success "Files synced"
}

# Function to set permissions
set_permissions() {
  print_status "Setting permissions..."
  chown -R $SERVICE_USER:$SERVICE_GROUP "$BOT_DIR"
  find "$BOT_DIR" -type d -exec chmod 750 {} \;
  find "$BOT_DIR" -type f -exec chmod 640 {} \;
  find "$BOT_DIR" -type f -name "*.sh" -o -name "*.py" -exec chmod 750 {} \; 2>/dev/null || true
  chmod 600 "$BOT_DIR/config.json"
  print_success "Permissions tightened"
}

# Function to install dependencies
install_dependencies() {
  print_status "Installing Python dependencies (venv)..."
  if ! command -v python3 &>/dev/null; then
      apt update && apt install -y python3
  fi
  if ! command -v pip3 &>/dev/null; then
      apt update && apt install -y python3-pip
  fi
  if ! python3 -c "import venv" 2>/dev/null; then
      apt update && apt install -y python3-venv
  fi

  # create venv inside the bot dir
  if [ ! -d "$BOT_DIR/.venv" ]; then
      python3 -m venv "$BOT_DIR/.venv"
  fi
  "$BOT_DIR/.venv/bin/pip" install --upgrade pip wheel

  if [ -f "$BOT_DIR/req.txt" ]; then
      "$BOT_DIR/.venv/bin/pip" install -r "$BOT_DIR/req.txt"
      print_success "Dependencies installed (venv)"
  else
      print_warning "req.txt missing — installing minimal deps"
      "$BOT_DIR/.venv/bin/pip" install aiogram telethon apscheduler
  fi
}

# Function to install systemd services
install_services() {
    print_status "Installing systemd services..."
    
    # Copy service files
    if [ -f "elon-bot.service" ]; then
        cp elon-bot.service /etc/systemd/system/
        print_success "elon-bot.service installed"
    else
        print_error "elon-bot.service not found"
        exit 1
    fi
    
    if [ -f "watchdog.service" ]; then
        cp watchdog.service /etc/systemd/system/
        print_success "watchdog.service installed"
    else
        print_warning "watchdog.service not found. Bot will work without watchdog."
    fi
    
    # Reload systemd
    systemctl daemon-reload
    print_success "systemd daemon reloaded"
    
    # Enable services
    systemctl enable elon-bot.service
    print_success "elon-bot service enabled"
    
    if [ -f "watchdog.service" ]; then
        systemctl enable watchdog.service
        print_success "watchdog service enabled"
    fi
}

# Function to validate deployment
validate_deployment() {
    print_status "Validating deployment..."
    
    local errors=0
    
    # Check main files
    if [ ! -f "$BOT_DIR/main.py" ]; then
        print_error "main.py not found in $BOT_DIR"
        ((errors++))
    fi
    
    if [ ! -f "$BOT_DIR/config.json" ]; then
        print_error "config.json not found in $BOT_DIR"
        ((errors++))
    fi
    
    if [ ! -f "$BOT_DIR/utils.py" ]; then
        print_error "utils.py not found in $BOT_DIR"
        ((errors++))
    fi
    
    # Check service files
    if [ ! -f "/etc/systemd/system/elon-bot.service" ]; then
        print_error "elon-bot.service not found in systemd"
        ((errors++))
    fi
    
    # Check Python syntax
    if ! python3 -m py_compile "$BOT_DIR/main.py" 2>/dev/null; then
        print_error "main.py has syntax errors"
        ((errors++))
    fi
    
    # Check config.json syntax
    if ! python3 -c "import json; json.load(open('$BOT_DIR/config.json'))" 2>/dev/null; then
        print_error "config.json has syntax errors"
        ((errors++))
    fi
    
    if [ $errors -eq 0 ]; then
        print_success "Deployment validation passed"
        return 0
    else
        print_error "Deployment validation failed with $errors errors"
        return 1
    fi
}

# Function to start services
start_services() {
    print_status "Starting services..."
    
    # Start elon-bot
    if systemctl start elon-bot; then
        print_success "elon-bot service started"
    else
        print_error "Failed to start elon-bot service"
        return 1
    fi
    
    # Wait a moment for service to initialize
    sleep 3
    
    # Check if service is running
    if systemctl is-active --quiet elon-bot; then
        print_success "elon-bot is running"
    else
        print_error "elon-bot failed to start properly"
        return 1
    fi
    
    # Start watchdog if available
    if [ -f "/etc/systemd/system/watchdog.service" ]; then
        if systemctl start watchdog; then
            print_success "watchdog service started"
        else
            print_warning "Failed to start watchdog service"
        fi
    fi
}

# Function to show status
show_status() {
    print_status "Service Status:"
    echo "=================="
    
    # Check elon-bot status
    if systemctl is-active --quiet elon-bot; then
        echo -e "elon-bot: ${GREEN}RUNNING${NC}"
    else
        echo -e "elon-bot: ${RED}STOPPED${NC}"
    fi
    
    # Check watchdog status
    if systemctl is-active --quiet watchdog 2>/dev/null; then
        echo -e "watchdog: ${GREEN}RUNNING${NC}"
    else
        echo -e "watchdog: ${YELLOW}NOT INSTALLED/STOPPED${NC}"
    fi
    
    echo ""
    print_status "Recent logs (last 5 lines):"
    echo "================================"
    journalctl -u elon-bot -n 5 --no-pager 2>/dev/null || echo "No logs available"
}

# Function to show help
show_help() {
    echo "Elon Bot Universal Deployment Script"
    echo "===================================="
    echo ""
    echo "Usage: $0 [OPTIONS]"
    echo ""
    echo "Options:"
    echo "  --fix-only      Only fix existing deployment issues"
    echo "  --no-start      Deploy but don't start services"
    echo "  --validate      Only validate current deployment"
    echo "  --status        Show service status"
    echo "  --help          Show this help message"
    echo ""
    echo "Examples:"
    echo "  $0                    # Full deployment"
    echo "  $0 --fix-only         # Fix existing issues"
    echo "  $0 --no-start         # Deploy without starting"
    echo "  $0 --validate         # Check current deployment"
    echo "  $0 --status           # Show status"
}

# Function to run health check
run_health_check() {
    print_status "Running health check..."
    
    if [ -f "$BOT_DIR/health_check.py" ]; then
        if python3 "$BOT_DIR/health_check.py" check > /dev/null 2>&1; then
            print_success "Health check passed"
        else
            print_warning "Health check failed - check logs for details"
        fi
    else
        print_warning "health_check.py not found - skipping health check"
    fi
}

# Main deployment function
deploy() {
    echo "🚀 Elon Bot Universal Deployment Script"
    echo "======================================="
    echo ""
    
    check_privileges
    check_prerequisites
    
    if [ "$1" != "--fix-only" ]; then
        stop_services
    fi
    
    create_directories
    copy_files
    set_permissions
    install_dependencies
    install_services
    
    if validate_deployment; then
        print_success "Deployment completed successfully!"
        
        if [ "$1" != "--no-start" ] && [ "$1" != "--validate" ]; then
            if start_services; then
                sleep 2
                run_health_check
                show_status
                
                echo ""
                print_success "🎉 Deployment completed successfully!"
                echo ""
                echo "📋 Available commands:"
                echo "  systemctl start elon-bot        # Start the bot"
                echo "  systemctl stop elon-bot         # Stop the bot"
                echo "  systemctl restart elon-bot      # Restart the bot"
                echo "  systemctl status elon-bot       # Check bot status"
                echo "  journalctl -u elon-bot -f       # View live logs"
                echo ""
                echo "🔍 Health monitoring:"
                echo "  python3 $BOT_DIR/health_check.py check        # Run health check"
                echo "  python3 $BOT_DIR/health_check.py monitor      # Continuous monitoring"
                echo "  python3 $BOT_DIR/bot_watchdog.py --once       # One-time health check"
            else
                print_error "Deployment completed but services failed to start"
                echo "Check logs: journalctl -u elon-bot -n 50"
                exit 1
            fi
        else
            echo ""
            print_success "Deployment completed! Services not started (--no-start flag)"
            echo "Start manually: systemctl start elon-bot"
        fi
    else
        print_error "Deployment validation failed"
        exit 1
    fi
}

# Parse command line arguments
case "${1:-}" in
    --help|-h)
        show_help
        exit 0
        ;;
    --status)
        show_status
        exit 0
        ;;
    --validate)
        check_privileges
        if validate_deployment; then
            print_success "Deployment validation passed"
            exit 0
        else
            print_error "Deployment validation failed"
            exit 1
        fi
        ;;
    --fix-only)
        print_status "Running in fix-only mode..."
        deploy --fix-only
        ;;
    --no-start)
        deploy --no-start
        ;;
    "")
        deploy
        ;;
    *)
        print_error "Unknown option: $1"
        show_help
        exit 1
        ;;
esac