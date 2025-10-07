"""
MetaTrader 5 (MT5) integration module for DCA1 project.
Provides connection, data retrieval, and trading capabilities.
"""

from os import path
import MetaTrader5 as mt5
import pandas as pd
import numpy as np
from typing import Optional, Dict, List, Tuple
from datetime import datetime, timedelta
import logging


class MT5Connection:
    """
    MetaTrader 5 connection and operations handler.
    
    This class provides methods to connect to MT5, retrieve market data,
    and execute trades for the DCA strategy.
    """
    
    def __init__(
        self,
        login: Optional[int] = None,
        password: Optional[str] = None,
        server: Optional[str] = None,
        path: Optional[str] = None,
        ):
        """
        Initialize MT5 connection.
        
        Args:
            login (int, optional): MT5 account login
            password (str, optional): MT5 account password
            server (str, optional): MT5 server name
        """
        self.login = login
        self.password = password
        self.server = server
        self.path = path
        # self.path = "D:\\MT5\\MT5\\terminal64.exe"
        self.connected = False
        self.mt5 = mt5
        self.logger = logging.getLogger(__name__)
    
    def connect(self) -> bool:
        """
        Establish connection to MT5 terminal.
        
        Returns:
            bool: True if connection successful, False otherwise
        """
        try:
            # Initialize MT5 connection
            if path:
                if not self.mt5.initialize(path=self.path):
                    self.logger.error(f"initialize() failed, error code = {self.mt5.last_error()}")
                    return False
            else:
                if not self.mt5.initialize():
                    self.logger.error(f"initialize() failed, error code = {self.mt5.last_error()}")
                    return False
            # Login if credentials provided
            if self.login and self.password and self.server:
                if not self.mt5.login(self.login, password=self.password, server=self.server):
                    self.logger.error(f"login() failed, error code = {self.mt5.last_error()}")
                    return False
                self.logger.info(f"Connected to MT5 account: {self.login}")
            else:
                self.logger.info("Connected to MT5 (using current terminal connection)")
            
            self.connected = True
            
            # Display connection info
            terminal_info = self.mt5.terminal_info()
            account_info = self.mt5.account_info()
            
            if terminal_info:
                self.logger.info(f"MT5 Terminal: {terminal_info.name} {terminal_info.build}")
            
            if account_info:
                self.logger.info(f"Account: {account_info.login}, Balance: {account_info.balance}")
            
            return True
            
        except Exception as e:
            self.logger.error(f"MT5 connection failed: {str(e)}")
            return False
    
    def disconnect(self) -> None:
        """Disconnect from MT5 terminal."""
        if self.connected:
            self.mt5.shutdown()
            self.connected = False
            self.logger.info("Disconnected from MT5")
    
    def get_symbol_info(self, symbol: str) -> Optional[Dict]:
        """
        Get symbol information.
        
        Args:
            symbol (str): Trading symbol (e.g., 'EURUSD')
            
        Returns:
            Dict: Symbol information or None if error
        """
        if not self.connected:
            self.logger.error("Not connected to MT5")
            return None
        
        symbol_info = self.mt5.symbol_info(symbol)
        if symbol_info is None:
            self.logger.error(f"Symbol {symbol} not found")
            return None
        
        return {
            'name': symbol_info.name,
            'bid': symbol_info.bid,
            'ask': symbol_info.ask,
            'spread': symbol_info.spread,
            'digits': symbol_info.digits,
            'point': symbol_info.point,
            'min_lot': symbol_info.volume_min,
            'max_lot': symbol_info.volume_max,
            'lot_step': symbol_info.volume_step
        }
    
    def get_current_price(self, symbol: str) -> Optional[float]:
        """
        Get current price for a symbol.
        
        Args:
            symbol (str): Trading symbol
            
        Returns:
            float: Current bid price or None if error
        """
        symbol_info = self.get_symbol_info(symbol)
        return symbol_info['bid'] if symbol_info else None
    
    def get_historical_data(self, symbol: str, timeframe: int, 
                           start_date: datetime, end_date: datetime) -> Optional[pd.DataFrame]:
        """
        Get historical price data.
        
        Args:
            symbol (str): Trading symbol
            timeframe (int): MT5 timeframe (e.g., mt5.TIMEFRAME_H1)
            start_date (datetime): Start date
            end_date (datetime): End date
            
        Returns:
            pd.DataFrame: Historical data or None if error
        """
        if not self.connected:
            self.logger.error("Not connected to MT5")
            return None
        
        try:
            rates = self.mt5.copy_rates_range(symbol, timeframe, start_date, end_date)
            if rates is None or len(rates) == 0:
                self.logger.error(f"No historical data for {symbol}")
                return None
            
            # Convert to DataFrame
            df = pd.DataFrame(rates)
            df['time'] = pd.to_datetime(df['time'], unit='s')
            df.set_index('time', inplace=True)
            
            return df
            
        except Exception as e:
            self.logger.error(f"Error getting historical data: {str(e)}")
            return None
    
    def place_market_order(self, symbol: str, order_type: int, volume: float,
                          comment: str = "DCA Order") -> Optional[Dict]:
        """
        Place a market order.
        
        Args:
            symbol (str): Trading symbol
            order_type (int): mt5.ORDER_TYPE_BUY or mt5.ORDER_TYPE_SELL
            volume (float): Order volume in lots
            comment (str): Order comment
            
        Returns:
            Dict: Order result or None if error
        """
        if not self.connected:
            self.logger.error("Not connected to MT5")
            return None
        
        # Get symbol info for price
        symbol_info = self.mt5.symbol_info(symbol)
        if symbol_info is None:
            self.logger.error(f"Symbol {symbol} not found")
            return None
        
        # Determine price based on order type
        if order_type == self.mt5.ORDER_TYPE_BUY:
            price = symbol_info.ask
        else:
            price = symbol_info.bid
        
        # Prepare order request
        request = {
            "action": self.mt5.TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": volume,
            "type": order_type,
            "price": price,
            "deviation": 20,
            "magic": 234000,
            "comment": comment,
            "type_time": self.mt5.ORDER_TIME_GTC,
            "type_filling": self.mt5.ORDER_FILLING_IOC,
        }
        
        # Send order
        try:
            result = self.mt5.order_send(request)
            if result is None:
                self.logger.error(f"Order send failed, error: {self.mt5.last_error()}")
                return None
            
            if result.retcode != self.mt5.TRADE_RETCODE_DONE:
                self.logger.error(f"Order failed, retcode: {result.retcode}")
                return None
            
            self.logger.info(f"Order executed: {symbol} {volume} lots at {price}")
            
            return {
                'order_id': result.order,
                'deal_id': result.deal,
                'volume': result.volume,
                'price': result.price,
                'comment': result.comment
            }
            
        except Exception as e:
            self.logger.error(f"Error placing order: {str(e)}")
            return None
    
    def get_positions(self) -> List[Dict]:
        """
        Get all open positions.
        
        Returns:
            List[Dict]: List of position information
        """
        if not self.connected:
            self.logger.error("Not connected to MT5")
            return []
        
        positions = self.mt5.positions_get()
        if positions is None:
            return []
        
        result = []
        for pos in positions:
            result.append({
                'ticket': pos.ticket,
                'symbol': pos.symbol,
                'type': pos.type,
                'volume': pos.volume,
                'price_open': pos.price_open,
                'price_current': pos.price_current,
                'profit': pos.profit,
                'comment': pos.comment,
                'time': datetime.fromtimestamp(pos.time)
            })
        
        return result
    
    def get_account_info(self) -> Optional[Dict]:
        """
        Get account information.
        
        Returns:
            Dict: Account information or None if error
        """
        if not self.connected:
            self.logger.error("Not connected to MT5")
            return None
        
        account_info = mt5.account_info()
        if account_info is None:
            return None
        
        return {
            'login': account_info.login,
            'balance': account_info.balance,
            'equity': account_info.equity,
            'margin': account_info.margin,
            'margin_free': account_info.margin_free,
            'currency': account_info.currency,
            'leverage': account_info.leverage
        }