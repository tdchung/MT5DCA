"""
New Grid DCA Strategy for XAUUSD
Steps:
1. Get current price
2. Create stopbuy at price + 0.3, stopsell at price - 0.3, volume 1x
3. Create stopbuy at price + 2 + 0.3, stopsell at price - 2 - 0.3, volume 1x
"""

import logging
import sys
import os
import time
from datetime import datetime, timedelta

from mt5_connector import MT5Connection
from config_manager import ConfigManager

try:
    import MetaTrader5 as mt5_api
except ImportError:
    print("MetaTrader5 not available - using mock")
    mt5_api = None

src_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'src'))
if src_path not in sys.path:
    sys.path.insert(0, src_path)



################################################################################################
def check_pending_order_filled(history, order_id, logger=None):
    res = False
    for record in history:
        if record.position_id == order_id:
            if logger: logger.info(f"Found matching record: {record}")
        if record.position_id == order_id and record.order == order_id:
            res = True
            break
    return res

def check_position_closed(mt5_api, order_id, logger=None):
    status = False
    try:
        res = mt5_api.positions_get(ticket=order_id)
        # print(res)
        if res is None or (hasattr(res, '__len__') and len(res) == 0):
            status = True
    except Exception as e:
        if logger: logger.error(f"ERORR :: check_position_closed :: {e}")
    return status


def pos_closed_pnl(mt5_api, position_id, logger=None):
    pnl = 0
    try:
        if logger: logger.info(f"DEBUG :: pos_closed_pnl {position_id}")
        res = mt5_api.history_deals_get(position=position_id)
        if logger: logger.info(f"DEBUG :: pos_closed_pnl {res}")
        # for info in res:
        info = res[-1]
        if logger: logger.info(f"DEBUG :: pos_closed_pnl :: detail {info}")
        pnl += info.profit
    except Exception as e:
        if logger: logger.error(f"ERORR :: pos_closed_pnl :: {e}")
    return pnl
    
    
###############################################################################################################
def place_pending_order(mt5_api, symbol, order_type, price, tp_price, volume=0.01, comment="", logger=None):
    existing_orders = mt5_api.orders_get(symbol=symbol)
    for o in existing_orders or []:
        if abs(o.price_open - price) < 1e-4 and o.type == order_type:
            if logger:
                logger.info(f"⏩ Skipping duplicate or der at {price:.2f} for {symbol}")
            return None
    request = {
        "action": mt5_api.TRADE_ACTION_PENDING,
        "symbol": symbol,
        "volume": volume,
        "type": order_type,
        "price": price,
        "tp": tp_price,
        "deviation": 20,
        "magic": 234002,
        "comment": comment,
        "type_time": mt5_api.ORDER_TIME_GTC,
        "type_filling": mt5_api.ORDER_FILLING_RETURN,
    }
    result = mt5_api.order_send(request)
    if result is None:
        if logger:
            logger.error(f"Order send failed, error: {mt5_api.last_error()}")
        return None
    if result.retcode != mt5_api.TRADE_RETCODE_DONE:
        if logger:
            logger.error(f"Order failed, retcode: {result.retcode}, comment: {result.comment}")
        return None
    order_type_str = "BUY STOP" if order_type == mt5_api.ORDER_TYPE_BUY_STOP else "SELL STOP"
    if logger:
        logger.info(f"✅ {order_type_str} order placed: {volume} lots at {price:.2f}, TP: {tp_price:.2f}")
    return result

