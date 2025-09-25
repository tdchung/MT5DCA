
"""
Direct Custom DCA Buy Strategy for XAUUSD
Refactored: Places grid orders only once, prevents duplicates, improves error handling, and adds docstrings.
"""

import logging
import sys
import os
import time

from mt5_connector import MT5Connection
from config_manager import ConfigManager

try:
    import MetaTrader5 as mt5_api
except ImportError:
    print("MetaTrader5 not available - using mock")
    mt5_api = None

import importlib.util
# Add src directory to path if not already present
src_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'src'))
if src_path not in sys.path:
    sys.path.insert(0, src_path)

def place_pending_order(mt5_api, symbol, order_type, price, tp_price, volume=0.01, comment="", logger=None):
    """Place a pending order if not already present at the price."""
    # Check for duplicate orders
    # time.sleep(5)
    existing_orders = mt5_api.orders_get(symbol=symbol)
    for o in existing_orders or []:
        if abs(o.price_open - price) < 1e-4 and o.type == order_type:
            if logger:
                logger.info(f"⏩ Skipping duplicate order at {price:.2f} for {symbol}")
            return None
    request = {
        "action": mt5_api.TRADE_ACTION_PENDING,
        "symbol": symbol,
        "volume": volume,
        "type": order_type,
        "price": price,
        "tp": tp_price,
        "deviation": 20,
        "magic": 234001,
        "comment": comment,
        "type_time": mt5_api.ORDER_TIME_GTC,
        "type_filling": mt5_api.ORDER_FILLING_RETURN,
    }
    max_retries = 3
    for attempt in range(1, max_retries + 1):
        result = mt5_api.order_send(request)
        if result is None:
            if logger:
                logger.error(f"Order send failed (attempt {attempt}), error: {mt5_api.last_error()}")
            time.sleep(1)
            continue
        if result.retcode == mt5_api.TRADE_RETCODE_DONE:
            order_type_str = "BUY STOP" if order_type == mt5_api.ORDER_TYPE_BUY_STOP else "SELL STOP"
            if logger:
                logger.info(f"✅ {order_type_str} order placed: {volume} lots at {price:.2f}, TP: {tp_price:.2f} (attempt {attempt})")
            return result
        else:
            if logger:
                logger.error(f"Order failed (attempt {attempt}), retcode: {result.retcode}, comment: {result.comment}")
            time.sleep(1)
    if logger:
        logger.error(f"❌ Order could not be placed after {max_retries} attempts for {symbol} at {price:.2f}")
    return None

