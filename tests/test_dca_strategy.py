"""
Test module for DCA strategy functionality.
"""

import unittest
from datetime import datetime
from src.dca_strategy import DCAStrategy


class TestDCAStrategy(unittest.TestCase):
    """Test cases for DCA Strategy class."""
    
    def setUp(self):
        """Set up test fixtures."""
        self.strategy = DCAStrategy(investment_amount=1000.0, frequency="weekly")
    
    def test_init(self):
        """Test DCA strategy initialization."""
        self.assertEqual(self.strategy.investment_amount, 1000.0)
        self.assertEqual(self.strategy.frequency, "weekly")
        self.assertEqual(len(self.strategy.trades), 0)
    
    def test_add_trade(self):
        """Test adding a trade."""
        self.strategy.add_trade("AAPL", 150.0, 6.67)
        
        self.assertEqual(len(self.strategy.trades), 1)
        trade = self.strategy.trades[0]
        self.assertEqual(trade['symbol'], "AAPL")
        self.assertEqual(trade['price'], 150.0)
        self.assertEqual(trade['quantity'], 6.67)
    
    def test_get_average_price(self):
        """Test average price calculation."""
        self.strategy.add_trade("AAPL", 150.0, 6.67)
        self.strategy.add_trade("AAPL", 145.0, 6.90)
        
        avg_price = self.strategy.get_average_price("AAPL")
        
        # Calculate expected average
        total_investment = (150.0 * 6.67) + (145.0 * 6.90)
        total_quantity = 6.67 + 6.90
        expected_avg = total_investment / total_quantity
        
        self.assertAlmostEqual(avg_price, expected_avg, places=2)
    
    def test_get_average_price_no_trades(self):
        """Test average price with no trades."""
        avg_price = self.strategy.get_average_price("AAPL")
        self.assertEqual(avg_price, 0.0)
    
    def test_get_portfolio_summary(self):
        """Test portfolio summary generation."""
        self.strategy.add_trade("AAPL", 150.0, 6.67)
        self.strategy.add_trade("MSFT", 300.0, 3.33)
        self.strategy.add_trade("AAPL", 145.0, 6.90)
        
        summary = self.strategy.get_portfolio_summary()
        
        self.assertIn("AAPL", summary)
        self.assertIn("MSFT", summary)
        self.assertEqual(summary["AAPL"]["trade_count"], 2)
        self.assertEqual(summary["MSFT"]["trade_count"], 1)


if __name__ == "__main__":
    unittest.main()