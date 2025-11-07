"""
Grid DCA Strategy - Account 263120967
Refactored version using strategy module for maximum code reuse.
"""

import sys
import os
import logging
from datetime import datetime

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mt5_connector import MT5Connection
from config_manager import ConfigManager
from Libs.telegramBot import TelegramBot
from strategy.grid_dca_strategy import GridDCAStrategy


def setup_logging():
    """Setup logging configuration."""
    log_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'logs')
    os.makedirs(log_dir, exist_ok=True)
    
    log_file = os.path.join(log_dir, f'grid_dca_263120967_{datetime.now().strftime("%Y%m%d")}.log')
    
    # Create formatters
    file_formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    console_formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    
    # File handler with UTF-8 encoding
    file_handler = logging.FileHandler(log_file, encoding='utf-8')
    file_handler.setFormatter(file_formatter)
    
    # Console handler with UTF-8 encoding
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(console_formatter)
    # Force UTF-8 encoding for console
    if hasattr(console_handler.stream, 'reconfigure'):
        console_handler.stream.reconfigure(encoding='utf-8')
    
    # Configure root logger
    logging.basicConfig(
        level=logging.INFO,
        handlers=[file_handler, console_handler]
    )
    
    return logging.getLogger(__name__)


def main():
    """
    Main entry point for Grid DCA Strategy (Account 263120967).
    Uses strategy module for complete encapsulation.
    """
    logger = setup_logging()
    logger.info("=" * 60)
    logger.info("Starting Grid DCA Strategy - Account 263120967")
    logger.info("=" * 60)
    
    try:
        # Load configuration
        config_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            'config',
            'mt5_config_263120967.json'
        )
        
        if not os.path.exists(config_path):
            logger.error(f"Configuration file not found: {config_path}")
            logger.error("Please create mt5_config.json with your account settings")
            return
        
        config = ConfigManager(config_path)
        logger.info(f"Configuration loaded from: {config_path}")
        
        # Extract configuration
        mt5_config = config.config.get('mt5', {})
        telegram_config = config.config.get('telegram', {})
        trading_config = config.config.get('trading', {})
        
        # Initialize MT5 connection
        mt5 = MT5Connection(
            login=mt5_config.get('login'),
            password=mt5_config.get('password'),
            server=mt5_config.get('server'),
            path=mt5_config.get('path')
        )
        
        if not mt5.connect():
            logger.error("Failed to initialize MT5 connection")
            return
        
        logger.info(f"✅ Connected to MT5: Account {mt5_config.get('login')}, Server {mt5_config.get('server')}")
        
        # Initialize Telegram bot
        telegram_bot = None
        if telegram_config.get('api_token'):
            try:
                telegram_bot = TelegramBot(
                    token=telegram_config['api_token'],
                    name=telegram_config.get('bot_name', 'MT5 Bot'),
                    chat_ids=[telegram_config.get('chat_id')] if telegram_config.get('chat_id') else []
                )
                logger.info(f"✅ Telegram bot initialized: {telegram_config.get('bot_name')}")
            except Exception as e:
                logger.warning(f"Failed to initialize Telegram bot: {e}")
                logger.info("Continuing without Telegram notifications...")
        
        # Create strategy instance
        strategy = GridDCAStrategy(
            config=config,  # Pass the ConfigManager instance
            mt5_connection=mt5,
            telegram_bot=telegram_bot,
            logger=logger
        )
        
        logger.info("Strategy initialized successfully")
        logger.info(f"Symbol: {trading_config.get('trade_symbol')}")
        logger.info(f"Trade Amount: {trading_config.get('trade_amount')}")
        logger.info(f"Delta Enter Price: {trading_config.get('delta_enter_price')}")
        logger.info(f"Target Profit: {trading_config.get('target_profit')}")
        
        # Run strategy (contains complete main loop)
        strategy.run()
        
    except KeyboardInterrupt:
        logger.info("\n" + "=" * 60)
        logger.info("Strategy stopped by user (Ctrl+C)")
        logger.info("=" * 60)
    except Exception as e:
        logger.error(f"Fatal error in main: {e}", exc_info=True)
    finally:
        logger.info("Cleaning up...")
        try:
            if 'mt5' in locals():
                mt5.disconnect()
                logger.info("MT5 disconnected")
        except Exception as e:
            logger.error(f"Error during cleanup: {e}")


if __name__ == "__main__":
    main()
