"""
DCA Strategy Module
Implements dollar cost averaging trading strategies.
"""

from typing import List, Dict, Optional
from datetime import datetime
import logging


class DCAStrategy:
    """
    Dollar Cost Averaging strategy implementation.
    
    This class provides methods to implement DCA trading strategies
    for various financial instruments.
    """
    
    def __init__(self, investment_amount: float, frequency: str = "weekly"):
        """
        Initialize DCA strategy.
        
        Args:
            investment_amount (float): Amount to invest per period
            frequency (str): Investment frequency ('daily', 'weekly', 'monthly')
        """
        self.investment_amount = investment_amount
        self.frequency = frequency
        self.trades: List[Dict] = []
        self.logger = logging.getLogger(__name__)
    
    def add_trade(self, symbol: str, price: float, quantity: float, 
                  timestamp: Optional[datetime] = None) -> None:
        """
        Add a trade to the DCA strategy.
        
        Args:
            symbol (str): Trading symbol
            price (float): Purchase price
            quantity (float): Quantity purchased
            timestamp (datetime, optional): Trade timestamp
        """
        if timestamp is None:
            timestamp = datetime.now()
        
        trade = {
            'symbol': symbol,
            'price': price,
            'quantity': quantity,
            'timestamp': timestamp,
            'investment': price * quantity
        }
        
        self.trades.append(trade)
        self.logger.info(f"Added trade: {trade}")
    
    def get_average_price(self, symbol: str) -> float:
        """
        Calculate average purchase price for a symbol.
        
        Args:
            symbol (str): Trading symbol
            
        Returns:
            float: Average purchase price
        """
        symbol_trades = [trade for trade in self.trades if trade['symbol'] == symbol]
        
        if not symbol_trades:
            return 0.0
        
        total_investment = sum(trade['investment'] for trade in symbol_trades)
        total_quantity = sum(trade['quantity'] for trade in symbol_trades)
        
        return total_investment / total_quantity if total_quantity > 0 else 0.0
    
    def get_portfolio_summary(self) -> Dict:
        """
        Get portfolio summary with all positions.
        
        Returns:
            Dict: Portfolio summary
        """
        symbols = set(trade['symbol'] for trade in self.trades)
        summary = {}
        
        for symbol in symbols:
            symbol_trades = [trade for trade in self.trades if trade['symbol'] == symbol]
            total_quantity = sum(trade['quantity'] for trade in symbol_trades)
            total_investment = sum(trade['investment'] for trade in symbol_trades)
            avg_price = self.get_average_price(symbol)
            
            summary[symbol] = {
                'quantity': total_quantity,
                'total_investment': total_investment,
                'average_price': avg_price,
                'trade_count': len(symbol_trades)
            }
        
        return summary