"""
Enhanced DCA Strategy with MT5 integration.
Extends the basic DCA strategy to work with live MT5 data and trading.
"""

from typing import List, Dict, Optional
from datetime import datetime
import logging

from dca_strategy import DCAStrategy
from mt5_connector import MT5Connection


class MT5DCAStrategy(DCAStrategy):
    """
    DCA Strategy with MetaTrader 5 integration.
    
    This class extends the basic DCA strategy to work with live MT5 data,
    execute trades automatically, and manage positions.
    """
    
    def __init__(self, investment_amount: float, frequency: str = "weekly",
                 mt5_login: Optional[int] = None, mt5_password: Optional[str] = None,
                 mt5_server: Optional[str] = None):
        """
        Initialize MT5 DCA strategy.
        
        Args:
            investment_amount (float): Amount to invest per period
            frequency (str): Investment frequency ('daily', 'weekly', 'monthly')
            mt5_login (int, optional): MT5 account login
            mt5_password (str, optional): MT5 account password
            mt5_server (str, optional): MT5 server name
        """
        super().__init__(investment_amount, frequency)
        
        self.mt5 = MT5Connection(mt5_login, mt5_password, mt5_server)
        self.auto_trading_enabled = False
        self.logger = logging.getLogger(__name__)
    
    def connect_mt5(self) -> bool:
        """
        Connect to MetaTrader 5.
        
        Returns:
            bool: True if connection successful
        """
        success = self.mt5.connect()
        if success:
            self.logger.info("MT5 connection established")
            return True
        else:
            self.logger.error("Failed to connect to MT5")
            return False
    
    def disconnect_mt5(self) -> None:
        """Disconnect from MetaTrader 5."""
        self.mt5.disconnect()
    
    def get_live_price(self, symbol: str) -> Optional[float]:
        """
        Get current live price from MT5.
        
        Args:
            symbol (str): Trading symbol
            
        Returns:
            float: Current price or None if error
        """
        return self.mt5.get_current_price(symbol)
    
    def execute_dca_purchase(self, symbol: str, lot_size: float = None) -> Optional[Dict]:
        """
        Execute a DCA purchase through MT5.
        
        Args:
            symbol (str): Trading symbol
            lot_size (float, optional): Lot size to buy (calculated if not provided)
            
        Returns:
            Dict: Order execution result or None if error
        """
        if not self.mt5.connected:
            self.logger.error("MT5 not connected")
            return None
        
        # Get current price
        current_price = self.get_live_price(symbol)
        if current_price is None:
            self.logger.error(f"Unable to get price for {symbol}")
            return None
        
        # Calculate lot size if not provided
        if lot_size is None:
            symbol_info = self.mt5.get_symbol_info(symbol)
            if symbol_info is None:
                return None
            
            # Calculate lots based on investment amount and current price
            # This is a simplified calculation - adjust based on your broker's requirements
            contract_size = 100000  # Standard forex lot size
            lot_size = self.investment_amount / (current_price * contract_size)
            
            # Round to broker's lot step
            lot_step = symbol_info['lot_step']
            lot_size = round(lot_size / lot_step) * lot_step
            
            # Ensure minimum lot size
            if lot_size < symbol_info['min_lot']:
                lot_size = symbol_info['min_lot']
        
        # Execute the order
        import MetaTrader5 as mt5
        result = self.mt5.place_market_order(
            symbol=symbol,
            order_type=mt5.ORDER_TYPE_BUY,
            volume=lot_size,
            comment=f"DCA_{self.frequency}"
        )
        
        if result:
            # Add trade to our tracking
            quantity = result['volume']  # In MT5, this is lot size
            price = result['price']
            
            self.add_trade(symbol, price, quantity)
            
            self.logger.info(f"DCA purchase executed: {symbol} {quantity} lots at {price}")
            
        return result
    
    def get_mt5_positions(self) -> List[Dict]:
        """
        Get current MT5 positions.
        
        Returns:
            List[Dict]: List of MT5 positions
        """
        if not self.mt5.connected:
            return []
        
        return self.mt5.get_positions()
    
    def sync_with_mt5_positions(self) -> None:
        """
        Synchronize local trade tracking with MT5 positions.
        This helps keep the DCA strategy in sync with actual MT5 trades.
        """
        if not self.mt5.connected:
            self.logger.warning("Cannot sync - MT5 not connected")
            return
        
        positions = self.get_mt5_positions()
        
        # Clear existing trades (optional - depends on your strategy)
        # self.trades.clear()
        
        # Add MT5 positions as trades
        for pos in positions:
            # Only add buy positions for DCA tracking
            if pos['type'] == 0:  # 0 = Buy position in MT5
                self.add_trade(
                    symbol=pos['symbol'],
                    price=pos['price_open'],
                    quantity=pos['volume'],
                    timestamp=pos['time']
                )
        
        self.logger.info(f"Synced {len(positions)} positions from MT5")
    
    def get_account_summary(self) -> Optional[Dict]:
        """
        Get MT5 account summary.
        
        Returns:
            Dict: Account information or None if error
        """
        if not self.mt5.connected:
            return None
        
        return self.mt5.get_account_info()
    
    def enable_auto_trading(self) -> None:
        """Enable automatic DCA trading."""
        if self.mt5.connected:
            self.auto_trading_enabled = True
            self.logger.info("Auto trading enabled")
        else:
            self.logger.error("Cannot enable auto trading - MT5 not connected")
    
    def disable_auto_trading(self) -> None:
        """Disable automatic DCA trading."""
        self.auto_trading_enabled = False
        self.logger.info("Auto trading disabled")
    
    def should_execute_dca(self, symbol: str) -> bool:
        """
        Determine if a DCA purchase should be executed.
        Override this method to implement your DCA timing logic.
        
        Args:
            symbol (str): Trading symbol
            
        Returns:
            bool: True if DCA should be executed
        """
        # Basic implementation - always return True
        # You can add your timing logic here (e.g., check if enough time has passed)
        return self.auto_trading_enabled
    
    def run_dca_cycle(self, symbols: List[str]) -> Dict[str, Optional[Dict]]:
        """
        Run a complete DCA cycle for multiple symbols.
        
        Args:
            symbols (List[str]): List of symbols to process
            
        Returns:
            Dict: Results for each symbol
        """
        results = {}
        
        for symbol in symbols:
            if self.should_execute_dca(symbol):
                result = self.execute_dca_purchase(symbol)
                results[symbol] = result
            else:
                results[symbol] = None
                self.logger.info(f"Skipping DCA for {symbol} - conditions not met")
        
        return results