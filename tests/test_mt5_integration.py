"""
Test module for MT5 integration functionality.
"""

import unittest
from unittest.mock import Mock, patch, MagicMock
import sys
import os

# Add src directory to path for imports
sys.path.append(os.path.join(os.path.dirname(__file__), '..', 'src'))

from mt5_dca_strategy import MT5DCAStrategy
from config_manager import ConfigManager


class TestMT5Integration(unittest.TestCase):
    """Test cases for MT5 integration."""
    
    def setUp(self):
        """Set up test fixtures."""
        self.strategy = MT5DCAStrategy(investment_amount=1000.0, frequency="weekly")
    
    def test_init(self):
        """Test MT5 DCA strategy initialization."""
        self.assertEqual(self.strategy.investment_amount, 1000.0)
        self.assertEqual(self.strategy.frequency, "weekly")
        self.assertFalse(self.strategy.auto_trading_enabled)
        self.assertIsNotNone(self.strategy.mt5)
    
    @patch('mt5_dca_strategy.MT5Connection')
    def test_connect_mt5_success(self, mock_mt5_connection):
        """Test successful MT5 connection."""
        # Mock the connection
        mock_mt5_instance = Mock()
        mock_mt5_instance.connect.return_value = True
        mock_mt5_connection.return_value = mock_mt5_instance
        
        strategy = MT5DCAStrategy(1000.0)
        strategy.mt5 = mock_mt5_instance
        
        result = strategy.connect_mt5()
        
        self.assertTrue(result)
        mock_mt5_instance.connect.assert_called_once()
    
    @patch('mt5_dca_strategy.MT5Connection')
    def test_connect_mt5_failure(self, mock_mt5_connection):
        """Test failed MT5 connection."""
        # Mock the connection failure
        mock_mt5_instance = Mock()
        mock_mt5_instance.connect.return_value = False
        mock_mt5_connection.return_value = mock_mt5_instance
        
        strategy = MT5DCAStrategy(1000.0)
        strategy.mt5 = mock_mt5_instance
        
        result = strategy.connect_mt5()
        
        self.assertFalse(result)
        mock_mt5_instance.connect.assert_called_once()
    
    def test_enable_disable_auto_trading(self):
        """Test auto trading enable/disable."""
        # Mock connected state
        self.strategy.mt5.connected = True
        
        # Test enable
        self.strategy.enable_auto_trading()
        self.assertTrue(self.strategy.auto_trading_enabled)
        
        # Test disable
        self.strategy.disable_auto_trading()
        self.assertFalse(self.strategy.auto_trading_enabled)
    
    def test_should_execute_dca(self):
        """Test DCA execution condition."""
        # Test when auto trading is disabled
        self.strategy.auto_trading_enabled = False
        self.assertFalse(self.strategy.should_execute_dca("EURUSD"))
        
        # Test when auto trading is enabled
        self.strategy.auto_trading_enabled = True
        self.assertTrue(self.strategy.should_execute_dca("EURUSD"))


class TestConfigManager(unittest.TestCase):
    """Test cases for configuration manager."""
    
    def setUp(self):
        """Set up test fixtures."""
        self.config_manager = ConfigManager()
    
    def test_get_default_config(self):
        """Test getting default configuration values."""
        # Test MT5 settings
        mt5_creds = self.config_manager.get_mt5_credentials()
        self.assertIsNone(mt5_creds['login'])
        self.assertIsNone(mt5_creds['password'])
        self.assertIsNone(mt5_creds['server'])
        
        # Test DCA settings
        dca_settings = self.config_manager.get_dca_settings()
        self.assertEqual(dca_settings['investment_amount'], 1000.0)
        self.assertEqual(dca_settings['frequency'], 'weekly')
        self.assertIsInstance(dca_settings['symbols'], list)
    
    def test_get_set_config(self):
        """Test getting and setting configuration values."""
        # Test setting a value
        self.config_manager.set('test.value', 'test_data')
        
        # Test getting the value
        result = self.config_manager.get('test.value')
        self.assertEqual(result, 'test_data')
        
        # Test getting non-existent key with default
        result = self.config_manager.get('non.existent', 'default')
        self.assertEqual(result, 'default')
    
    def test_risk_settings(self):
        """Test risk management settings."""
        risk_settings = self.config_manager.get_risk_settings()
        
        self.assertIn('max_lot_size', risk_settings)
        self.assertIn('min_lot_size', risk_settings)
        self.assertIn('max_daily_trades', risk_settings)
        
        self.assertIsInstance(risk_settings['max_lot_size'], (int, float))
        self.assertIsInstance(risk_settings['min_lot_size'], (int, float))


if __name__ == "__main__":
    unittest.main()