"""
Custom DCA Buy Strategy for XAUUSD (Gold)
Implements a grid trading strategy with buy/sell stops at different levels.
"""

import sys
import os
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
import time

# Add src directory to path for imports
sys.path.append(os.path.join(os.path.dirname(__file__), '..', 'src'))

try:
    import MetaTrader5 as mt5
except ImportError:
    print("MetaTrader5 not available - using mock")
    mt5 = None

from mt5_connector import MT5Connection
from config_manager import ConfigManager


class CustomDcaBuy:
    """
    Custom DCA strategy for XAUUSD with grid trading approach.
    Places buy/sell stops at different price levels with take profit targets.
    """
    
    def __init__(self, symbol: str = "XAUUSD"):
        """
        Initialize the custom DCA strategy.
        
        Args:
            symbol (str): Trading symbol (default: XAUUSD)
        """
        self.symbol = symbol
        self.logger = logging.getLogger(__name__)
        self.mt5 = None
        self.orders = []  # Track placed orders
        self.filled_orders = []  # Track filled orders
        self.tp_hit_orders = []  # Track TP hit orders
        self.total_realized_pnl = 0.0
        self.magic_number = 234001  # Unique identifier for our orders
        
    def connect_mt5(self) -> bool:
        """Connect to MetaTrader 5."""
        config = ConfigManager()
        credentials = config.get_mt5_credentials()
        
        self.mt5 = MT5Connection(
            login=credentials['login'],
            password=credentials['password'],
            server=credentials['server']
        )
        
        return self.mt5.connect()
    
    def disconnect_mt5(self) -> None:
        """Disconnect from MetaTrader 5."""
        if self.mt5:
            self.mt5.disconnect()
    
    def get_current_prices(self) -> Dict[str, float]:
        """
        Step 1: Get current bid/ask prices and show info.
        
        Returns:
            Dict: Current bid/ask prices and spread info
        """
        if not self.mt5 or not self.mt5.connected:
            self.logger.error("MT5 not connected")
            return {}
        
        symbol_info = self.mt5.get_symbol_info(self.symbol)
        if not symbol_info:
            self.logger.error(f"Cannot get symbol info for {self.symbol}")
            return {}
        
        prices = {
            'bid': symbol_info['bid'],
            'ask': symbol_info['ask'],
            'spread': symbol_info['spread'],
            'current_price': (symbol_info['bid'] + symbol_info['ask']) / 2,
            'point': symbol_info['point'],
            'digits': symbol_info['digits']
        }
        
        # Show price info
        self.logger.info(f"=== {self.symbol} Current Prices ===")
        self.logger.info(f"Bid: {prices['bid']:.{prices['digits']}f}")
        self.logger.info(f"Ask: {prices['ask']:.{prices['digits']}f}")
        self.logger.info(f"Mid Price: {prices['current_price']:.{prices['digits']}f}")
        self.logger.info(f"Spread: {prices['spread']} points")
        self.logger.info(f"Point Value: {prices['point']}")
        
        return prices
    
    def place_pending_order(self, order_type: int, price: float, tp_price: float, 
                           volume: float = 0.01, comment: str = "") -> Optional[Dict]:
        """
        Place a pending order (buy stop or sell stop).
        
        Args:
            order_type (int): Order type (mt5.ORDER_TYPE_BUY_STOP or mt5.ORDER_TYPE_SELL_STOP)
            price (float): Order price
            tp_price (float): Take profit price
            volume (float): Order volume
            comment (str): Order comment
            
        Returns:
            Dict: Order result or None if failed
        """
        if not self.mt5 or not self.mt5.connected or not mt5:
            self.logger.error("MT5 not available")
            return None
        
        # Prepare order request
        request = {
            "action": mt5.TRADE_ACTION_PENDING,
            "symbol": self.symbol,
            "volume": volume,
            "type": order_type,
            "price": price,
            "tp": tp_price,
            "deviation": 20,
            "magic": self.magic_number,
            "comment": comment,
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_RETURN,
        }
        
        try:
            result = mt5.order_send(request)
            if result is None:
                self.logger.error(f"Order send failed, error: {mt5.last_error()}")
                return None

            if result.retcode != mt5.TRADE_RETCODE_DONE:
                self.logger.error(f"Order failed, retcode: {result.retcode}, comment: {result.comment}")
                return None

            order_info = {
                'order_id': result.order,
                'type': order_type,
                'price': price,
                'tp_price': tp_price,
                'volume': volume,
                'comment': comment,
                'timestamp': datetime.now()
            }

            self.orders.append(order_info)

            if order_type == mt5.ORDER_TYPE_BUY_STOP:
                order_type_str = "BUY STOP"
            elif order_type == mt5.ORDER_TYPE_SELL_STOP:
                order_type_str = "SELL STOP"
            elif order_type == mt5.ORDER_TYPE_BUY:
                order_type_str = "BUY"
            elif order_type == mt5.ORDER_TYPE_SELL:
                order_type_str = "SELL"
            else:
                order_type_str = f"Type {order_type}"
            self.logger.info(f"✅ {order_type_str} order placed: {volume} lots at {price:.2f}, TP: {tp_price:.2f}")

            return order_info

        except Exception as e:
            self.logger.error(f"Error placing order: {str(e)}")
            return None
    
    def execute_strategy(self) -> bool:
        """
        Execute the complete custom DCA strategy.
        
        Steps 2-5: Create all required orders based on current price.
        """
        if not mt5:
            self.logger.error("MetaTrader5 module not available")
            return False
        
        # Step 1: Get current prices
        prices = self.get_current_prices()
        if not prices:
            return False
        
        current_price = prices['current_price']
        digits = prices['digits']
        
        self.logger.info(f"\n=== Executing Custom DCA Strategy ===")
        self.logger.info(f"Base Price: {current_price:.{digits}f}")
        
        # Step 2: Create buy stop at price + 0.2, TP at price + 2
        buy_stop_1_price = current_price + 0.2
        buy_stop_1_tp = current_price + 2.0

        order1 = self.place_pending_order(
            order_type=mt5.ORDER_TYPE_BUY_STOP,
            price=buy_stop_1_price,
            tp_price=buy_stop_1_tp,
            volume=0.01,
            comment="DCA_BuyStop_1"
        )

        # Step 3: Create sell stop at price - 0.2, TP at price - 2
        sell_stop_1_price = current_price - 0.2
        sell_stop_1_tp = current_price - 2.0

        order2 = self.place_pending_order(
            order_type=mt5.ORDER_TYPE_SELL_STOP,
            price=sell_stop_1_price,
            tp_price=sell_stop_1_tp,
            volume=0.01,
            comment="DCA_SellStop_1"
        )

        # Step 4: Create buy stop at price + 2 + 0.2, TP at price + 2 + 0.2 + 2
        buy_stop_2_price = current_price + 2.0 + 0.2
        buy_stop_2_tp = current_price + 2.0 + 0.2 + 2.0

        order3 = self.place_pending_order(
            order_type=mt5.ORDER_TYPE_BUY_STOP,
            price=buy_stop_2_price,
            tp_price=buy_stop_2_tp,
            volume=0.01,
            comment="DCA_BuyStop_2"
        )

        # Step 5: Create sell stop at price - 2 - 0.2, TP at price - 2 - 0.2 - 2
        sell_stop_2_price = current_price - 2.0 - 0.2
        sell_stop_2_tp = current_price - 2.0 - 0.2 - 2.0

        order4 = self.place_pending_order(
            order_type=mt5.ORDER_TYPE_SELL_STOP,
            price=sell_stop_2_price,
            tp_price=sell_stop_2_tp,
            volume=0.01,
            comment="DCA_SellStop_2"
        )
        
        # Summary of placed orders
        self.logger.info(f"\n=== Strategy Orders Summary ===")
        self.logger.info(f"1. Buy Stop:  {buy_stop_1_price:.2f} → TP: {buy_stop_1_tp:.2f}")
        self.logger.info(f"2. Sell Stop: {sell_stop_1_price:.2f} → TP: {sell_stop_1_tp:.2f}")
        self.logger.info(f"3. Buy Stop:  {buy_stop_2_price:.2f} → TP: {buy_stop_2_tp:.2f}")
        self.logger.info(f"4. Sell Stop: {sell_stop_2_price:.2f} → TP: {sell_stop_2_tp:.2f}")
        self.logger.info(f"Total Orders Placed: {len([o for o in [order1, order2, order3, order4] if o])}")
        
        return True
    
    def check_order_status(self) -> Dict:
        """
        Step 6: Check and notify if orders are filled or TP hit.
        
        Returns:
            Dict: Status summary with PNL information
        """
        if not self.mt5 or not self.mt5.connected or not mt5:
            return {}
        
        # Get current positions
        positions = self.mt5.get_positions()
        
        # Get pending orders
        pending_orders = mt5.orders_get(symbol=self.symbol)
        if pending_orders is None:
            pending_orders = []
        
        # Get order history (for filled/closed orders)
        from datetime import timedelta
        end_time = datetime.now()
        start_time = end_time - timedelta(hours=24)  # Last 24 hours
        
        deals = mt5.history_deals_get(
            start_time,
            end_time,
            group=f"*{self.symbol}*"
        )
        if deals is None:
            deals = []
        
        # Analyze current status
        def get_attr(obj, key):
            # Try dict access, then attribute access
            if isinstance(obj, dict):
                return obj.get(key)
            return getattr(obj, key, None)

        total_unrealized_pnl = sum(get_attr(pos, 'profit') for pos in positions if get_attr(pos, 'magic') == self.magic_number)

        # Calculate realized PNL from closed positions
        realized_pnl_today = 0.0
        for deal in deals:
            if get_attr(deal, 'magic') == self.magic_number and get_attr(deal, 'type') == 1:  # OUT deals (closing)
                realized_pnl_today += get_attr(deal, 'profit')

        status = {
            'current_positions': len([p for p in positions if get_attr(p, 'magic') == self.magic_number]),
            'pending_orders': len([o for o in pending_orders if get_attr(o, 'magic') == self.magic_number]),
            'unrealized_pnl': total_unrealized_pnl,
            'realized_pnl_today': realized_pnl_today,
            'total_pnl': total_unrealized_pnl + realized_pnl_today,
            'positions': [p for p in positions if get_attr(p, 'magic') == self.magic_number],
            'pending': [o for o in pending_orders if get_attr(o, 'magic') == self.magic_number]
        }
        
        # Display status
        self.logger.info(f"\n=== Order Status Check ===")
        self.logger.info(f"Active Positions: {status['current_positions']}")
        self.logger.info(f"Pending Orders: {status['pending_orders']}")
        
        if status['positions']:
            self.logger.info(f"\n--- Active Positions ---")
            for i, pos in enumerate(status['positions'], 1):
                pos_type = "BUY" if pos['type'] == 0 else "SELL"
                self.logger.info(f"{i}. {pos_type} {pos['volume']} lots at {pos['price_open']:.2f}")
                self.logger.info(f"   Current: {pos['price_current']:.2f}, P&L: {pos['profit']:.2f}")
        
        if status['pending']:
            self.logger.info(f"\n--- Pending Orders ---")
            for i, order in enumerate(status['pending'], 1):
                order_type_names = {
                    mt5.ORDER_TYPE_BUY_STOP: "BUY STOP",
                    mt5.ORDER_TYPE_SELL_STOP: "SELL STOP"
                }
                order_type_str = order_type_names.get(order.type, f"Type {order.type}")
                self.logger.info(f"{i}. {order_type_str} {order.volume_current} lots at {order.price_open:.2f}")
                if hasattr(order, 'tp') and order.tp > 0:
                    self.logger.info(f"   TP: {order.tp:.2f}")
        
        self.logger.info(f"\n=== PNL Summary ===")
        self.logger.info(f"Unrealized P&L: ${status['unrealized_pnl']:.2f}")
        self.logger.info(f"Realized P&L (24h): ${status['realized_pnl_today']:.2f}")
        self.logger.info(f"Total P&L: ${status['total_pnl']:.2f}")
        
        return status
    
    def monitor_strategy(self, duration_minutes: int = 60) -> None:
        """
        Monitor the strategy for a specified duration.
        
        Args:
            duration_minutes (int): How long to monitor in minutes
        """
        self.logger.info(f"\n=== Starting Strategy Monitor ({duration_minutes} minutes) ===")
        
        start_time = datetime.now()
        end_time = start_time + timedelta(minutes=duration_minutes)
        
        while datetime.now() < end_time:
            status = self.check_order_status()
            
            if status.get('current_positions', 0) > 0:
                self.logger.info(f"🔥 Orders filled! {status['current_positions']} active positions")
            
            # Wait 30 seconds before next check
            time.sleep(30)
        
        self.logger.info("=== Monitoring completed ===")


def main():
    """Main function to run the Custom DCA Buy strategy."""
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    
    logger = logging.getLogger(__name__)
    
    # Create strategy instance
    strategy = CustomDcaBuy("XAUUSD")
    
    try:
        # Connect to MT5
        if not strategy.connect_mt5():
            logger.error("Failed to connect to MT5")
            return
        
        logger.info("=== Custom DCA Buy Strategy for XAUUSD ===")
        
        # Execute the strategy
        if strategy.execute_strategy():
            logger.info("✅ Strategy executed successfully")
            
            # Check initial status
            strategy.check_order_status()
            
            # Optional: Monitor for a short period (uncomment to enable)
            # strategy.monitor_strategy(duration_minutes=5)
        else:
            logger.error("❌ Strategy execution failed")
    
    except Exception as e:
        logger.error(f"Error running strategy: {str(e)}")
    
    finally:
        strategy.disconnect_mt5()


if __name__ == "__main__":
    main()