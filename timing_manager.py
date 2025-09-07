#!/usr/bin/env python3
"""
Elon Bot Timing Manager
Manages all timing configurations and provides dynamic timing adjustments
"""

import json
import logging
import asyncio
from datetime import datetime, timedelta
from typing import Dict, Any, Optional
import os

# Configure logging
logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

class TimingManager:
    def __init__(self, config_path: str = "timing_config.json"):
        self.config_path = config_path
        self.config = self.load_config()
        self.runtime_adjustments = {}
        
    def load_config(self) -> Dict[str, Any]:
        """Load timing configuration from file."""
        try:
            with open(self.config_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except FileNotFoundError:
            log.error(f"Config file {self.config_path} not found!")
            return self.get_default_config()
        except json.JSONDecodeError as e:
            log.error(f"Invalid JSON in config file: {e}")
            return self.get_default_config()
    
    def get_default_config(self) -> Dict[str, Any]:
        """Get default timing configuration."""
        return {
            "timing_settings": {
                "pack_timeout_seconds": 5,
                "scheduler_interval_seconds": 30,
                "rate_limit_window_seconds": 60,
                "max_messages_per_window": 20,
                "flood_wait_retry_delay_seconds": 1,
                "cache_cleanup_interval_minutes": 1,
                "announcement_retry_delay_seconds": 10,
                "connection_timeout_seconds": 30,
                "message_send_timeout_seconds": 15
            },
            "monitoring_settings": {
                "enable_detailed_logging": True,
                "log_performance_metrics": True,
                "track_success_rates": True,
                "monitor_memory_usage": True,
                "alert_on_high_failure_rate": True,
                "failure_rate_threshold_percent": 50
            }
        }
    
    def get_timing(self, key: str, default: Any = None) -> Any:
        """Get timing value with runtime override support."""
        # Check runtime adjustments first
        if key in self.runtime_adjustments:
            return self.runtime_adjustments[key]
        
        # Check config file
        timing_settings = self.config.get('timing_settings', {})
        if key in timing_settings:
            return timing_settings[key]
        
        # Return default
        return default
    
    def set_runtime_timing(self, key: str, value: Any) -> None:
        """Set runtime timing adjustment."""
        self.runtime_adjustments[key] = value
        log.info(f"Runtime timing adjusted: {key} = {value}")
    
    def reset_runtime_timing(self, key: Optional[str] = None) -> None:
        """Reset runtime timing adjustments."""
        if key:
            self.runtime_adjustments.pop(key, None)
            log.info(f"Runtime timing reset: {key}")
        else:
            self.runtime_adjustments.clear()
            log.info("All runtime timings reset")
    
    def reload_config(self) -> bool:
        """Reload configuration from file."""
        try:
            self.config = self.load_config()
            log.info("Configuration reloaded successfully")
            return True
        except Exception as e:
            log.error(f"Failed to reload configuration: {e}")
            return False
    
    def save_config(self) -> bool:
        """Save current configuration to file."""
        try:
            with open(self.config_path, 'w', encoding='utf-8') as f:
                json.dump(self.config, f, indent=2, ensure_ascii=False)
            log.info("Configuration saved successfully")
            return True
        except Exception as e:
            log.error(f"Failed to save configuration: {e}")
            return False
    
    def update_timing(self, key: str, value: Any) -> bool:
        """Update timing in config file."""
        try:
            if 'timing_settings' not in self.config:
                self.config['timing_settings'] = {}
            
            self.config['timing_settings'][key] = value
            return self.save_config()
        except Exception as e:
            log.error(f"Failed to update timing {key}: {e}")
            return False
    
    def get_all_timings(self) -> Dict[str, Any]:
        """Get all timing settings with runtime overrides."""
        all_timings = {}
        
        # Base timings from config
        timing_settings = self.config.get('timing_settings', {})
        all_timings.update(timing_settings)
        
        # Apply runtime overrides
        all_timings.update(self.runtime_adjustments)
        
        return all_timings
    
    def optimize_timings_based_on_performance(self, performance_data: Dict[str, Any]) -> None:
        """Dynamically optimize timings based on performance data."""
        try:
            success_rate = performance_data.get('success_rate_percent', 100)
            memory_usage = performance_data.get('avg_memory_mb', 0)
            cpu_usage = performance_data.get('avg_cpu_percent', 0)
            
            # Adjust pack timeout based on success rate
            if success_rate < 80:
                current_timeout = self.get_timing('pack_timeout_seconds', 5)
                new_timeout = min(current_timeout + 2, 15)  # Max 15 seconds
                self.set_runtime_timing('pack_timeout_seconds', new_timeout)
                log.info(f"Adjusted pack timeout to {new_timeout}s due to low success rate")
            
            # Adjust scheduler interval based on CPU usage
            if cpu_usage > 70:
                current_interval = self.get_timing('scheduler_interval_seconds', 30)
                new_interval = min(current_interval + 10, 60)  # Max 60 seconds
                self.set_runtime_timing('scheduler_interval_seconds', new_interval)
                log.info(f"Adjusted scheduler interval to {new_interval}s due to high CPU usage")
            
            # Adjust rate limiting based on memory usage
            if memory_usage > 150:
                current_max = self.get_timing('max_messages_per_window', 20)
                new_max = max(current_max - 5, 10)  # Min 10 messages
                self.set_runtime_timing('max_messages_per_window', new_max)
                log.info(f"Adjusted max messages per window to {new_max} due to high memory usage")
                
        except Exception as e:
            log.error(f"Error optimizing timings: {e}")
    
    def get_timing_summary(self) -> str:
        """Get a summary of all timing settings."""
        timings = self.get_all_timings()
        
        summary = "⏱️ TIMING CONFIGURATION SUMMARY\n"
        summary += "=" * 40 + "\n"
        
        for key, value in timings.items():
            status = "🔧 RUNTIME" if key in self.runtime_adjustments else "📄 CONFIG"
            summary += f"{status} {key}: {value}\n"
        
        summary += f"\n📊 Total Settings: {len(timings)}\n"
        summary += f"🔧 Runtime Overrides: {len(self.runtime_adjustments)}\n"
        
        return summary

# Global timing manager instance
timing_manager = TimingManager()

def get_timing(key: str, default: Any = None) -> Any:
    """Global function to get timing value."""
    return timing_manager.get_timing(key, default)

def set_runtime_timing(key: str, value: Any) -> None:
    """Global function to set runtime timing."""
    timing_manager.set_runtime_timing(key, value)

def reload_timing_config() -> bool:
    """Global function to reload timing configuration."""
    return timing_manager.reload_config()

if __name__ == "__main__":
    # CLI interface for timing management
    import sys
    
    tm = TimingManager()
    
    if len(sys.argv) > 1:
        command = sys.argv[1]
        
        if command == "show":
            print(tm.get_timing_summary())
        elif command == "reload":
            if tm.reload_config():
                print("✅ Configuration reloaded successfully")
            else:
                print("❌ Failed to reload configuration")
        elif command == "reset":
            tm.reset_runtime_timing()
            print("✅ Runtime timings reset")
        elif command == "get" and len(sys.argv) > 2:
            key = sys.argv[2]
            value = tm.get_timing(key)
            print(f"{key}: {value}")
        elif command == "set" and len(sys.argv) > 3:
            key = sys.argv[2]
            value = sys.argv[3]
            try:
                # Try to convert to appropriate type
                if value.isdigit():
                    value = int(value)
                elif value.replace('.', '').isdigit():
                    value = float(value)
                elif value.lower() in ['true', 'false']:
                    value = value.lower() == 'true'
                
                tm.set_runtime_timing(key, value)
                print(f"✅ Set {key} = {value}")
            except Exception as e:
                print(f"❌ Error setting timing: {e}")
        else:
            print("Usage:")
            print("  python3 timing_manager.py show")
            print("  python3 timing_manager.py reload")
            print("  python3 timing_manager.py reset")
            print("  python3 timing_manager.py get <key>")
            print("  python3 timing_manager.py set <key> <value>")
    else:
        print(tm.get_timing_summary())