def log_status(mt5, symbol, current_price, buy_stop_1_entry, buy_stop_1_tp, sell_stop_1_entry, sell_stop_1_tp, buy_stop_2_entry, buy_stop_2_tp, sell_stop_2_entry, sell_stop_2_tp, logger):
    """Log current positions, account info, PNL, and grid visualization."""
    logger.info(f"\n=== Step 6: Current Status Check ===")
    positions = mt5.get_positions()
    xauusd_positions = [p for p in positions if p['symbol'] == symbol]
    logger.info(f"Current {symbol} Positions: {len(xauusd_positions)}")
    if len(xauusd_positions) > 0:
        logger.info(f"🔥 Orders filled! {len(xauusd_positions)} active positions for {symbol}")
        # Track which grid orders are filled by comment/order ID
        grid_comments = ["DCA_BuyStop_1", "DCA_SellStop_1", "DCA_BuyStop_2", "DCA_SellStop_2"]
        filled_grid_orders = []
        for pos in xauusd_positions:
            if pos['comment'] in grid_comments:
                filled_grid_orders.append((pos['ticket'], pos['comment'], pos['type'], pos['volume'], pos['price_open'], pos['price_current'], pos['profit']))
        if filled_grid_orders:
            logger.info(f"Filled grid orders:")
            for order in filled_grid_orders:
                order_type = "BUY" if order[2] == 0 else "SELL"
                logger.info(f"  Order ID: {order[0]}, Comment: {order[1]}, Type: {order_type}, Volume: {order[3]}, Entry: {order[4]:.2f}, Current: {order[5]:.2f}, P&L: ${order[6]:.2f}")
    total_unrealized_pnl = 0
    realized_pnl = 0
    grid_order_map = {
        "DCA_BuyStop_1": buy_stop_1_tp,
        "DCA_BuyStop_2": buy_stop_2_tp,
        "DCA_SellStop_1": sell_stop_1_tp,
        "DCA_SellStop_2": sell_stop_2_tp,
    }
    if xauusd_positions:
        for i, pos in enumerate(xauusd_positions, 1):
            pos_type = "BUY" if pos['type'] == 0 else "SELL"
            pnl = pos['profit']
            total_unrealized_pnl += pnl
            # Check if order reached TP by order id/comment
            reached_tp = False
            tp_price = grid_order_map.get(pos.get('comment'))
            if tp_price is not None and abs(pos['price_current'] - tp_price) < 1e-2:
                reached_tp = True
            if reached_tp:
                realized_pnl += pnl
                logger.info(f"🎯 TP filled for Order ID {pos['ticket']} ({pos.get('comment','')}): {pos_type} {pos['volume']} lots at {pos['price_current']:.2f} | P&L: ${pnl:.2f}")
            logger.info(f"  Position {i}: {pos_type} {pos['volume']} lots")
            logger.info(f"    Entry: {pos['price_open']:.2f}")
            logger.info(f"    Current: {pos['price_current']:.2f}")
            logger.info(f"    P&L: ${pnl:.2f}{' (TP reached)' if reached_tp else ''}")
    account_info = mt5.get_account_info()
    if account_info:
        logger.info(f"\nAccount Summary:")
        logger.info(f"  Balance: ${account_info['balance']:.2f}")
        logger.info(f"  Equity: ${account_info['equity']:.2f}")
        logger.info(f"  Free Margin: ${account_info['margin_free']:.2f}")
    logger.info(f"\n=== PNL Summary ===")
    logger.info(f"Unrealized P&L: ${total_unrealized_pnl:.2f}")
    logger.info(f"Realized P&L (TP reached): ${realized_pnl:.2f}")
    logger.info(f"Total P&L: ${(total_unrealized_pnl + realized_pnl):.2f}")
    logger.info(f"\n=== Strategy Grid Visualization ===")
    # Show which grid orders are filled
    grid_levels = [
        ("Buy Stop 2", buy_stop_2_entry, buy_stop_2_tp, "DCA_BuyStop_2"),
        ("Buy Stop 1", buy_stop_1_entry, buy_stop_1_tp, "DCA_BuyStop_1"),
        ("Current", current_price, None, None),
        ("Sell Stop 1", sell_stop_1_entry, sell_stop_1_tp, "DCA_SellStop_1"),
        ("Sell Stop 2", sell_stop_2_entry, sell_stop_2_tp, "DCA_SellStop_2"),
    ]
    # Get filled grid comments from open positions
    filled_comments = set()
    for p in xauusd_positions:
        if p.get('comment'):
            filled_comments.add(p['comment'])
    for name, entry, tp, comment in grid_levels:
        filled = comment in filled_comments if comment else False
        mark = "✅" if filled else "  "
        if name == "Current":
            logger.info(f"⚫ {name}:     {entry:.2f}")
        else:
            logger.info(f"{mark} {name}:  {entry:.2f} → TP: {tp:.2f}")
    logger.info(f"\n")
    logger.info(f"\n")

