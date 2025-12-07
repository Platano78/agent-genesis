"""Scheduler management for Agent Genesis indexing.

Provides configuration management and Windows Task Scheduler integration
for automated conversation indexing.
"""

import json
import logging
import subprocess
from pathlib import Path
from typing import Optional, Dict, Any
from datetime import datetime

logger = logging.getLogger(__name__)

# Paths
PROJECT_ROOT = Path(__file__).parent.parent
CONFIG_DIR = PROJECT_ROOT / "config"
CONFIG_FILE = CONFIG_DIR / "scheduler.json"
SCHEDULER_MANAGER_SCRIPT = PROJECT_ROOT / "scripts" / "scheduler_manager.ps1"

# Task name for Windows Task Scheduler
TASK_NAME = "GenesisAutoIndex"

# Default configuration
DEFAULT_CONFIG = {
    "enabled": False,
    "frequency_minutes": 30,
    "last_run": None,
    "task_name": TASK_NAME,
    "created_at": None,
    "updated_at": None
}


class SchedulerManager:
    """Manages scheduler configuration and Task Scheduler integration."""

    def __init__(self):
        """Initialize scheduler manager."""
        self._ensure_config_dir()
        self.config = self._load_config()

    def _ensure_config_dir(self):
        """Ensure config directory exists."""
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)

    def _load_config(self) -> Dict[str, Any]:
        """Load scheduler configuration from file."""
        if not CONFIG_FILE.exists():
            logger.info("Creating default scheduler configuration")
            self._save_config(DEFAULT_CONFIG.copy())
            return DEFAULT_CONFIG.copy()

        try:
            with open(CONFIG_FILE, 'r') as f:
                config = json.load(f)
                # Merge with defaults for any missing keys
                merged = DEFAULT_CONFIG.copy()
                merged.update(config)
                return merged
        except Exception as e:
            logger.error(f"Failed to load scheduler config: {e}")
            return DEFAULT_CONFIG.copy()

    def _save_config(self, config: Dict[str, Any]):
        """Save scheduler configuration to file."""
        try:
            config['updated_at'] = datetime.now().isoformat()
            if config.get('created_at') is None:
                config['created_at'] = config['updated_at']

            with open(CONFIG_FILE, 'w') as f:
                json.dump(config, f, indent=2)
            self.config = config
            logger.info(f"Scheduler config saved: {CONFIG_FILE}")
        except Exception as e:
            logger.error(f"Failed to save scheduler config: {e}")
            raise

    def _run_powershell_command(self, action: str, **kwargs) -> Dict[str, Any]:
        """Execute PowerShell scheduler manager script.

        Args:
            action: Action to perform (Status, Enable, Disable, Remove, Configure)
            **kwargs: Additional parameters for the action

        Returns:
            Dictionary with result status and output
        """
        if not SCHEDULER_MANAGER_SCRIPT.exists():
            return {
                "success": False,
                "error": "scheduler_manager.ps1 not found",
                "message": f"PowerShell script not found at {SCHEDULER_MANAGER_SCRIPT}"
            }

        # Build PowerShell command
        cmd = [
            "powershell.exe",
            "-ExecutionPolicy", "Bypass",
            "-File", str(SCHEDULER_MANAGER_SCRIPT),
            "-Action", action
        ]

        # Add optional parameters
        if 'frequency_minutes' in kwargs:
            cmd.extend(["-FrequencyMinutes", str(kwargs['frequency_minutes'])])

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=30,
                check=False
            )

            # Parse output (expecting JSON from PowerShell)
            try:
                output = json.loads(result.stdout) if result.stdout.strip() else {}
            except json.JSONDecodeError:
                output = {"raw_output": result.stdout, "raw_error": result.stderr}

            return {
                "success": result.returncode == 0,
                "return_code": result.returncode,
                "output": output,
                "error": result.stderr if result.returncode != 0 else None
            }

        except subprocess.TimeoutExpired:
            return {
                "success": False,
                "error": "timeout",
                "message": "PowerShell command timed out after 30 seconds"
            }
        except Exception as e:
            return {
                "success": False,
                "error": "execution_failed",
                "message": str(e)
            }

    def get_status(self) -> Dict[str, Any]:
        """Get current scheduler status.

        Returns:
            Dictionary with scheduler configuration and task status
        """
        # Get task status from Windows Task Scheduler
        ps_result = self._run_powershell_command("Status")

        return {
            "config": self.config,
            "task_scheduler": ps_result.get("output", {}),
            "config_file": str(CONFIG_FILE),
            "last_updated": self.config.get('updated_at'),
            "task_exists": ps_result.get("success", False)
        }

    def enable(self, frequency_minutes: Optional[int] = None) -> Dict[str, Any]:
        """Enable scheduler with specified frequency.

        Args:
            frequency_minutes: Indexing interval (default: 30, min: 5, max: 1440)

        Returns:
            Dictionary with operation result
        """
        # Validate frequency
        freq = frequency_minutes or self.config.get('frequency_minutes', 30)
        if freq < 5 or freq > 1440:
            return {
                "success": False,
                "error": "invalid_frequency",
                "message": "Frequency must be between 5 and 1440 minutes"
            }

        # Create/update Task Scheduler task
        ps_result = self._run_powershell_command("Enable", frequency_minutes=freq)

        if ps_result["success"]:
            # Update config
            self.config['enabled'] = True
            self.config['frequency_minutes'] = freq
            self._save_config(self.config)

            return {
                "success": True,
                "message": f"Scheduler enabled: indexing every {freq} minutes",
                "config": self.config,
                "task_info": ps_result.get("output", {})
            }
        else:
            return {
                "success": False,
                "error": "task_creation_failed",
                "message": ps_result.get("error", "Failed to create Task Scheduler task"),
                "details": ps_result
            }

    def disable(self) -> Dict[str, Any]:
        """Disable scheduler without removing the task.

        Returns:
            Dictionary with operation result
        """
        ps_result = self._run_powershell_command("Disable")

        if ps_result["success"]:
            # Update config
            self.config['enabled'] = False
            self._save_config(self.config)

            return {
                "success": True,
                "message": "Scheduler disabled (task preserved)",
                "config": self.config
            }
        else:
            return {
                "success": False,
                "error": "disable_failed",
                "message": ps_result.get("error", "Failed to disable task"),
                "details": ps_result
            }

    def remove(self) -> Dict[str, Any]:
        """Completely remove scheduled task.

        Returns:
            Dictionary with operation result
        """
        ps_result = self._run_powershell_command("Remove")

        if ps_result["success"]:
            # Update config
            self.config['enabled'] = False
            self._save_config(self.config)

            return {
                "success": True,
                "message": "Scheduled task removed completely",
                "config": self.config
            }
        else:
            return {
                "success": False,
                "error": "remove_failed",
                "message": ps_result.get("error", "Failed to remove task"),
                "details": ps_result
            }

    def configure(self, frequency_minutes: int) -> Dict[str, Any]:
        """Update frequency without disabling task.

        Args:
            frequency_minutes: New indexing interval (min: 5, max: 1440)

        Returns:
            Dictionary with operation result
        """
        # Validate frequency
        if frequency_minutes < 5 or frequency_minutes > 1440:
            return {
                "success": False,
                "error": "invalid_frequency",
                "message": "Frequency must be between 5 and 1440 minutes"
            }

        # Update Task Scheduler task
        ps_result = self._run_powershell_command(
            "Configure",
            frequency_minutes=frequency_minutes
        )

        if ps_result["success"]:
            # Update config
            self.config['frequency_minutes'] = frequency_minutes
            self._save_config(self.config)

            return {
                "success": True,
                "message": f"Scheduler frequency updated to {frequency_minutes} minutes",
                "config": self.config,
                "task_info": ps_result.get("output", {})
            }
        else:
            return {
                "success": False,
                "error": "configure_failed",
                "message": ps_result.get("error", "Failed to update task frequency"),
                "details": ps_result
            }

    def update_last_run(self, timestamp: Optional[str] = None):
        """Update last_run timestamp.

        Args:
            timestamp: ISO format timestamp (defaults to now)
        """
        self.config['last_run'] = timestamp or datetime.now().isoformat()
        self._save_config(self.config)
