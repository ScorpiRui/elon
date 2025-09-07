#!/usr/bin/env python3
"""
Elon Bot Performance Monitor
Monitors bot performance, timing, and provides real-time statistics
"""

import json
import time
import psutil
import asyncio
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional
import subprocess
import os

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('bot_monitor.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
log = logging.getLogger(__name__)

class BotMonitor:
    def __init__(self, config_path: str = "timing_config.json"):
        self.config_path = config_path
        self.config = self.load_config()
        self.metrics = {
            'start_time': datetime.now(),
            'total_announcements': 0,
            'successful_sends': 0,
            'failed_sends': 0,
            'banned_channels': set(),
            'performance_history': [],
            'memory_usage': [],
            'cpu_usage': []
        }
        
    def load_config(self) -> Dict:
        """Load timing configuration from file."""
        try:
            with open(self.config_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except FileNotFoundError:
            log.error(f"Config file {self.config_path} not found!")
            return {}
        except json.JSONDecodeError as e:
            log.error(f"Invalid JSON in config file: {e}")
            return {}
    
    def get_system_metrics(self) -> Dict:
        """Get current system performance metrics."""
        try:
            process = psutil.Process()
            return {
                'memory_mb': process.memory_info().rss / 1024 / 1024,
                'cpu_percent': process.cpu_percent(),
                'open_files': len(process.open_files()),
                'threads': process.num_threads(),
                'timestamp': datetime.now().isoformat()
            }
        except Exception as e:
            log.error(f"Error getting system metrics: {e}")
            return {}
    
    def get_bot_status(self) -> Dict:
        """Get current bot service status."""
        try:
            result = subprocess.run(
                ['systemctl', 'is-active', 'elon-bot'],
                capture_output=True,
                text=True
            )
            return {
                'status': result.stdout.strip(),
                'timestamp': datetime.now().isoformat()
            }
        except Exception as e:
            log.error(f"Error getting bot status: {e}")
            return {'status': 'unknown', 'timestamp': datetime.now().isoformat()}
    
    def analyze_performance(self) -> Dict:
        """Analyze current performance metrics."""
        if not self.metrics['performance_history']:
            return {'status': 'no_data'}
        
        recent_metrics = self.metrics['performance_history'][-10:]  # Last 10 entries
        
        avg_memory = sum(m['memory_mb'] for m in recent_metrics) / len(recent_metrics)
        avg_cpu = sum(m['cpu_percent'] for m in recent_metrics) / len(recent_metrics)
        
        total_attempts = self.metrics['successful_sends'] + self.metrics['failed_sends']
        success_rate = (self.metrics['successful_sends'] / total_attempts * 100) if total_attempts > 0 else 0
        
        return {
            'avg_memory_mb': round(avg_memory, 2),
            'avg_cpu_percent': round(avg_cpu, 2),
            'success_rate_percent': round(success_rate, 2),
            'total_announcements': self.metrics['total_announcements'],
            'banned_channels_count': len(self.metrics['banned_channels']),
            'uptime_hours': (datetime.now() - self.metrics['start_time']).total_seconds() / 3600
        }
    
    def check_alerts(self) -> List[str]:
        """Check for performance alerts."""
        alerts = []
        
        if not self.metrics['performance_history']:
            return alerts
        
        recent_metrics = self.metrics['performance_history'][-5:]
        
        # Memory usage alert
        avg_memory = sum(m['memory_mb'] for m in recent_metrics) / len(recent_metrics)
        if avg_memory > 200:  # More than 200MB
            alerts.append(f"High memory usage: {avg_memory:.1f}MB")
        
        # CPU usage alert
        avg_cpu = sum(m['cpu_percent'] for m in recent_metrics) / len(recent_metrics)
        if avg_cpu > 80:  # More than 80%
            alerts.append(f"High CPU usage: {avg_cpu:.1f}%")
        
        # Success rate alert
        total_attempts = self.metrics['successful_sends'] + self.metrics['failed_sends']
        if total_attempts > 0:
            success_rate = self.metrics['successful_sends'] / total_attempts * 100
            threshold = self.config.get('monitoring_settings', {}).get('failure_rate_threshold_percent', 50)
            if success_rate < (100 - threshold):
                alerts.append(f"Low success rate: {success_rate:.1f}%")
        
        return alerts
    
    def generate_report(self) -> str:
        """Generate a comprehensive performance report."""
        performance = self.analyze_performance()
        alerts = self.check_alerts()
        bot_status = self.get_bot_status()
        
        report = f"""
🤖 ELON BOT PERFORMANCE REPORT
{'='*50}
📊 Performance Metrics:
  • Success Rate: {performance.get('success_rate_percent', 0):.1f}%
  • Total Announcements: {performance.get('total_announcements', 0)}
  • Banned Channels: {performance.get('banned_channels_count', 0)}
  • Uptime: {performance.get('uptime_hours', 0):.1f} hours

💻 System Resources:
  • Memory Usage: {performance.get('avg_memory_mb', 0):.1f} MB
  • CPU Usage: {performance.get('avg_cpu_percent', 0):.1f}%
  • Bot Status: {bot_status.get('status', 'unknown')}

⚠️  Alerts: {len(alerts)}
"""
        
        if alerts:
            for alert in alerts:
                report += f"  • {alert}\n"
        else:
            report += "  • No alerts\n"
        
        report += f"\n🕐 Report Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        
        return report
    
    async def monitor_loop(self):
        """Main monitoring loop."""
        log.info("Starting bot monitoring...")
        
        while True:
            try:
                # Collect metrics
                system_metrics = self.get_system_metrics()
                if system_metrics:
                    self.metrics['performance_history'].append(system_metrics)
                    self.metrics['memory_usage'].append(system_metrics['memory_mb'])
                    self.metrics['cpu_usage'].append(system_metrics['cpu_percent'])
                
                # Keep only last 100 entries
                if len(self.metrics['performance_history']) > 100:
                    self.metrics['performance_history'] = self.metrics['performance_history'][-100:]
                
                # Check for alerts
                alerts = self.check_alerts()
                if alerts:
                    log.warning(f"Performance alerts: {alerts}")
                
                # Log performance every 5 minutes
                if len(self.metrics['performance_history']) % 10 == 0:
                    performance = self.analyze_performance()
                    log.info(f"Performance: Memory={performance.get('avg_memory_mb', 0):.1f}MB, "
                            f"CPU={performance.get('avg_cpu_percent', 0):.1f}%, "
                            f"Success={performance.get('success_rate_percent', 0):.1f}%")
                
                # Sleep for 30 seconds
                await asyncio.sleep(30)
                
            except Exception as e:
                log.error(f"Error in monitoring loop: {e}")
                await asyncio.sleep(30)

def main():
    """Main function for running the monitor."""
    monitor = BotMonitor()
    
    print("🤖 Elon Bot Monitor")
    print("=" * 50)
    print("Commands:")
    print("  python3 monitor_bot.py report  - Generate performance report")
    print("  python3 monitor_bot.py monitor - Start continuous monitoring")
    print("  python3 monitor_bot.py status  - Check bot status")
    print()
    
    import sys
    if len(sys.argv) > 1:
        command = sys.argv[1]
        
        if command == "report":
            print(monitor.generate_report())
        elif command == "status":
            status = monitor.get_bot_status()
            print(f"Bot Status: {status['status']}")
        elif command == "monitor":
            asyncio.run(monitor.monitor_loop())
        else:
            print(f"Unknown command: {command}")
    else:
        print(monitor.generate_report())

if __name__ == "__main__":
    main()