def main():
    """Run Custom DCA Buy Strategy for XAUUSD."""
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s'
    )
    logger = logging.getLogger(__name__)
    logger.info("=== Custom DCA Buy Strategy for XAUUSD ===")
    try:
        # Load configuration
        config = ConfigManager()
        credentials = config.get_mt5_credentials()
        strategy_cfg = config.get_dca_settings()
        symbol = strategy_cfg.get('symbol', 'XAUUSD')
        trade_amount = strategy_cfg.get('trade_amount', 0.01)
        # Create MT5 connection
        mt5 = MT5Connection(
            login=credentials['login'],
            password=credentials['password'],
            server=credentials['server']
        )
        # Connect
        if not mt5.connect():
            logger.error("❌ Failed to connect to MT5")
            return
        logger.info(f"✅ Connected to Exness MT5 Account (Symbol: {symbol}, Trade Amount: {trade_amount})")
        # Step 1: Get XAUUSD current prices
        logger.info(f"\n=== Step 1: Getting {symbol} Prices ===")
        symbol_info = mt5.get_symbol_info(symbol)
        if not symbol_info:
            logger.error(f"❌ Cannot get {symbol} symbol info")
            mt5.disconnect()
            return
        bid = symbol_info['bid']
        ask = symbol_info['ask']
        current_price = (bid + ask) / 2
        spread = symbol_info['spread']
        logger.info(f"📊 {symbol} Current Prices:")
        logger.info(f"   Bid: {bid:.2f}")
        logger.info(f"   Ask: {ask:.2f}")
        logger.info(f"   Mid Price: {current_price:.2f}")
        logger.info(f"   Spread: {spread} points")
        # Calculate strategy levels
        logger.info(f"\n=== Steps 2-5: Strategy Order Calculation ===")
        buy_stop_1_entry = current_price + 0.3
        buy_stop_1_tp = current_price + 2.3
        sell_stop_1_entry = current_price - 0.3
        sell_stop_1_tp = current_price - 2.3
        buy_stop_2_entry = current_price + 2.3
        buy_stop_2_tp = current_price + 4.3
        sell_stop_2_entry = current_price - 2.3  
        sell_stop_2_tp = current_price - 4.3
        # Place grid orders only once
        place_pending_order(mt5_api, symbol, mt5_api.ORDER_TYPE_BUY_STOP, buy_stop_1_entry, buy_stop_1_tp, trade_amount, "DCA_BuyStop_1", logger)
        place_pending_order(mt5_api, symbol, mt5_api.ORDER_TYPE_SELL_STOP, sell_stop_1_entry, sell_stop_1_tp, trade_amount, "DCA_SellStop_1", logger)
        time.sleep(2)  # Short delay to avoid overwhelming MT5
        place_pending_order(mt5_api, symbol, mt5_api.ORDER_TYPE_BUY_STOP, buy_stop_2_entry, buy_stop_2_tp, trade_amount, "DCA_BuyStop_2", logger)
        place_pending_order(mt5_api, symbol, mt5_api.ORDER_TYPE_SELL_STOP, sell_stop_2_entry, sell_stop_2_tp, trade_amount, "DCA_SellStop_2", logger)

        logger.info(f"Base Price: {current_price:.2f}")
        logger.info(f"")
        logger.info(f"📈 Step 2 - Buy Stop 1:")
        logger.info(f"   Entry: {buy_stop_1_entry:.2f} (Price + 0.2)")
        logger.info(f"   TP:    {buy_stop_1_tp:.2f} (Price + 2.0)")
        logger.info(f"   Profit Potential: {buy_stop_1_tp - buy_stop_1_entry:.2f}")
        logger.info(f"")
        logger.info(f"📉 Step 3 - Sell Stop 1:")
        logger.info(f"   Entry: {sell_stop_1_entry:.2f} (Price - 0.2)")
        logger.info(f"   TP:    {sell_stop_1_tp:.2f} (Price - 2.0)")
        logger.info(f"   Profit Potential: {sell_stop_1_entry - sell_stop_1_tp:.2f}")
        logger.info(f"")
        logger.info(f"📈 Step 4 - Buy Stop 2:")
        logger.info(f"   Entry: {buy_stop_2_entry:.2f} (Price + 2.2)")
        logger.info(f"   TP:    {buy_stop_2_tp:.2f} (Price + 4.2)")
        logger.info(f"   Profit Potential: {buy_stop_2_tp - buy_stop_2_entry:.2f}")
        logger.info(f"")
        logger.info(f"📉 Step 5 - Sell Stop 2:")
        logger.info(f"   Entry: {sell_stop_2_entry:.2f} (Price - 2.2)")
        logger.info(f"   TP:    {sell_stop_2_tp:.2f} (Price - 4.2)")
        logger.info(f"   Profit Potential: {sell_stop_2_entry - sell_stop_2_tp:.2f}")
        # Interval check loop (status only)
        try:
            while True:
                # Update current price dynamically
                symbol_info = mt5.get_symbol_info(symbol)
                if symbol_info:
                    bid = symbol_info['bid']
                    ask = symbol_info['ask']
                    current_price = (bid + ask) / 2
                log_status(mt5, symbol, current_price, buy_stop_1_entry, buy_stop_1_tp, sell_stop_1_entry, sell_stop_1_tp, buy_stop_2_entry, buy_stop_2_tp, sell_stop_2_entry, sell_stop_2_tp, logger)
                time.sleep(10)
        except KeyboardInterrupt:
            logger.info("Interrupted by user. Disconnecting...")
        mt5.disconnect()
    except Exception as e:
        logger.error(f"Error: {e}")

if __name__ == "__main__":
    main()