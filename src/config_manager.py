"""
Configuration manager for MT5 DCA project.
"""

import json
import os
from typing import Dict, Any, Optional
import logging


class ConfigManager:
    """
    Configuration manager for MT5 DCA settings.
    """
    
    def __init__(self, config_file: str = "config/mt5_config.json"):
        """
        Initialize configuration manager.
        
        Args:
            config_file (str): Path to configuration file
        """
        self.config_file = config_file
        self.config: Dict[str, Any] = {}
        self.logger = logging.getLogger(__name__)
        self.load_config()
    
    def load_config(self) -> None:
        """Load configuration from file."""
        try:
            if os.path.exists(self.config_file):
                with open(self.config_file, 'r') as f:
                    self.config = json.load(f)
                self.logger.info(f"Configuration loaded from {self.config_file}")
            else:
                self.logger.warning(f"Configuration file {self.config_file} not found, using defaults")
                self._create_default_config()
        except Exception as e:
            self.logger.error(f"Error loading configuration: {str(e)}")
            self._create_default_config()
    
    def _create_default_config(self) -> None:
        """Create default configuration."""
        self.config = {
            "mt5": {
                "login": None,
                "password": None,
                "server": None,
                "timeout": 60000
            },
            "dca": {
                "investment_amount": 1000.0,
                "frequency": "weekly",
                "symbols": ["EURUSD", "GBPUSD", "USDJPY"],
                "auto_trading": False
            },
            "risk_management": {
                "max_lot_size": 1.0,
                "min_lot_size": 0.01,
                "max_daily_trades": 10
            },
            "logging": {
                "level": "INFO",
                "file": "logs/dca_mt5.log"
            }
        }
    
    def save_config(self) -> None:
        """Save configuration to file."""
        try:
            os.makedirs(os.path.dirname(self.config_file), exist_ok=True)
            with open(self.config_file, 'w') as f:
                json.dump(self.config, f, indent=4)
            self.logger.info(f"Configuration saved to {self.config_file}")
        except Exception as e:
            self.logger.error(f"Error saving configuration: {str(e)}")
    
    def get(self, key: str, default: Any = None) -> Any:
        """
        Get configuration value.
        
        Args:
            key (str): Configuration key (use dot notation for nested keys)
            default (Any): Default value if key not found
            
        Returns:
            Any: Configuration value
        """
        keys = key.split('.')
        value = self.config
        
        try:
            for k in keys:
                value = value[k]
            return value
        except KeyError:
            return default
    
    def set(self, key: str, value: Any) -> None:
        """
        Set configuration value.
        
        Args:
            key (str): Configuration key (use dot notation for nested keys)
            value (Any): Value to set
        """
        keys = key.split('.')
        config = self.config
        
        # Navigate to the parent of the target key
        for k in keys[:-1]:
            if k not in config:
                config[k] = {}
            config = config[k]
        
        # Set the final key
        config[keys[-1]] = value
    
    def get_mt5_credentials(self) -> Dict[str, Optional[str]]:
        """
        Get MT5 connection credentials.
        
        Returns:
            Dict: MT5 credentials
        """
        return {
            'login': self.get('mt5.login'),
            'password': self.get('mt5.password'),
            'server': self.get('mt5.server')
        }
    
    def get_dca_settings(self) -> Dict[str, Any]:
        """
        Get DCA strategy settings.
        
        Returns:
            Dict: DCA settings
        """
        return {
            'investment_amount': self.get('dca.investment_amount', 1000.0),
            'frequency': self.get('dca.frequency', 'weekly'),
            'symbols': self.get('dca.symbols', ['EURUSD']),
            'auto_trading': self.get('dca.auto_trading', False)
        }
    
    def get_risk_settings(self) -> Dict[str, float]:
        """
        Get risk management settings.
        
        Returns:
            Dict: Risk management settings
        """
        return {
            'max_lot_size': self.get('risk_management.max_lot_size', 1.0),
            'min_lot_size': self.get('risk_management.min_lot_size', 0.01),
            'max_daily_trades': self.get('risk_management.max_daily_trades', 10)
        }