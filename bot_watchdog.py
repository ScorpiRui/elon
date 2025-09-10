#!/usr/bin/env python3
"""
Elon Bot Watchdog Service
Continuously monitors bot health and automatically restarts if needed
"""

import time
import logging
import subprocess
import json
import os
from datetime import datetime, timedelta
import sys

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('bot_watchdog.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
log = logging.getLogger(__name__)

class BotWatchdog:
    def __init__(self, check_interval=60, max_restart_attempts=3):
        self.check_interval = check_interval
        self.max_restart_attempts = max_restart_attempts
        self.restart_count = 0
        self.last_restart_time = None
        self.health_check_script = "/root/elon/health_check.py"
        
    def run_health_check(self):
        """Run health check script."""
        try:
            result = subprocess.run(
                ['python3', self.health_check_script, 'check'],
                capture_output=True,
                text=True,
                timeout=30
            )
            
            if result.returncode == 0:
                log.info("Health check passed")
                return True
            else:
                log.warning(f"Health check failed: {result.stderr}")
                return False
        except Exception as e:
            log.error(f"Error running health check: {e}")
            return False
    
    def restart_service(self):
        """Restart the bot service."""
        try:
            log.info("Restarting bot service...")
            result = subprocess.run(
                ['systemctl', 'restart', 'elon-bot'],
                capture_output=True,
                text=True,
                timeout=60
            )
            
            if result.returncode == 0:
                log.info("Service restarted successfully")
                self.restart_count += 1
                self.last_restart_time = datetime.now()
                return True
            else:
                log.error(f"Failed to restart service: {result.stderr}")
                return False
        except Exception as e:
            log.error(f"Error restarting service: {e}")
            return False
    
    def should_restart(self):
        """Check if service should be restarted based on restart limits."""
        if self.last_restart_time is None:
            return True
        
        # Reset restart count if last restart was more than 1 hour ago
        if datetime.now() - self.last_restart_time > timedelta(hours=1):
            self.restart_count = 0
            return True
        
        # Don't restart if we've exceeded max attempts in the last hour
        return self.restart_count < self.max_restart_attempts
    
    def run_watchdog(self):
        """Main watchdog loop."""
        log.info("Starting bot watchdog service...")
        
        while True:
            try:
                log.info("Running health check...")
                
                if not self.run_health_check():
                    if self.should_restart():
                        log.warning("Health check failed. Restarting service...")
                        if self.restart_service():
                            log.info("Service restarted successfully")
                        else:
                            log.error("Failed to restart service")
                    else:
                        log.error(f"Health check failed but restart limit exceeded ({self.restart_count}/{self.max_restart_attempts})")
                else:
                    log.info("Bot is healthy")
                
                # Wait before next check
                log.info(f"Waiting {self.check_interval} seconds before next check...")
                time.sleep(self.check_interval)
                
            except KeyboardInterrupt:
                log.info("Watchdog stopped by user")
                break
            except Exception as e:
                log.error(f"Error in watchdog loop: {e}")
                time.sleep(self.check_interval)

def main():
    """Main function."""
    import argparse
    
    parser = argparse.ArgumentParser(description='Elon Bot Watchdog Service')
    parser.add_argument('--interval', type=int, default=60, help='Check interval in seconds (default: 60)')
    parser.add_argument('--max-restarts', type=int, default=3, help='Max restart attempts per hour (default: 3)')
    parser.add_argument('--once', action='store_true', help='Run health check once and exit')
    
    args = parser.parse_args()
    
    watchdog = BotWatchdog(
        check_interval=args.interval,
        max_restart_attempts=args.max_restarts
    )
    
    if args.once:
        # Run health check once
        if watchdog.run_health_check():
            print("✅ Bot is healthy")
            sys.exit(0)
        else:
            print("❌ Bot health check failed")
            sys.exit(1)
    else:
        # Run continuous monitoring
        watchdog.run_watchdog()

if __name__ == "__main__":
    main()
