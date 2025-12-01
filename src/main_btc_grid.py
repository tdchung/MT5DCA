"""
Main script for BTC Grid Strategy
Implements buy-only grid trading for BTC with telegram notifications.
"""

import sys
import os
import signal
import threading
import time
import logging
from datetime import datetime

# Add the src directory to the path so we can import our modules
sys.path.append(os.path.join(os.path.dirname(__file__), '..'))

from config_manager import ConfigManager
from mt5_connector import MT5Connection
from Libs.telegramBot import TelegramBot
from strategy.grid_btc_ftmo import GridBTCStrategy


def setup_logger(name, log_file, level=logging.INFO):
    """Setup logger with file and console output."""
    # Create logs directory if it doesn't exist
    os.makedirs(os.path.dirname(log_file), exist_ok=True)
    
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    
    # File handler with UTF-8 encoding
    file_handler = logging.FileHandler(log_file, encoding='utf-8')
    file_handler.setFormatter(formatter)
    
    # Console handler with UTF-8 encoding
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    console_handler.stream.reconfigure(encoding='utf-8', errors='replace')
    
    logger = logging.getLogger(name)
    logger.setLevel(level)
    
    # Clear existing handlers to avoid duplicates
    if logger.hasHandlers():
        logger.handlers.clear()
    
    logger.addHandler(file_handler)
    logger.addHandler(console_handler)
    
    return logger


class BTCGridMain:
    """Main application for BTC Grid Strategy."""
    
    def __init__(self):
        self.config_path = os.path.join(os.path.dirname(__file__), '..', 'config', 'mt5_config_btc.json')
        self.config = None
        self.mt5 = None
        self.telegram_bot = None
        self.strategy = None
        self.logger = None
        self.strategy_thread = None
        self.running = False
    
    def setup(self):
        """Initialize all components."""
        try:
            # Setup logging
            self.logger = setup_logger('btc_grid_main', 'logs/btc_grid_strategy.log')
            self.logger.info("🚀 Starting BTC Grid Strategy Application")
            
            # Load configuration
            self.config = ConfigManager(self.config_path)
            if not self.config.config:
                raise Exception("Failed to load configuration")
            
            # Initialize MT5 connection
            mt5_config = self.config.get_mt5_credentials()
            self.mt5 = MT5Connection(
                login=mt5_config.get('login'),
                password=mt5_config.get('password'),
                server=mt5_config.get('server'),
                path=mt5_config.get('path')
            )
            if not self.mt5.connect():
                raise Exception("Failed to initialize MT5 connection")
            
            self.logger.info("✅ MT5 connection established")
            
            # Initialize Telegram bot
            telegram_config = {
                'api_token': self.config.get('telegram.api_token'),
                'chat_id': self.config.get('telegram.chat_id')
            }
            if telegram_config.get('api_token'):
                self.telegram_bot = TelegramBot(
                    token=telegram_config['api_token'],
                    chat_ids=[telegram_config['chat_id']]  # Pass as list
                )
                
                # Store chat_id for direct messaging
                self.telegram_chat_id = telegram_config['chat_id']
                
                self.logger.info("✅ Telegram bot initialized")
            else:
                self.logger.warning("⚠️ Telegram configuration not found")
                self.telegram_chat_id = None
            
            # Initialize strategy
            self.strategy = GridBTCStrategy(
                config_file_path=self.config_path,
                mt5_connection=self.mt5,
                telegram_bot=self.telegram_bot,
                logger=self.logger
            )
            
            self.logger.info("✅ BTC Grid Strategy initialized")
            return True
            
        except Exception as e:
            if self.logger:
                self.logger.error(f"❌ Setup failed: {e}")
            else:
                print(f"❌ Setup failed: {e}")
            return False
    

    def run(self):
        """Run the main application."""
        if not self.setup():
            return False
        
        # Setup signal handlers for graceful shutdown
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)
        
        self.running = True
        
        try:
            # Send startup notification
            if self.telegram_bot:
                self.telegram_bot.send_message(
                    f"🟨 <b>BTC Grid Strategy Application Started</b>\n\n"
                    f"• Time: <code>{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</code>\n"
                    f"• Symbol: <code>BTCUSD</code>\n"
                    f"• Grid: <code>6 orders (3 above + 3 below)</code>\n"
                    f"• Auto-Start: <code>Enabled</code>\n\n"
                    f"🚀 <b>Strategy starting automatically...</b>",
                    chat_id=self.telegram_chat_id
                )
            
            # Auto-start the strategy
            auto_start = self.config.config.get('trading', {}).get('auto_start', True)
            if auto_start and self.strategy:
                self.logger.info("🚀 Auto-starting BTC Grid Strategy...")
                self.strategy.start()
            
            self.logger.info("🎯 Application ready. Strategy auto-started.")
            
            # Keep the application running
            while self.running:
                time.sleep(1)
                
                # Check connections periodically
                if not self.mt5.connected:
                    self.logger.warning("Connection lost. Attempting reconnection...")
                    if not self.mt5.connect():
                        self.logger.error("Failed to reconnect to MT5")
                        time.sleep(30)  # Wait before next attempt
        
        except KeyboardInterrupt:
            self.logger.info("📝 Keyboard interrupt received")
        except Exception as e:
            self.logger.error(f"❌ Unexpected error in main loop: {e}")
        finally:
            self._cleanup()
        
        return True
    
    def _signal_handler(self, signum, frame):
        """Handle shutdown signals."""
        self.logger.info(f"📝 Received signal {signum}. Shutting down gracefully...")
        self.running = False
    
    def _cleanup(self):
        """Cleanup resources."""
        try:
            self.logger.info("🧹 Starting cleanup...")
            
            # Stop strategy
            if self.strategy and self.strategy.is_running:
                self.strategy.stop()
            
            # Close MT5 connection
            if self.mt5:
                self.mt5.disconnect()
            
            self.logger.info("✅ Cleanup completed")
            
        except Exception as e:
            if self.logger:
                self.logger.error(f"❌ Error during cleanup: {e}")


def main():
    """Entry point for the application."""
    app = BTCGridMain()
    
    try:
        success = app.run()
        sys.exit(0 if success else 1)
    except Exception as e:
        print(f"❌ Fatal error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()