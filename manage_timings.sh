#!/bin/bash

# Elon Bot Timing Management Script
# Provides easy commands to manage bot timing configurations

echo "⏱️  Elon Bot Timing Manager"
echo "=========================="

case "$1" in
    "show")
        echo "📊 Current Timing Configuration:"
        python3 timing_manager.py show
        ;;
    "reload")
        echo "🔄 Reloading timing configuration..."
        python3 timing_manager.py reload
        ;;
    "reset")
        echo "🔄 Resetting runtime timing adjustments..."
        python3 timing_manager.py reset
        ;;
    "get")
        if [ -z "$2" ]; then
            echo "❌ Usage: $0 get <timing_key>"
            echo "Available keys: pack_timeout_seconds, scheduler_interval_seconds, etc."
        else
            echo "📋 Getting timing value for: $2"
            python3 timing_manager.py get "$2"
        fi
        ;;
    "set")
        if [ -z "$2" ] || [ -z "$3" ]; then
            echo "❌ Usage: $0 set <timing_key> <value>"
            echo "Example: $0 set pack_timeout_seconds 10"
        else
            echo "⚙️  Setting timing: $2 = $3"
            python3 timing_manager.py set "$2" "$3"
        fi
        ;;
    "monitor")
        echo "📊 Starting performance monitoring..."
        python3 monitor_bot.py monitor
        ;;
    "report")
        echo "📋 Generating performance report..."
        python3 monitor_bot.py report
        ;;
    "status")
        echo "🔍 Checking bot status..."
        python3 monitor_bot.py status
        ;;
    "restart")
        echo "🔄 Restarting bot with new timings..."
        sudo systemctl restart elon-bot
        echo "✅ Bot restarted"
        ;;
    "optimize")
        echo "🎯 Optimizing timings based on performance..."
        # This would integrate with performance data
        echo "⚠️  Optimization feature coming soon"
        ;;
    *)
        echo "📋 Available Commands:"
        echo ""
        echo "Configuration:"
        echo "  $0 show                    - Show current timing configuration"
        echo "  $0 reload                  - Reload timing configuration from file"
        echo "  $0 reset                   - Reset runtime timing adjustments"
        echo "  $0 get <key>               - Get specific timing value"
        echo "  $0 set <key> <value>       - Set runtime timing value"
        echo ""
        echo "Monitoring:"
        echo "  $0 monitor                - Start continuous performance monitoring"
        echo "  $0 report                 - Generate performance report"
        echo "  $0 status                 - Check bot status"
        echo ""
        echo "Management:"
        echo "  $0 restart                - Restart bot with new timings"
        echo "  $0 optimize               - Optimize timings based on performance"
        echo ""
        echo "Examples:"
        echo "  $0 set pack_timeout_seconds 10"
        echo "  $0 set scheduler_interval_seconds 45"
        echo "  $0 get max_messages_per_window"
        echo "  $0 report"
        ;;
esac