###############################################################################################################
def main():
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s'
    )
    logger = logging.getLogger(__name__)
    logger.info("=== New Grid DCA Strategy for XAUUSD ===")
    script_start_time = datetime.now()
    try:
        config = ConfigManager()
        credentials = config.get_mt5_credentials()
        symbol = "XAUUSDc"
        trade_amount = 0.01
        mt5 = MT5Connection(
            login=credentials['login'],
            password=credentials['password'],
            server=credentials['server']
        )
        if not mt5.connect():
            logger.error("❌ Failed to connect to MT5")
            return
        logger.info(f"✅ Connected to Exness MT5 Account (Symbol: {symbol}, Trade Amount: {trade_amount})")
        # Step 1: Get current price
        symbol_info = mt5.get_symbol_info(symbol)
        if not symbol_info:
            logger.error(f"❌ Cannot get {symbol} symbol info")
            mt5.disconnect()
            return
        bid = symbol_info['bid']
        ask = symbol_info['ask']
        current_price = (bid + ask) / 2
        logger.info(f"Current Price: {current_price:.2f}")

        order_results = []

        # Step 2
        buy_stop_0_entry = current_price + 0.3
        sell_stop_0_entry = current_price - 0.3
        tp_buy_0 = buy_stop_0_entry + 2.0
        tp_sell_0 = sell_stop_0_entry - 2.0
        res0_buy = place_pending_order(mt5_api, symbol, mt5_api.ORDER_TYPE_BUY_STOP, buy_stop_0_entry, tp_buy_0, 1.0*trade_amount, "new_grid_buy_0", logger)
        res0_sell = place_pending_order(mt5_api, symbol, mt5_api.ORDER_TYPE_SELL_STOP, sell_stop_0_entry, tp_sell_0, 1.0*trade_amount, "new_grid_sell_0", logger)
        order_results.extend([res0_buy, res0_sell])
        time.sleep(0.1)

        # Step 3
        buy_stop_1_entry = current_price + 2.0 + 0.3
        sell_stop_1_entry = current_price - 2.0 - 0.3
        tp_buy_1 = buy_stop_1_entry + 2.0
        tp_sell_1 = sell_stop_1_entry - 2.0
        res1_buy = place_pending_order(mt5_api, symbol, mt5_api.ORDER_TYPE_BUY_STOP, buy_stop_1_entry, tp_buy_1, 1.0*trade_amount, "new_grid_buy_1", logger)
        res1_sell = place_pending_order(mt5_api, symbol, mt5_api.ORDER_TYPE_SELL_STOP, sell_stop_1_entry, tp_sell_1, 1.0*trade_amount, "new_grid_sell_1", logger)
        order_results.extend([res1_buy, res1_sell])
        time.sleep(0.1)

        # Step 4
        buy_stop_2_entry = current_price + 2.0 + 2.0 + 0.3
        sell_stop_2_entry = current_price - 2.0 - 2.0 - 0.3
        tp_buy_2 = buy_stop_2_entry + 2.0
        tp_sell_2 = sell_stop_2_entry - 2.0
        res2_buy = place_pending_order(mt5_api, symbol, mt5_api.ORDER_TYPE_BUY_STOP, buy_stop_2_entry, tp_buy_2, 2.0*trade_amount, "new_grid_buy_2", logger)
        res2_sell = place_pending_order(mt5_api, symbol, mt5_api.ORDER_TYPE_SELL_STOP, sell_stop_2_entry, tp_sell_2, 2.0*trade_amount, "new_grid_sell_2", logger)
        order_results.extend([res2_buy, res2_sell])
        time.sleep(0.1)

        # List order IDs
        saved_orders = [r.order for r in order_results if r is not None and hasattr(r, 'order')]
        logger.info(f"order_results: {order_results}")
        logger.info(f"Saved order IDs: {saved_orders}")
        logger.info("Grid orders placed.")

        # Step 6: Monitor and notify if order filled or TP filled
        try:
            notified_filled = set()
            notified_tp = set()
            closed_pnl = 0
            idx = 0
            while True:
                idx += 1
                positions = mt5.get_positions()
                open_pnl = 0
                # Calculate open P&L for all open positions matching saved order IDs
                for pos in positions:
                    if pos.get('ticket') in saved_orders:
                        open_pnl += pos.get('profit', 0)
                        
                # Check closed positions for TP filled
                closed_positions = []
                history = []
                now = datetime.now()
                history = mt5_api.history_deals_get(script_start_time, now)
                
                # check if Pending order filled
                for oid in saved_orders:
                    if oid not in notified_filled:
                        if check_pending_order_filled(history, oid, logger):
                            # Determine side from comment
                            order_comment = None
                            for r in order_results:
                                if hasattr(r, 'order') and r.order == oid:
                                    order_comment = getattr(r, 'comment', None)
                                    break
                            if order_comment:
                                side = 'BUY' if 'buy' in order_comment else 'SELL'
                            else:
                                side = '?'
                            logger.info(f"🔥 Pending order filled: ID {oid} | {side}")
                            notified_filled.add(oid)
                            logger.info(f"Filled order IDs: {notified_filled}")
                
                # check if Position closed (TP filled)
                for oid in notified_filled:
                    if oid not in notified_tp:
                        if check_position_closed(mt5_api, oid, logger):
                            pnl = pos_closed_pnl(mt5_api, oid, logger)
                            logger.info(f"❤️ TP filled: Position ID {oid} closed | P&L: ${pnl:.2f}")
                            notified_tp.add(oid)
                            logger.info(f"TP filled order IDs: {notified_tp}")
                            # If buy TP filled, create corresponding sell stop
                            # Find which buy order this is
                            for r in order_results:
                                if hasattr(r, 'order') and r.order == oid:
                                    comment = getattr(r, 'comment', '')
                                    if 'buy' in comment:
                                        # Extract buy index
                                        try:
                                            idx_str = comment.split('_')[-1]
                                            buy_idx = int(idx_str)
                                        except Exception:
                                            buy_idx = None
                                        if buy_idx is not None:
                                            # Get price and amount for this buy
                                            price_buy = None
                                            amount_buy = None
                                            if buy_idx == 0:
                                                price_buy = buy_stop_1_entry
                                                amount_buy = 1.0 * trade_amount
                                            elif buy_idx == 1:
                                                price_buy = buy_stop_2_entry
                                                amount_buy = 2.0 * trade_amount
                                            elif buy_idx == 2:
                                                # If you add buy 3, update here
                                                price_buy = None
                                                amount_buy = None
                                            if price_buy is not None and amount_buy is not None:
                                                sell_stop_price = price_buy - 0.6
                                                tp_sell = sell_stop_price - 2.0
                                                sell_comment = f"new_grid_sell_-{buy_idx+1}"  # e.g., after buyTP_1, buyTP_2
                                                logger.info(f"🟦 Creating SELL STOP after BUY TP: price={sell_stop_price:.2f}, amount={amount_buy}, comment={sell_comment}")
                                                place_pending_order(mt5_api, symbol, mt5_api.ORDER_TYPE_SELL_STOP, sell_stop_price, tp_sell, amount_buy, sell_comment, logger)
                                    elif 'sell' in comment:
                                        # Extract sell index
                                        try:
                                            idx_str = comment.split('_')[-1]
                                            sell_idx = int(idx_str)
                                        except Exception:
                                            sell_idx = None
                                        if sell_idx is not None:
                                            # Get price and amount for this sell
                                            price_sell = None
                                            amount_sell = None
                                            if sell_idx == 0:
                                                price_sell = sell_stop_1_entry
                                                amount_sell = 1.0 * trade_amount
                                            elif sell_idx == 1:
                                                price_sell = sell_stop_2_entry
                                                amount_sell = 2.0 * trade_amount
                                            elif sell_idx == 2:
                                                # If you add sell 3, update here
                                                price_sell = None
                                                amount_sell = None
                                            if price_sell is not None and amount_sell is not None:
                                                buy_stop_price = price_sell + 0.6
                                                tp_buy = buy_stop_price + 2.0
                                                buy_comment = f"new_grid_buy_-{sell_idx+1}"  # e.g., after sellTP_1, sellTP_2
                                                logger.info(f"🟩 Creating BUY STOP after SELL TP: price={buy_stop_price:.2f}, amount={amount_sell}, comment={buy_comment}")
                                                place_pending_order(mt5_api, symbol, mt5_api.ORDER_TYPE_BUY_STOP, buy_stop_price, tp_buy, amount_sell, buy_comment, logger)
                            closed_pnl += pnl
                
                if idx % 50 == 0:
                    logger.info(f"Current open positions P&L: ${open_pnl:.2f}")
                    logger.info(f"Closed positions (TP filled) P&L: ${closed_pnl:.2f}")
                    logger.info(f"All P&L: ${closed_pnl + open_pnl:.2f}")
                    
                time.sleep(0.5)
        except KeyboardInterrupt:
            logger.info("Interrupted by user. Disconnecting...")
        mt5.disconnect()
    except Exception as e:
        logger.error(f"Error: {e}")

if __name__ == "__main__":
    main()
