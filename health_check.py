#!/usr/bin/env python3
"""
Elon Bot Health Check Script
Monitors bot health, task completion, and ensures proper operation
"""

import json
import time
import asyncio
import logging
import subprocess
import os
import sys
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
import psutil

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('health_check.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
log = logging.getLogger(__name__)

class BotHealthChecker:
    def __init__(self):
        self.config_file = "config.json"
        self.announcements_file = "announcements.json"
        self.drivers_file = "drivers.json"
        self.last_check_time = datetime.now()
        self.health_status = {
            'service_running': False,
            'bot_responsive': False,
            'tasks_completing': False,
            'last_message_sent': None,
            'announcements_active': 0,
            'errors_detected': [],
            'performance_issues': []
        }
        
    def check_service_status(self) -> bool:
        """Check if systemd service is running."""
        try:
            result = subprocess.run(
                ['systemctl', 'is-active', 'elon-bot'],
                capture_output=True,
                text=True,
                timeout=5
            )
            is_active = result.stdout.strip() == 'active'
            self.health_status['service_running'] = is_active
            
            if not is_active:
                self.health_status['errors_detected'].append("Service is not running")
                log.error("Service is not running")
            
            return is_active
        except Exception as e:
            log.error(f"Error checking service status: {e}")
            self.health_status['errors_detected'].append(f"Service check failed: {e}")
            return False
    
    def check_bot_process(self) -> bool:
        """Check if bot process is actually running."""
        try:
            # Look for python process running main.py
            for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
                try:
                    cmdline = proc.info['cmdline']
                    if cmdline and any('main.py' in arg for arg in cmdline):
                        # Check if process is responsive
                        if proc.is_running() and proc.status() != psutil.STATUS_ZOMBIE:
                            log.info(f"Bot process found: PID {proc.info['pid']}")
                            return True
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue
            
            log.error("Bot process not found")
            self.health_status['errors_detected'].append("Bot process not found")
            return False
        except Exception as e:
            log.error(f"Error checking bot process: {e}")
            self.health_status['errors_detected'].append(f"Process check failed: {e}")
            return False
    
    def check_announcements_file(self) -> Tuple[bool, int]:
        """Check announcements file and count active announcements."""
        try:
            if not os.path.exists(self.announcements_file):
                log.error("Announcements file not found")
                self.health_status['errors_detected'].append("Announcements file missing")
                return False, 0
            
            with open(self.announcements_file, 'r', encoding='utf-8') as f:
                announcements = json.load(f)
            
            active_count = sum(1 for ann in announcements if ann.get('is_running', False))
            self.health_status['announcements_active'] = active_count
            
            log.info(f"Found {active_count} active announcements")
            return True, active_count
        except Exception as e:
            log.error(f"Error checking announcements file: {e}")
            self.health_status['errors_detected'].append(f"Announcements check failed: {e}")
            return False, 0
    
    def check_recent_activity(self) -> bool:
        """Check if bot has been sending messages recently."""
        try:
            # Check log files for recent activity
            log_files = ['announcement_debug.log', 'health_check.log']
            recent_activity = False
            
            for log_file in log_files:
                if os.path.exists(log_file):
                    # Check if file was modified in last 5 minutes
                    mod_time = datetime.fromtimestamp(os.path.getmtime(log_file))
                    if datetime.now() - mod_time < timedelta(minutes=5):
                        recent_activity = True
                        break
            
            # Check systemd journal for recent activity
            try:
                result = subprocess.run(
                    ['journalctl', '-u', 'elon-bot', '--since', '5 minutes ago', '-q'],
                    capture_output=True,
                    text=True,
                    timeout=10
                )
                if result.stdout.strip():
                    recent_activity = True
            except Exception:
                pass
            
            self.health_status['bot_responsive'] = recent_activity
            
            if not recent_activity:
                self.health_status['errors_detected'].append("No recent activity detected")
                log.warning("No recent activity detected")
            
            return recent_activity
        except Exception as e:
            log.error(f"Error checking recent activity: {e}")
            self.health_status['errors_detected'].append(f"Activity check failed: {e}")
            return False
    
    def check_task_completion(self) -> bool:
        """Check if tasks are being completed properly."""
        try:
            # Check if there are stuck announcements
            with open(self.announcements_file, 'r', encoding='utf-8') as f:
                announcements = json.load(f)
            
            stuck_announcements = []
            for ann in announcements:
                if ann.get('is_running', False):
                    # Check if announcement has been running too long without progress
                    created_at = ann.get('created_at')
                    if created_at:
                        try:
                            created_time = datetime.fromisoformat(created_at.replace('Z', '+00:00'))
                            if datetime.now() - created_time > timedelta(hours=2):
                                # Check if there are any task packs
                                task_packs = ann.get('task_packs', [])
                                if task_packs:
                                    # Check if packs are making progress
                                    all_stuck = True
                                    for pack in task_packs:
                                        if not pack.get('completed', False):
                                            sent_peers = len(pack.get('sent_peer_ids', []))
                                            total_peers = len(pack.get('peers', []))
                                            if sent_peers < total_peers:
                                                all_stuck = False
                                                break
                                    
                                    if all_stuck:
                                        stuck_announcements.append(ann['id'])
                        except Exception:
                            pass
            
            if stuck_announcements:
                self.health_status['errors_detected'].append(f"Stuck announcements: {stuck_announcements}")
                log.warning(f"Found stuck announcements: {stuck_announcements}")
                return False
            
            self.health_status['tasks_completing'] = True
            return True
        except Exception as e:
            log.error(f"Error checking task completion: {e}")
            self.health_status['errors_detected'].append(f"Task completion check failed: {e}")
            return False
    
    def check_performance_issues(self) -> List[str]:
        """Check for performance issues."""
        issues = []
        
        try:
            # Check memory usage
            for proc in psutil.process_iter(['pid', 'name', 'memory_info']):
                try:
                    cmdline = proc.info.get('cmdline', [])
                    if cmdline and any('main.py' in arg for arg in cmdline):
                        memory_mb = proc.info['memory_info'].rss / 1024 / 1024
                        if memory_mb > 500:  # Increased threshold to 500MB
                            issues.append(f"High memory usage: {memory_mb:.1f}MB")
                        break
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue
            
            # Check CPU usage
            for proc in psutil.process_iter(['pid', 'name', 'cpu_percent']):
                try:
                    cmdline = proc.info.get('cmdline', [])
                    if cmdline and any('main.py' in arg for arg in cmdline):
                        cpu_percent = proc.cpu_percent()
                        if cpu_percent > 95:  # Increased threshold to 95%
                            issues.append(f"High CPU usage: {cpu_percent:.1f}%")
                        break
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue
            
            # Check for flood wait errors in logs
            try:
                if os.path.exists('announcement_debug.log'):
                    with open('announcement_debug.log', 'r', encoding='utf-8') as f:
                        recent_logs = f.readlines()[-100:]  # Last 100 lines
                        flood_waits = sum(1 for line in recent_logs if 'FLOOD_WAIT' in line or 'Flood wait' in line)
                        if flood_waits > 10:  # More than 10 flood waits in recent logs
                            issues.append(f"High flood wait frequency: {flood_waits} recent flood waits")
            except Exception:
                pass
            
            self.health_status['performance_issues'] = issues
            return issues
        except Exception as e:
            log.error(f"Error checking performance: {e}")
            return []
    
    def restart_service(self) -> bool:
        """Restart the bot service."""
        try:
            log.info("Restarting bot service...")
            result = subprocess.run(
                ['systemctl', 'restart', 'elon-bot'],
                capture_output=True,
                text=True,
                timeout=30
            )
            
            if result.returncode == 0:
                log.info("Service restarted successfully")
                return True
            else:
                log.error(f"Failed to restart service: {result.stderr}")
                return False
        except Exception as e:
            log.error(f"Error restarting service: {e}")
            return False
    
    def run_health_check(self) -> Dict:
        """Run complete health check."""
        log.info("Starting health check...")
        
        # Reset health status
        self.health_status = {
            'service_running': False,
            'bot_responsive': False,
            'tasks_completing': False,
            'last_message_sent': None,
            'announcements_active': 0,
            'errors_detected': [],
            'performance_issues': []
        }
        
        # Run all checks
        service_ok = self.check_service_status()
        process_ok = self.check_bot_process()
        announcements_ok, active_count = self.check_announcements_file()
        activity_ok = self.check_recent_activity()
        tasks_ok = self.check_task_completion()
        performance_issues = self.check_performance_issues()
        
        # Determine overall health
        overall_healthy = (
            service_ok and 
            process_ok and 
            announcements_ok and 
            (activity_ok or active_count == 0) and  # No activity is OK if no active announcements
            tasks_ok and 
            len(performance_issues) == 0
        )
        
        self.health_status['overall_healthy'] = overall_healthy
        self.health_status['check_timestamp'] = datetime.now().isoformat()
        
        log.info(f"Health check completed. Overall healthy: {overall_healthy}")
        
        return self.health_status
    
    def should_restart(self) -> bool:
        """Determine if service should be restarted."""
        return (
            not self.health_status['service_running'] or
            not self.health_status['bot_responsive'] or
            not self.health_status['tasks_completing'] or
            len(self.health_status['errors_detected']) > 0 or
            len(self.health_status['performance_issues']) > 0
        )

def main():
    """Main function for health check."""
    checker = BotHealthChecker()
    
    if len(sys.argv) > 1:
        command = sys.argv[1]
        
        if command == "check":
            health_status = checker.run_health_check()
            print(json.dumps(health_status, indent=2))
            
            if checker.should_restart():
                print("⚠️  Health issues detected. Service should be restarted.")
                return 1
            else:
                print("✅ Bot is healthy.")
                return 0
                
        elif command == "restart":
            if checker.restart_service():
                print("✅ Service restarted successfully.")
                return 0
            else:
                print("❌ Failed to restart service.")
                return 1
                
        elif command == "monitor":
            print("🔍 Starting continuous health monitoring...")
            while True:
                health_status = checker.run_health_check()
                
                if checker.should_restart():
                    print("⚠️  Health issues detected. Restarting service...")
                    if checker.restart_service():
                        print("✅ Service restarted successfully.")
                    else:
                        print("❌ Failed to restart service.")
                
                time.sleep(60)  # Check every minute
                
        else:
            print(f"Unknown command: {command}")
            return 1
    else:
        # Default: run health check
        health_status = checker.run_health_check()
        print(json.dumps(health_status, indent=2))
        
        if checker.should_restart():
            print("⚠️  Health issues detected.")
            return 1
        else:
            print("✅ Bot is healthy.")
            return 0

if __name__ == "__main__":
    sys.exit(main())
