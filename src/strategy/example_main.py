"""
Example Main File Using Grid DCA Strategy Module

This file demonstrates how to use the Grid DCA Strategy module.
All strategy logic is now in src/strategy/grid_dca_strategy.py
"""

import logging
import sys
import os

# Add src path
src_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'src'))
if src_path not in sys.path:
    sys.path.insert(0, src_path)

from mt5_connector import MT5Connection
from config_manager import ConfigManager
from Libs.telegramBot import TelegramBot
from strategy import GridDCAStrategy

################################################################################################
# CONFIGURATION - Only thing that differs between main files
CONFIG_FILE = "config/mt5_config_183585926.json"  # Change this per account
################################################################################################


def main():
    """
    Main entry point - just load config and run strategy.
    """
    # Setup logging
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s'
    )
    logger = logging.getLogger(__name__)
    
    try:
        # Load configuration
        config = ConfigManager(CONFIG_FILE)
        
        # Setup Telegram bot (optional)
        telegram_config = config.config.get('telegram', {})
        telegram_api_token = telegram_config.get('api_token')
        telegram_bot_name = telegram_config.get('bot_name')
        telegram_bot = TelegramBot(telegram_api_token, telegram_bot_name) if telegram_api_token else None
        
        # Setup MT5 connection
        credentials = config.get_mt5_credentials()
        mt5 = MT5Connection(
            login=credentials['login'],
            password=credentials['password'],
            server=credentials['server'],
            path=credentials['path'],
        )
        
        if not mt5.connect():
            logger.error("❌ Failed to connect to MT5")
            return
        
        # Create and run strategy
        strategy = GridDCAStrategy(
            config=config,
            mt5_connection=mt5,
            telegram_bot=telegram_bot,
            logger=logger
        )
        
        # Run the strategy (blocking call)
        strategy.run()
        
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        raise


if __name__ == "__main__":
    main()
