"""
Grid DCA Strategy for Account 159684431
Refactored to use the strategy module.
"""

import logging
import sys
import os

from mt5_connector import MT5Connection
from config_manager import ConfigManager
from Libs.telegramBot import TelegramBot
from strategy.grid_dca_strategy import GridDCAStrategy

src_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'src'))
if src_path not in sys.path:
    sys.path.insert(0, src_path)


################################################################################################
# Configuration
################################################################################################
CONFIG_FILE = "config/mt5_config_159684431.json"


def main():
    """Main entry point for the Grid DCA strategy."""
    # Setup logging
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s'
    )
    logger = logging.getLogger(__name__)
    
    try:
        # Load configuration
        config = ConfigManager(CONFIG_FILE)
        credentials = config.get_mt5_credentials()
        
        # Load Telegram configuration
        telegram_config = config.config.get('telegram', {})
        TELEGRAM_API_TOKEN = telegram_config.get('api_token')
        TELEGRAM_BOT_NAME = telegram_config.get('bot_name')
        TELEGRAM_CHAT_ID = telegram_config.get('chat_id')
        
        # Load trading configuration
        trading_config = config.config.get('trading', {})
        
        # Initialize Telegram bot
        telegram_bot = TelegramBot(TELEGRAM_API_TOKEN, TELEGRAM_BOT_NAME) if TELEGRAM_API_TOKEN else None
        
        # Connect to MT5
        mt5 = MT5Connection(
            login=credentials['login'],
            password=credentials['password'],
            server=credentials['server'],
            path=credentials['path'],
        )
        
        if not mt5.connect():
            logger.error("❌ Failed to connect to MT5")
            return
        
        # Initialize strategy with configuration
        strategy = GridDCAStrategy(
            config=trading_config,
            mt5_connection=mt5,
            telegram_bot=telegram_bot,
            logger=logger
        )
        
        # Set Telegram chat ID
        strategy.telegram_chat_id = TELEGRAM_CHAT_ID
        
        # Run the strategy
        strategy.run()
        
    except KeyboardInterrupt:
        logger.info("Interrupted by user. Shutting down...")
    except Exception as e:
        logger.error(f"Error in main: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()
