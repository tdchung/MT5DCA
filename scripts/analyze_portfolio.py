"""
Utility script for DCA1 project.
This script can be used for data processing, analysis, or other utility functions.
"""

import sys
import os

# Add src directory to path for imports
sys.path.append(os.path.join(os.path.dirname(__file__), '..', 'src'))

from dca_strategy import DCAStrategy


def analyze_portfolio():
    """Analyze a sample portfolio."""
    strategy = DCAStrategy(investment_amount=500.0, frequency="monthly")
    
    # Sample data
    trades = [
        ("AAPL", 150.0, 3.33),
        ("AAPL", 145.0, 3.45),
        ("MSFT", 300.0, 1.67),
        ("GOOGL", 2500.0, 0.20)
    ]
    
    for symbol, price, quantity in trades:
        strategy.add_trade(symbol, price, quantity)
    
    print("Portfolio Analysis:")
    print("=" * 50)
    
    summary = strategy.get_portfolio_summary()
    for symbol, data in summary.items():
        print(f"{symbol}:")
        print(f"  Quantity: {data['quantity']:.2f}")
        print(f"  Total Investment: ${data['total_investment']:.2f}")
        print(f"  Average Price: ${data['average_price']:.2f}")
        print(f"  Trade Count: {data['trade_count']}")
        print()


if __name__ == "__main__":
    analyze_portfolio()