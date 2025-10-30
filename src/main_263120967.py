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
from datetime import datetime, timedelta, timezone

from mt5_connector import MT5Connection
from config_manager import ConfigManager

from Libs.telegramBot import TelegramBot

src_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'src'))
if src_path not in sys.path:
    sys.path.insert(0, src_path)


################################################################################################
CONFIG_FILE = f"config/mt5_config_263120967.json"


################################################################################################
config = ConfigManager(CONFIG_FILE)

# Load Telegram configuration from config file
telegram_config = config.config.get('telegram', {})
TELEGRAM_API_TOKEN = telegram_config.get('api_token')
TELEGRAM_BOT_NAME = telegram_config.get('bot_name')
TELEGRAM_CHAT_ID = telegram_config.get('chat_id')

# Load trading configuration from config file
trading_config = config.config.get('trading', {})
FIBONACCI_LEVELS = trading_config.get('fibonacci_levels', [1, 1, 2, 2, 3, 3, 5, 8, 13, 13, 13, 13, 13])
TRADE_SYMBOL = trading_config.get('trade_symbol', "XAUUSDc")
DELTA_ENTER_PRICE = trading_config.get('delta_enter_price', 0.55)
TARGET_PROFIT = trading_config.get('target_profit', 2.1)
TRADE_AMOUNT = trading_config.get('trade_amount', 0.05)
PERCENT_SCALE = trading_config.get('percent_scale', 11)
MAX_REDUCE_BALANCE = trading_config.get('max_reduce_balance', 3000)
MIN_FREE_MARGIN = trading_config.get('min_free_margin', 100)

telegramBot = TelegramBot(TELEGRAM_API_TOKEN, TELEGRAM_BOT_NAME) if TELEGRAM_API_TOKEN else None

################################################################################################
# FIBONACCI_LEVELS = [1, 1, 2, 3, 5, 8, 13, 21, 34]

gTpExpected = 0
gDetailOrders = {
    'buy_9': {'status': None},
    'sell_9': {'status': None},
    'buy_8': {'status': None},
    'sell_8': {'status': None},
    'buy_7': {'status': None},
    'sell_7': {'status': None},
    'buy_6': {'status': None},
    'sell_6': {'status': None},
    'buy_5': {'status': None},
    'sell_5': {'status': None},
    'buy_4': {'status': None},
    'sell_4': {'status': None},
    'buy_3': {'status': None},
    'sell_3': {'status': None},
    'buy_2': {'status': None},
    'sell_2': {'status': None},
    'buy_1': {'status': None},
    'sell_1': {'status': None},
    'buy_0': {'status': None},
    'sell_0': {'status': None},
    'buy_-1': {'status': None},
    'sell_-1': {'status': None},
    'buy_-2': {'status': None},
    'sell_-2': {'status': None},
    'buy_-3': {'status': None},
    'sell_-3': {'status': None},
    'buy_-4': {'status': None},
    'sell_-4': {'status': None},
    'buy_-5': {'status': None},
    'sell_-5': {'status': None},
    'buy_-6': {'status': None},
    'sell_-6': {'status': None},
    'buy_-7': {'status': None},
    'sell_-7': {'status': None},
    'buy_-8': {'status': None},
    'sell_-8': {'status': None},
    'buy_-9': {'status': None},
    'sell_-9': {'status': None},
}
gCurrentIdx = 0
gStartBalance = 0
gMaxDrawdown = 0
gNotifiedFilled = set()
gBotPaused = False  # Flag to control bot pause state
gStopRequested = False  # Flag to indicate /stop command received
gNextTradeAmount = None  # New trade amount to use for next run
# Quiet hours settings (configurable at runtime)
gQuietHoursEnabled = True
gQuietHoursStart = 19
gQuietHoursEnd = 23
gQuietHoursFactor = 0.5  # Reduce to 50% during quiet hours
gSessionStartTime = None  # Tracks current run/session start time for status
gMaxDDThreshold = None  # Auto-pause drawdown threshold (account currency)
gMaxPositions = None  # Max concurrent strategy positions
gMaxOrders = None  # Max concurrent strategy pending orders
gMaxSpread = None  # Max allowed spread (price units)
gBlackoutEnabled = False
gBlackoutStart = 0
gBlackoutEnd = 0
gStopAtDateTime = None  # Scheduled pause time (GMT+7)

################################################################################################
def check_pending_order_filled(history, order_id, logger=None):
    res = False
    for record in history:
        # if record.position_id == order_id:
        #     if logger: logger.info(f"Found matching record: {record}")
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
        # if logger: logger.info(f"DEBUG :: pos_closed_pnl {res}")
        # for info in res:
        info = res[-1]
        if logger: logger.info(f"DEBUG :: pos_closed_pnl :: detail {info}")
        pnl += info.profit
    except Exception as e:
        if logger: logger.error(f"ERORR :: pos_closed_pnl :: {e}")
    return pnl

def get_current_balance(mt5_api, logger=None):
    current_balance = 0
    try:
        acc_info_mt5 = mt5_api.account_info() if mt5_api else None
        if logger: logger.info(f"DEBUG :: acc_info_mt5 {acc_info_mt5}")
        if acc_info_mt5 and hasattr(acc_info_mt5, 'balance'):
            current_balance = acc_info_mt5.balance
    except Exception as e:
        logger.error(f"Error getting start balance: {e}")
    return current_balance

def get_current_equity(mt5_api, logger=None):
    current_equity = 0
    try:
        acc_info_mt5 = mt5_api.account_info() if mt5_api else None
        if logger: logger.info(f"DEBUG :: acc_info_mt5 {acc_info_mt5}")
        if acc_info_mt5 and hasattr(acc_info_mt5, 'equity'):
            current_equity = acc_info_mt5.equity
    except Exception as e:
        logger.error(f"Error getting current equity: {e}")
    return current_equity

def get_current_free_margin(mt5_api, logger=None):
    current_free_margin = 0
    try:
        acc_info_mt5 = mt5_api.account_info() if mt5_api else None
        if logger: logger.info(f"DEBUG :: acc_info_mt5 {acc_info_mt5}")
        if acc_info_mt5 and hasattr(acc_info_mt5, 'margin_free'):
            current_free_margin = acc_info_mt5.margin_free
    except Exception as e:
        logger.error(f"Error getting current free margin: {e}")
    return current_free_margin


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
            telegramBot.send_message(
                f"⭕️ :: {comment} :: Order failed, retcode: {result.retcode}, comment: {result.comment}",
                chat_id=TELEGRAM_CHAT_ID,
            )
        return None
    order_type_str = "BUY STOP" if order_type == mt5_api.ORDER_TYPE_BUY_STOP else "SELL STOP"
    if logger:
        logger.info(f"✅ :: {comment} :: {order_type_str} order placed: {volume} lots at {price:.2f}, TP: {tp_price:.2f}")
    return result


###############################################################################################################
# Show order status list
def get_order_status_str(key, val):
    global gNotifiedFilled
    msg = ''
    try:
        order_obj = val.get('order')
        status = val.get('status')
        order_id = None
        price = None
        volume = None
        order_status = ''
        if order_obj:
            order_id = getattr(order_obj, 'order', None)
            price = getattr(order_obj.request, 'price', None)
            volume = getattr(order_obj.request, 'volume', None)
            order_status = getattr(order_obj, 'status', '')
            price = round(price, 3) if price is not None else None
            volume = round(volume, 2) if volume is not None else None
        # Check gNotifiedFilled for this order_id
        if order_id is not None and order_id in gNotifiedFilled:
            status_str = '✅'
        elif status == 'placed' and order_status != 'filled':
            status_str = '✔️'
        elif status == 'placed' and order_status == 'filled':
            status_str = '✅'
        else:
            status_str = '❔'
        side, idx = key.split('_')
        side_str = 'Buy' if side == 'buy' else 'Sell'
        idx_str = idx
        return f"Status: {status_str} {side_str} <b>{idx_str}</b>: <code>{price if price is not None else '-'}</code> {volume if volume is not None else '-'}"
    except Exception as e:
        print(f"ERROR in get_order_status_str: {e}")
    return msg

###################################################################################
def get_all_order_status_str(logger=None):
    global gDetailOrders
    all_status_report = ''
    try:
        # Sort keys: buys descending, sells ascending
        def order_sort_key(x):
            side, idx = x.split('_')
            idx = int(idx)
            return (0, idx)
        
        sorted_keys = sorted(gDetailOrders.keys(), key=order_sort_key)
        all_order_status_lines = []
        for key in sorted_keys:
            val = gDetailOrders.get(key, {})
            if val and val.get('order') is not None:
                all_order_status_lines.append(get_order_status_str(key, val))
        all_status_report = '\n'.join(all_order_status_lines)
    except Exception as e:
        if logger:
            logger.error(f"Error in get_all_order_status_str: {e}")
    return all_status_report

###############################################################################################################
def get_filled_orders_list(logger=None):
    """
    Get a list of order comments that are filled for future use
    Returns a dictionary with filled order details including comment, order_id, and side
    """
    global gDetailOrders, gNotifiedFilled
    filled_orders = []
    
    try:
        for key, val in gDetailOrders.items():
            if val and val.get('order') is not None:
                order_obj = val['order']
                order_id = getattr(order_obj, 'order', None)
                
                # Check if this order is filled (in gNotifiedFilled)
                if order_id and order_id in gNotifiedFilled:
                    order_comment = getattr(order_obj, 'comment', key)
                    order_price = getattr(order_obj.request, 'price', None)
                    order_volume = getattr(order_obj.request, 'volume', None)
                    
                    # Determine side from comment or key
                    side = 'BUY' if 'buy' in key.lower() else 'SELL'
                    
                    # Extract index from comment
                    try:
                        index = int(key.split('_')[-1])
                    except:
                        index = None
                    
                    filled_order_info = {
                        'key': key,
                        'comment': order_comment,
                        'order_id': order_id,
                        'side': side,
                        'index': index,
                        'price': round(order_price, 3) if order_price else None,
                        'volume': round(order_volume, 2) if order_volume else None
                    }
                    filled_orders.append(filled_order_info)
        
        # Sort by index for better readability
        filled_orders.sort(key=lambda x: (x['side'], x['index'] if x['index'] is not None else 0))
        
        if logger:
            logger.info(f"Found {len(filled_orders)} filled orders")
            for order in filled_orders:
                logger.info(f"Filled: {order['comment']} | {order['side']} | Index: {order['index']} | Price: {order['price']} | Volume: {order['volume']}")
                
    except Exception as e:
        if logger:
            logger.error(f"Error getting filled orders list: {e}")
    
    return filled_orders

def get_filled_orders_summary(logger=None):
    """
    Get a formatted summary string of filled orders for display
    """
    filled_orders = get_filled_orders_list(logger)
    
    if not filled_orders:
        return "No filled orders found."
    
    summary_lines = []
    summary_lines.append(f"📋 <b>Filled Orders Summary ({len(filled_orders)} orders)</b>\n")
    
    # Group by side
    buy_orders = [order for order in filled_orders if order['side'] == 'BUY']
    sell_orders = [order for order in filled_orders if order['side'] == 'SELL']
    
    if buy_orders:
        summary_lines.append("🟢 <b>BUY Orders Filled:</b>")
        for order in buy_orders:
            summary_lines.append(f"  • {order['comment']} | Price: {order['price']} | Vol: {order['volume']}")
        summary_lines.append("")
    
    if sell_orders:
        summary_lines.append("🔴 <b>SELL Orders Filled:</b>")
        for order in sell_orders:
            summary_lines.append(f"  • {order['comment']} | Price: {order['price']} | Vol: {order['volume']}")
    
    return '\n'.join(summary_lines)

def check_consecutive_orders_pattern(logger=None):
    """
    Check for consecutive order patterns that might affect strategy decisions
    Returns information about consecutive buy/sell patterns
    """
    filled_orders = get_filled_orders_list(logger)
    
    if len(filled_orders) < 2:
        return {"consecutive_buys": [], "consecutive_sells": [], "pattern_detected": False}
    
    # Group by side and sort by index
    buy_orders = sorted([order for order in filled_orders if order['side'] == 'BUY'], 
                       key=lambda x: x['index'] if x['index'] is not None else 0)
    sell_orders = sorted([order for order in filled_orders if order['side'] == 'SELL'], 
                        key=lambda x: x['index'] if x['index'] is not None else 0)
    
    consecutive_buys = []
    consecutive_sells = []
    
    # Check for consecutive buy patterns
    for i in range(len(buy_orders) - 1):
        if (buy_orders[i]['index'] is not None and 
            buy_orders[i+1]['index'] is not None and
            buy_orders[i+1]['index'] == buy_orders[i]['index'] + 1):
            consecutive_buys.append((buy_orders[i], buy_orders[i+1]))
    
    # Check for consecutive sell patterns  
    for i in range(len(sell_orders) - 1):
        if (sell_orders[i]['index'] is not None and 
            sell_orders[i+1]['index'] is not None and
            sell_orders[i+1]['index'] == sell_orders[i]['index'] - 1):
            consecutive_sells.append((sell_orders[i], sell_orders[i+1]))
    
    pattern_detected = len(consecutive_buys) > 0 or len(consecutive_sells) > 0
    
    if logger and pattern_detected:
        logger.info(f"Consecutive patterns detected - Buys: {len(consecutive_buys)}, Sells: {len(consecutive_sells)}")
    
    return {
        "consecutive_buys": consecutive_buys,
        "consecutive_sells": consecutive_sells, 
        "pattern_detected": pattern_detected,
        "total_filled": len(filled_orders)
    }

def run_at_index(mt5_api, symbol, amount, index, price=0, logger=None):
    global gDetailOrders
    global gStartBalance
    global gNotifiedFilled
    global gBlackoutEnabled, gBlackoutStart, gBlackoutEnd
    global gMaxPositions, gMaxOrders, gMaxSpread

    try:
        # Blackout check (GMT+7)
        gmt_plus_7 = timezone(timedelta(hours=7))
        now_gmt7 = datetime.now(gmt_plus_7)
        current_hour = now_gmt7.hour
        in_blackout = (
            gBlackoutEnabled and (
                (gBlackoutStart <= gBlackoutEnd and gBlackoutStart <= current_hour <= gBlackoutEnd) or
                (gBlackoutStart > gBlackoutEnd and (current_hour >= gBlackoutStart or current_hour <= gBlackoutEnd))
            )
        )
        if in_blackout:
            if logger:
                logger.info(f"⛔️ Blackout window active {gBlackoutStart:02d}-{gBlackoutEnd:02d} GMT+7. Skipping grid build.")
            if telegramBot:
                telegramBot.send_message(
                    f"⛔️ Blackout window active {gBlackoutStart:02d}-{gBlackoutEnd:02d} GMT+7. Skipping grid build.",
                    chat_id=TELEGRAM_CHAT_ID,
                )
            return

        current_equity = get_current_equity(mt5_api, logger=logger)
        current_fee_margin = get_current_free_margin(mt5_api, logger=logger)
        if current_equity < gStartBalance - MAX_REDUCE_BALANCE:
            if logger:
                logger.error(f"⛔️ Current equity {current_equity} has reduced more than {MAX_REDUCE_BALANCE} from start balance {gStartBalance}. Stopping further trades.")
            telegramBot.send_message(f"⛔️ Current equity {current_equity} has reduced more than {MAX_REDUCE_BALANCE} from start balance {gStartBalance}. Stopping further trades.", chat_id=TELEGRAM_CHAT_ID)
            return
        
        if current_fee_margin < MIN_FREE_MARGIN:
            if logger:
                logger.error(f"⛔️ Current free margin {current_fee_margin} is below minimum required {MIN_FREE_MARGIN}. Stopping further trades.")
            telegramBot.send_message(f"⛔️ Current free margin {current_fee_margin} is below minimum required {MIN_FREE_MARGIN}. Stopping further trades.", chat_id=TELEGRAM_CHAT_ID)
            return
        
        # Get current price from MT5
        tick = mt5_api.symbol_info_tick(symbol)
        if not tick:
            if logger:
                logger.error(f"Could not get tick for {symbol}")
            return
        # Spread cap
        try:
            spread = (tick.ask - tick.bid) if (hasattr(tick, 'ask') and hasattr(tick, 'bid')) else 0.0
        except Exception:
            spread = 0.0
        if gMaxSpread is not None and spread > gMaxSpread:
            if logger:
                logger.info(f"⛔️ Spread {spread:.3f} > max {gMaxSpread:.3f}. Skipping grid build.")
            if telegramBot:
                telegramBot.send_message(
                    f"⛔️ Spread {spread:.3f} > max {gMaxSpread:.3f}. Skipping grid build.",
                    chat_id=TELEGRAM_CHAT_ID,
                )
            return

        # price = tick.ask if tick.ask else tick.last
        if not price:
            price = (tick.bid + tick.ask) / 2
        if logger:
            logger.info(f"run_at_index: Current price for {symbol}: {price:.2f}")

        percent0 = abs(index) / 100      * PERCENT_SCALE
        percent1 = abs(index + 1) / 100 * PERCENT_SCALE
        percent2 = abs(index + 2) / 100 * PERCENT_SCALE
        percent_1 = abs(index - 1) / 100 * PERCENT_SCALE
        percent_2 = abs(index - 2) / 100 * PERCENT_SCALE

        pypass_buy1 = False
        pypass_sell1 = False
        pattern_data = check_consecutive_orders_pattern(logger)
        if pattern_data['pattern_detected']:
            if len(pattern_data['consecutive_buys']) >= 2:
                logger.warning("⚠️ Strong upward trend detected - consider reducing BUY exposure")
                pypass_sell1 = True

            if len(pattern_data['consecutive_sells']) >= 2:
                logger.warning("⚠️ Strong downward trend detected - consider reducing SELL exposure")
                pypass_buy1 = True

        # Calculate buy stop entries and TP
        buy_entry_1 = price + DELTA_ENTER_PRICE * (1 + percent0)
        buy_tp_1 = buy_entry_1 + TARGET_PROFIT * (1 + percent0)
        buy_entry_2 = price + TARGET_PROFIT * (1 + percent0) + DELTA_ENTER_PRICE * (1 + percent1)
        buy_tp_2 = buy_entry_2 + TARGET_PROFIT * (1 + percent1)
        buy_entry_3 = price + TARGET_PROFIT * (1 + percent0) + TARGET_PROFIT * (1 + percent1) + DELTA_ENTER_PRICE * (1 + percent2)
        buy_tp_3 = buy_entry_3 + TARGET_PROFIT * (1 + percent2)

        # Calculate sell stop entries and TP
        sell_entry_1 = price - DELTA_ENTER_PRICE * (1 + percent0)
        sell_tp_1 = sell_entry_1 - TARGET_PROFIT * (1 + percent0)
        sell_entry_2 = price - TARGET_PROFIT * (1 + percent0) - DELTA_ENTER_PRICE * (1 + percent_1)
        sell_tp_2 = sell_entry_2 - TARGET_PROFIT * (1 + percent_1)
        sell_entry_3 = price - TARGET_PROFIT * (1 + percent0) - TARGET_PROFIT * (1 + percent_1) - DELTA_ENTER_PRICE * (1 + percent_2)
        sell_tp_3 = sell_entry_3 - TARGET_PROFIT * (1 + percent_2)

        # Use trade amount scaled by FIBONACCI_LEVELS
        fibb_amount_1 = amount * FIBONACCI_LEVELS[abs(index)]
        fibb_amount_2 = amount * FIBONACCI_LEVELS[abs(index+1)] if abs(index+1) < len(FIBONACCI_LEVELS) else amount
        fibb_amount_3 = amount * FIBONACCI_LEVELS[abs(index+2)] if abs(index+2) < len(FIBONACCI_LEVELS) else amount

        fibs_amount_1 = amount * FIBONACCI_LEVELS[abs(index)]
        fibs_amount_2 = amount * FIBONACCI_LEVELS[abs(index-1)] if abs(index-1) < len(FIBONACCI_LEVELS) else amount
        fibs_amount_3 = amount * FIBONACCI_LEVELS[abs(index-2)] if abs(index-2) < len(FIBONACCI_LEVELS) else amount

        # Capacity caps for positions/orders
        try:
            # Count strategy positions
            pos_count = 0
            for p in (mt5_api.positions_get(symbol=symbol) or []):
                if getattr(p, 'magic', None) == 234002:
                    pos_count += 1
            # Count strategy pending orders
            ord_count = 0
            for o in (mt5_api.orders_get(symbol=symbol) or []):
                if getattr(o, 'magic', None) == 234002:
                    ord_count += 1
            if (gMaxPositions is not None and pos_count >= gMaxPositions) or (
                gMaxOrders is not None and ord_count >= gMaxOrders
            ):
                if logger:
                    logger.info(f"⛔️ Capacity cap reached (pos {pos_count}/{gMaxPositions or '∞'}, orders {ord_count}/{gMaxOrders or '∞'}). Skipping grid build.")
                if telegramBot:
                    telegramBot.send_message(
                        f"⛔️ Capacity cap reached (pos {pos_count}/{gMaxPositions or '∞'}, orders {ord_count}/{gMaxOrders or '∞'}). Skipping grid build.",
                        chat_id=TELEGRAM_CHAT_ID,
                    )
                return
        except Exception as e:
            if logger:
                logger.debug(f"Capacity cap check error: {e}")

        # Place buy stop orders only if not already placed
        buy_comment_1 = f"buy_{index}"
        buy_comment_2 = f"buy_{index+1}"
        buy_comment_3 = f"buy_{index+2}"
        # Place sell stop orders only if not already placed
        sell_comment_1 = f"sell_{index}"
        sell_comment_2 = f"sell_{index-1}"
        sell_comment_3 = f"sell_{index-2}"

        new_orders = []
        if gDetailOrders.get(buy_comment_1, {}).get('status') != 'placed':
            if not pypass_buy1:
                res_buy_1 = place_pending_order(mt5_api, symbol, mt5_api.ORDER_TYPE_BUY_STOP, buy_entry_1, buy_tp_1, fibb_amount_1, buy_comment_1, logger)
                if res_buy_1:
                    gDetailOrders[buy_comment_1] = {'status': 'placed', 'order': res_buy_1}
                    new_orders.append(res_buy_1)
        if gDetailOrders.get(sell_comment_1, {}).get('status') != 'placed':
            if not pypass_sell1:
                res_sell_1 = place_pending_order(mt5_api, symbol, mt5_api.ORDER_TYPE_SELL_STOP, sell_entry_1, sell_tp_1, fibs_amount_1, sell_comment_1, logger)
                if res_sell_1:
                    gDetailOrders[sell_comment_1] = {'status': 'placed', 'order': res_sell_1}
                    new_orders.append(res_sell_1)

        if gDetailOrders.get(buy_comment_2, {}).get('status') != 'placed':
            res_buy_2 = place_pending_order(mt5_api, symbol, mt5_api.ORDER_TYPE_BUY_STOP, buy_entry_2, buy_tp_2, fibb_amount_2, buy_comment_2, logger)
            if res_buy_2:
                gDetailOrders[buy_comment_2] = {'status': 'placed', 'order': res_buy_2}
                new_orders.append(res_buy_2)
        if gDetailOrders.get(sell_comment_2, {}).get('status') != 'placed':
            res_sell_2 = place_pending_order(mt5_api, symbol, mt5_api.ORDER_TYPE_SELL_STOP, sell_entry_2, sell_tp_2, fibs_amount_2, sell_comment_2, logger)
            if res_sell_2:
                gDetailOrders[sell_comment_2] = {'status': 'placed', 'order': res_sell_2}
                new_orders.append(res_sell_2)

        if gDetailOrders.get(buy_comment_3, {}).get('status') != 'placed':
            res_buy_3 = place_pending_order(mt5_api, symbol, mt5_api.ORDER_TYPE_BUY_STOP, buy_entry_3, buy_tp_3, fibb_amount_3, buy_comment_3, logger)
            if res_buy_3:
                gDetailOrders[buy_comment_3] = {'status': 'placed', 'order': res_buy_3}
                new_orders.append(res_buy_3)
        if gDetailOrders.get(sell_comment_3, {}).get('status') != 'placed':
            res_sell_3 = place_pending_order(mt5_api, symbol, mt5_api.ORDER_TYPE_SELL_STOP, sell_entry_3, sell_tp_3, fibs_amount_3, sell_comment_3, logger)
            if res_sell_3:
                gDetailOrders[sell_comment_3] = {'status': 'placed', 'order': res_sell_3}
                new_orders.append(res_sell_3)

        # Show all keys in gDetailOrders
        if len(new_orders) > 0:
            telegramBot.send_message(f"<b>New Orders Placed:</b>\n\n" + '\n'.join([get_order_status_str(k, gDetailOrders[k]) for k in sorted(gDetailOrders.keys()) if gDetailOrders[k].get('order') in new_orders]), chat_id=TELEGRAM_CHAT_ID)
            if logger:
                logger.info(f"Grid orders placed for index {index}: buy/sell stops at {buy_entry_1:.2f}, {buy_entry_2:.2f}, {buy_entry_3:.2f}, {sell_entry_1:.2f}, {sell_entry_2:.2f}, {sell_entry_3:.2f}")
    except Exception as e:
        logger.error(f"ERROR :: {e}")

def close_all_positions(mt5_api, symbol, logger=None):
    try:
        positions = mt5_api.positions_get(symbol=symbol)
        if not positions:
            if logger:
                logger.info(f"No open positions to close for {symbol}.")
            return
        
        # Collect all order IDs from gDetailOrders with status 'placed'
        strategy_order_ids = set()
        for key, val in gDetailOrders.items():
            if val.get('status') == 'placed' and val.get('order') is not None:
                order_obj = val['order']
                oid = getattr(order_obj, 'order', None)
                if oid is not None:
                    strategy_order_ids.add(oid)
        
        positions_closed = 0
        for pos in positions:
            ticket = getattr(pos, 'ticket', None)
            volume = getattr(pos, 'volume', None)
            type_ = getattr(pos, 'type', None)
            magic = getattr(pos, 'magic', None)
            
            if ticket is None or volume is None or type_ is None:
                if logger:
                    logger.warning(f"Could not get ticket/volume/type for position: {pos}")
                continue
            
            # Only close positions that belong to this strategy
            # Check both magic number and if position ID is in our tracked orders
            # if magic != 234002 and ticket not in strategy_order_ids:
            # if ticket not in strategy_order_ids:
            #     if logger:
            #         logger.debug(f"Skipping position {ticket} - not from this strategy (magic: {magic})")
            #     continue
            
            # Determine close type
            if type_ == mt5_api.POSITION_TYPE_BUY:
                close_type = mt5_api.ORDER_TYPE_SELL
            elif type_ == mt5_api.POSITION_TYPE_SELL:
                close_type = mt5_api.ORDER_TYPE_BUY
            else:
                if logger:
                    logger.warning(f"Unknown position type for ticket {ticket}: {type_}")
                continue
            
            # Try supported filling modes
            filling_modes = [mt5_api.ORDER_FILLING_IOC, mt5_api.ORDER_FILLING_FOK, mt5_api.ORDER_FILLING_RETURN]
            success = False
            for fill_mode in filling_modes:
                request = {
                    "action": mt5_api.TRADE_ACTION_DEAL,
                    "symbol": symbol,
                    "volume": volume,
                    "type": close_type,
                    "position": ticket,
                    "deviation": 20,
                    "magic": 234002,
                    "comment": f"close_strategy_positions",
                    "type_time": mt5_api.ORDER_TIME_GTC,
                    "type_filling": fill_mode,
                }
                result = mt5_api.order_send(request)
                if result is None:
                    if logger:
                        logger.error(f"Failed to close position {ticket} (mode {fill_mode}): {mt5_api.last_error()}")
                elif result.retcode == mt5_api.TRADE_RETCODE_DONE:
                    if logger:
                        logger.info(f"✅ Closed strategy position {ticket} for {symbol}, volume {volume} (mode {fill_mode})")
                    positions_closed += 1
                    success = True
                    break
                else:
                    if logger:
                        logger.error(f"Failed to close position {ticket} (mode {fill_mode}): retcode {result.retcode}, comment: {result.comment}")
            if not success and logger:
                logger.error(f"❌ Could not close position {ticket} for {symbol} with any supported filling mode.")
        
        if logger:
            logger.info(f"Strategy positions closed: {positions_closed} out of {len(positions)} total positions for {symbol}")
            
    except Exception as e:
        if logger:
            logger.error(f"Error closing strategy positions: {e}")


def cancel_all_pending_orders(mt5_api, symbol, logger=None):
    try:
        orders = mt5_api.orders_get(symbol=symbol)
        if not orders:
            if logger:
                logger.info(f"No pending orders to cancel for {symbol}.")
            return
            
        # Collect all order IDs from gDetailOrders with status 'placed'
        strategy_order_ids = set()
        for key, val in gDetailOrders.items():
            if val.get('status') == 'placed' and val.get('order') is not None:
                order_obj = val['order']
                oid = getattr(order_obj, 'order', None)
                if oid is not None:
                    strategy_order_ids.add(oid)
        
        orders_cancelled = 0
        for order in orders:
            ticket = getattr(order, 'ticket', None)
            magic = getattr(order, 'magic', None)
            
            if ticket is None:
                if logger:
                    logger.warning(f"Could not get ticket for order: {order}")
                continue
            
            # Only cancel pending orders that belong to this strategy
            # Check both magic number and if order ID is in our tracked orders
            # if magic != 234002 and ticket not in strategy_order_ids:
            #     if logger:
            #         logger.debug(f"Skipping order {ticket} - not from this strategy (magic: {magic})")
            #     continue
            
            request = {
                "action": mt5_api.TRADE_ACTION_REMOVE,
                "order": ticket,
                "symbol": symbol,
                "magic": 234002,
                "comment": "cancel_strategy_orders",
            }
            result = mt5_api.order_send(request)
            if result is None:
                if logger:
                    logger.error(f"Failed to cancel pending order {ticket}: {mt5_api.last_error()}")
            elif result.retcode != mt5_api.TRADE_RETCODE_DONE:
                if logger:
                    logger.error(f"Failed to cancel pending order {ticket}: retcode {result.retcode}, comment: {result.comment}")
            else:
                if logger:
                    logger.info(f"✅ Cancelled strategy order {ticket} for {symbol}")
                orders_cancelled += 1
                # telegramBot.send_message(f"✅ Cancelled pending order {ticket} for {symbol}", chat_id=TELEGRAM_CHAT_ID)
        
        if logger:
            logger.info(f"Strategy orders cancelled: {orders_cancelled} out of {len(orders)} total orders for {symbol}")
            
    except Exception as e:
        if logger:
            logger.error(f"Error cancelling strategy pending orders: {e}")


###############################################################################################################
def monitor_drawdown(mt5_api, logger=None):
    global gMaxDrawdown
    global gStartBalance
    try:
        current_equity = get_current_equity(mt5_api)
        if current_equity < gStartBalance:
            gMaxDrawdown = max(gMaxDrawdown, gStartBalance - current_equity)
            if logger:
                logger.info(f"New max drawdown recorded: {gMaxDrawdown}")
    except Exception as e:
        if logger:
            logger.error(f"Error monitoring drawdown: {e}")

def drawdown_report():
    global gMaxDrawdown
    global gStartBalance
    msg = ''
    try:
        msg = f"📉 <b>Drawdown Report</b>\n\n"
        msg += f"Start Balance: {gStartBalance:.2f}\n"
        msg += f"Max Drawdown: {gMaxDrawdown:.2f}\n"
        msg += f"Percentage Drawdown: {(gMaxDrawdown / gStartBalance * 100):.2f}%\n"
    except Exception as e:
        print(f"Error generating drawdown report: {e}")
    return msg


###############################################################################################################
def handle_telegram_command(bot, mt5_api=None, logger=None):
    """
    Handle incoming Telegram commands
    """
    global gBotPaused
    global gStopRequested
    global gNextTradeAmount
    global gQuietHoursEnabled, gQuietHoursStart, gQuietHoursEnd, gQuietHoursFactor
    global gSessionStartTime
    global gMaxDDThreshold, gMaxPositions, gMaxOrders, gMaxSpread
    global gBlackoutEnabled, gBlackoutStart, gBlackoutEnd, gStopAtDateTime
    
    try:
        # Get updates from Telegram
        updates = bot.bot.get_updates(timeout=1)
        
        for update in updates:
            if update.message and update.message.text:
                chat_id = update.message.chat.id
                text = update.message.text.strip()
                
                if logger:
                    logger.info(f"Received Telegram command: {text} from chat_id: {chat_id}")
                
                # Handle /start command
                if text == '/start':
                    # Get account number
                    account_number = "N/A"
                    if mt5_api:
                        try:
                            acc_info = mt5_api.account_info()
                            if acc_info and hasattr(acc_info, 'login'):
                                account_number = acc_info.login
                        except Exception as e:
                            if logger:
                                logger.debug(f"Could not get account info: {e}")
                    
                    # Resume bot if it was paused
                    if gBotPaused:
                        gBotPaused = False
                        gStopRequested = False
                        resume_msg = f"▶️ <b>Bot Resumed!</b>\n\n"
                        resume_msg += f"• Account: {account_number}\n"
                        resume_msg += f"• Symbol: {TRADE_SYMBOL}\n"
                        resume_msg += f"• Trade Amount: {TRADE_AMOUNT}\n"
                        resume_msg += f"• Status: Running ✅\n\n"
                        resume_msg += f"The bot will now resume trading operations."
                        
                        bot.send_message(resume_msg, chat_id=chat_id, disable_notification=False)
                        
                        if logger:
                            logger.info(f"Bot resumed by user command from chat_id: {chat_id}")
                    else:
                        welcome_msg = f"👋 <b>Hello!</b>\n\n"
                        welcome_msg += f"• Account: {account_number}\n\n"
                        welcome_msg += f"Welcome to the Grid DCA Trading Bot for {TRADE_SYMBOL}!\n\n"
                        welcome_msg += f"<b>Bot Status:</b>\n"
                        welcome_msg += f"• Strategy: Grid DCA\n"
                        welcome_msg += f"• Symbol: {TRADE_SYMBOL}\n"
                        welcome_msg += f"• Trade Amount: {TRADE_AMOUNT}\n"
                        welcome_msg += f"• Status: Running ✅\n\n"
                        welcome_msg += f"You will receive notifications about:\n"
                        welcome_msg += f"• New orders placed\n"
                        welcome_msg += f"• Orders filled\n"
                        welcome_msg += f"• Take profit achieved\n"
                        welcome_msg += f"• Risk alerts\n\n"
                        welcome_msg += f"<b>Commands:</b>\n"
                        welcome_msg += f"• /start - Resume bot (if stopped)\n"
                        welcome_msg += f"• /stop - Stop bot after next TP\n"
                        welcome_msg += f"• /setamount X.XX - Set trade amount for next run\n"
                        
                        bot.send_message(welcome_msg, chat_id=chat_id, disable_notification=False)
                        
                        if logger:
                            logger.info(f"Sent welcome message to chat_id: {chat_id}")
                
                # Handle /stop command
                elif text == '/stop':
                    if not gStopRequested:
                        gStopRequested = True
                        stop_msg = f"⏸️ <b>Stop Requested</b>\n\n"
                        stop_msg += f"The bot will:\n"
                        stop_msg += f"1. Continue running until next target profit\n"
                        stop_msg += f"2. Close all positions when TP is reached\n"
                        stop_msg += f"3. Pause and wait for /start command\n\n"
                        stop_msg += f"Current status: Waiting for TP... 💤"
                        
                        bot.send_message(stop_msg, chat_id=chat_id, disable_notification=False)
                        
                        if logger:
                            logger.info(f"Stop requested by user from chat_id: {chat_id}")
                    else:
                        already_stopped_msg = f"⏸️ Stop already requested. Bot will pause after next TP."
                        bot.send_message(already_stopped_msg, chat_id=chat_id, disable_notification=False)
                
                # Handle /setamount command
                elif text.startswith('/setamount'):
                    try:
                        parts = text.split()
                        if len(parts) == 2:
                            new_amount = float(parts[1])
                            if new_amount > 0:
                                gNextTradeAmount = new_amount
                                amount_msg = f"💰 <b>Trade Amount Updated</b>\n\n"
                                amount_msg += f"• Configured amount: {TRADE_AMOUNT}\n"
                                amount_msg += f"• Override amount (persistent): {gNextTradeAmount}\n\n"
                                amount_msg += (
                                    "The override will be applied after the next target profit is reached "
                                    "and will persist for all subsequent runs until you change it again."
                                )
                            
                                bot.send_message(amount_msg, chat_id=chat_id, disable_notification=False)
                            
                                if logger:
                                    logger.info(f"Trade amount set to {gNextTradeAmount} for next run")
                            else:
                                error_msg = f"❌ Invalid amount. Please provide a positive number.\nExample: /setamount 0.05"
                                bot.send_message(error_msg, chat_id=chat_id, disable_notification=False)
                        else:
                            error_msg = f"❌ Invalid format.\nUsage: /setamount X.XX\nExample: /setamount 0.05"
                            bot.send_message(error_msg, chat_id=chat_id, disable_notification=False)
                    except ValueError:
                        error_msg = f"❌ Invalid number format.\nUsage: /setamount X.XX\nExample: /setamount 0.05"
                        bot.send_message(error_msg, chat_id=chat_id, disable_notification=False)
                    except Exception as e:
                        error_msg = f"❌ Error setting trade amount: {str(e)}"
                        bot.send_message(error_msg, chat_id=chat_id, disable_notification=False)
                        if logger:
                            logger.error(f"Error in /setamount command: {e}")

                # Handle /status command
                elif text == '/status':
                    try:
                        # Account info
                        acc_info = mt5_api.account_info() if mt5_api else None
                        login = getattr(acc_info, 'login', 'N/A') if acc_info else 'N/A'
                        balance = getattr(acc_info, 'balance', 0.0) if acc_info else 0.0
                        equity = getattr(acc_info, 'equity', 0.0) if acc_info else 0.0
                        free_margin = getattr(acc_info, 'margin_free', 0.0) if acc_info else 0.0

                        # Positions and orders (strategy-only via magic)
                        open_positions = mt5_api.positions_get(symbol=TRADE_SYMBOL) if mt5_api else []
                        pos_count = 0
                        open_pnl = 0.0
                        for p in open_positions or []:
                            if getattr(p, 'magic', None) == 234002:
                                pos_count += 1
                                open_pnl += float(getattr(p, 'profit', 0.0))

                        pending_orders = mt5_api.orders_get(symbol=TRADE_SYMBOL) if mt5_api else []
                        order_count = 0
                        for o in pending_orders or []:
                            if getattr(o, 'magic', None) == 234002:
                                order_count += 1

                        status_str = 'Paused ⏸️' if gBotPaused else ('Stopping after TP ⏳' if gStopRequested else 'Running ✅')
                        next_amount_str = f"{gNextTradeAmount}" if 'gNextTradeAmount' in globals() and gNextTradeAmount else '-'
                        # Run time
                        run_time_str = '-'
                        try:
                            if gSessionStartTime:
                                run_time = datetime.now() - gSessionStartTime
                                run_time_str = str(run_time).split('.')[0]
                        except Exception:
                            pass

                        msg = f"🤖 <b>Bot Status</b>\n\n"
                        msg += f"• Account: {login}\n"
                        msg += f"• Symbol: {TRADE_SYMBOL}\n"
                        msg += f"• Status: {status_str}\n"
                        # Scheduled stop info
                        try:
                            if 'gStopAtDateTime' in globals() and gStopAtDateTime:
                                msg += f"• Stop at: {gStopAtDateTime.strftime('%Y-%m-%d %H:%M')} GMT+7\n"
                        except Exception:
                            pass
                        msg += f"• Current Index: {gCurrentIdx}\n"
                        msg += f"• Target Profit Threshold: ${gTpExpected:.2f}\n\n"
                        msg += f"<b>Session</b>\n"
                        msg += f"• Run time: {run_time_str}\n\n"
                        msg += f"<b>Account</b>\n"
                        msg += f"• Balance: ${balance:.2f}\n"
                        msg += f"• Equity: ${equity:.2f}\n"
                        msg += f"• Free Margin: ${free_margin:.2f}\n\n"
                        msg += f"<b>Positions & Orders</b>\n"
                        msg += f"• Open positions: {pos_count}\n"
                        msg += f"• Pending orders: {order_count}\n"
                        msg += f"• Open PnL (strategy): ${open_pnl:.2f}\n\n"
                        msg += f"<b>Trade Amount</b>\n"
                        msg += f"• Configured amount: {TRADE_AMOUNT}\n"
                        msg += f"• Next run override: {next_amount_str}\n\n"
                        msg += f"<b>Guards</b>\n"
                        try:
                            qh_state = 'on' if gQuietHoursEnabled else 'off'
                            msg += f"• Quiet hours: {qh_state} ({gQuietHoursStart:02d}-{gQuietHoursEnd:02d} x{gQuietHoursFactor})\n"
                            bl_state = 'on' if gBlackoutEnabled else 'off'
                            msg += f"• Blackout: {bl_state} ({gBlackoutStart:02d}-{gBlackoutEnd:02d})\n"
                            msg += f"• Caps: maxDD={gMaxDDThreshold}, maxPos={gMaxPositions}, maxOrders={gMaxOrders}, maxSpread={gMaxSpread}\n"
                        except Exception:
                            pass

                        bot.send_message(msg, chat_id=chat_id, disable_notification=False)
                    except Exception as e:
                        if logger:
                            logger.error(f"Error building /status: {e}")
                        bot.send_message("❌ Failed to get status.", chat_id=chat_id, disable_notification=False)

                # Handle /clearamount command
                elif text.strip().lower() == '/clearamount':
                    try:
                        if 'gNextTradeAmount' in globals() and gNextTradeAmount is not None:
                            cleared = gNextTradeAmount
                            gNextTradeAmount = None
                            bot.send_message(
                                f"🧹 Cleared persistent amount override (was: {cleared}).\n"
                                f"Bot will use configured/time-based amount going forward.",
                                chat_id=chat_id,
                                disable_notification=False,
                            )
                            if logger:
                                logger.info("Persistent trade amount override cleared")
                        else:
                            bot.send_message("ℹ️ No persistent override set.", chat_id=chat_id, disable_notification=False)
                    except Exception as e:
                        if logger:
                            logger.error(f"Error handling /clearamount: {e}")
                        bot.send_message("❌ Failed to clear override.", chat_id=chat_id, disable_notification=False)

                # Handle /quiethours command
                elif text.startswith('/quiethours'):
                    try:
                        parts = text.split()
                        if len(parts) == 1:
                            state = 'on' if gQuietHoursEnabled else 'off'
                            bot.send_message(
                                (
                                    f"🕰️ <b>Quiet Hours</b> {state}\n"
                                    f"Window: {gQuietHoursStart:02d}-{gQuietHoursEnd:02d} GMT+7\n"
                                    f"Factor: x{gQuietHoursFactor}\n\n"
                                    "Usage:\n"
                                    "/quiethours on|off\n"
                                    "/quiethours HH-HH [factor]\n"
                                    "Example: /quiethours 19-23 0.5"
                                ),
                                chat_id=chat_id,
                                disable_notification=False,
                            )
                        elif len(parts) == 2 and parts[1].lower() in ('on','off'):
                            gQuietHoursEnabled = parts[1].lower() == 'on'
                            bot.send_message(
                                f"🕰️ Quiet hours {'enabled' if gQuietHoursEnabled else 'disabled'}.",
                                chat_id=chat_id,
                                disable_notification=False,
                            )
                        else:
                            # Expect HH-HH [factor]
                            rng = parts[1]
                            if '-' not in rng:
                                raise ValueError('Range must be HH-HH')
                            start_s, end_s = rng.split('-', 1)
                            start = int(start_s)
                            end = int(end_s)
                            if not (0 <= start <= 23 and 0 <= end <= 23):
                                raise ValueError('Hours must be 0-23')
                            factor = gQuietHoursFactor
                            if len(parts) >= 3:
                                factor = float(parts[2])
                                if factor <= 0:
                                    raise ValueError('Factor must be > 0')
                            gQuietHoursStart = start
                            gQuietHoursEnd = end
                            gQuietHoursFactor = factor
                            gQuietHoursEnabled = True
                            bot.send_message(
                                f"🕰️ Quiet hours set: {start:02d}-{end:02d} (GMT+7), factor x{factor}. Enabled.",
                                chat_id=chat_id,
                                disable_notification=False,
                            )
                    except Exception as e:
                        if logger:
                            logger.error(f"Error handling /quiethours: {e}")
                        bot.send_message("❌ Failed to update quiet hours.", chat_id=chat_id, disable_notification=False)

                # Handle /help command
                elif text == '/help':
                    try:
                        help_msg = (
                            "📖 <b>Available Commands</b>\n\n"
                            "<b>Control</b>\n"
                            "• /start — Resume bot (if paused)\n"
                            "• /resume — Alias of /start\n"
                            "• /pause — Pause immediately (no new grids)\n"
                            "• /stop — Finish current cycle, pause after TP\n"
                            "• /stopat HH:MM — Schedule pause at time (GMT+7)\n"
                            "• /panic — Emergency stop (requires '/panic confirm')\n\n"
                            "<b>Configuration</b>\n"
                            "• /setamount X.XX — Set persistent override (applies after next TP)\n"
                            "• /clearamount — Remove persistent override\n"
                            "• /quiethours — Show or set quiet-hours window and factor\n"
                            "• /setmaxdd X — Auto-pause if drawdown exceeds X\n"
                            "• /setmaxpos N — Cap concurrent positions\n"
                            "• /setmaxorders N — Cap concurrent pending orders\n"
                            "• /setspread X — Max allowed spread\n"
                            "• /blackout — Show or set a full trade blackout window\n\n"
                            "<b>Insights</b>\n"
                            "• /status — Bot and account status\n"
                            "• /drawdown — Show drawdown report\n"
                            "• /history N — Last N deals\n"
                            "• /pnl today|week|month — Aggregated PnL\n"
                            "• /filled — Show filled orders summary\n"
                            "• /pattern — Show consecutive filled-order pattern\n\n"
                            "<b>Examples</b>\n"
                            "• /setamount 0.05\n"
                            "• /stopat 21:00\n"
                            "• /setmaxdd 300\n"
                            "• /setspread 0.30\n"
                            "• /panic confirm\n"
                        )
                        bot.send_message(help_msg, chat_id=chat_id, disable_notification=False)
                    except Exception as e:
                        if logger:
                            logger.error(f"Error building /help: {e}")
                        bot.send_message("❌ Failed to build help.", chat_id=chat_id, disable_notification=False)

                # Handle /pause command
                elif text == '/pause':
                    try:
                        if not gBotPaused:
                            gBotPaused = True
                            gStopRequested = False
                            bot.send_message(
                                "⏸️ <b>Bot Paused</b>\n\nTrading is paused immediately. No new grids will be placed. Send /start or /resume to continue.",
                                chat_id=chat_id,
                                disable_notification=False,
                            )
                            if logger:
                                logger.info("Bot paused by user command")
                        else:
                            bot.send_message("⏸️ Bot is already paused.", chat_id=chat_id, disable_notification=False)
                    except Exception as e:
                        if logger:
                            logger.error(f"Error handling /pause: {e}")

                # Handle /panic command (requires confirmation)
                elif text.startswith('/panic'):
                    try:
                        if text.strip().lower() == '/panic confirm':
                            # Close and cancel immediately for strategy-owned items, then pause
                            if mt5_api:
                                try:
                                    close_all_positions(mt5_api, TRADE_SYMBOL, logger)
                                except Exception as e:
                                    if logger:
                                        logger.error(f"/panic close_all_positions error: {e}")
                                try:
                                    cancel_all_pending_orders(mt5_api, TRADE_SYMBOL, logger)
                                except Exception as e:
                                    if logger:
                                        logger.error(f"/panic cancel_all_pending_orders error: {e}")
                            gBotPaused = True
                            gStopRequested = False
                            # Optional: clear in-memory state
                            try:
                                gDetailOrders.clear()
                            except Exception:
                                pass
                            try:
                                gNotifiedFilled.clear()
                            except Exception:
                                pass
                            bot.send_message(
                                "🛑 <b>PANIC STOP executed</b>\n\nAll strategy positions closed, pending orders cancelled, and bot paused. Send /start or /resume to continue.",
                                chat_id=chat_id,
                                disable_notification=False,
                            )
                            if logger:
                                logger.warning("PANIC STOP executed: closed positions, cancelled orders, paused bot")
                        else:
                            bot.send_message(
                                "⚠️ This will close all strategy positions and cancel all strategy orders immediately.\n\n"
                                "If you are sure, send:\n<b>/panic confirm</b>",
                                chat_id=chat_id,
                                disable_notification=False,
                            )
                    except Exception as e:
                        if logger:
                            logger.error(f"Error handling /panic: {e}")

                # Handle /resume command (alias of /start)
                elif text == '/resume':
                    try:
                        # Get account number
                        account_number = "N/A"
                        if mt5_api:
                            try:
                                acc_info = mt5_api.account_info()
                                if acc_info and hasattr(acc_info, 'login'):
                                    account_number = acc_info.login
                            except Exception as e:
                                if logger:
                                    logger.debug(f"Could not get account info: {e}")
                        if gBotPaused:
                            gBotPaused = False
                            gStopRequested = False
                            resume_msg = (
                                "▶️ <b>Bot Resumed!</b>\n\n"
                                f"• Account: {account_number}\n"
                                f"• Symbol: {TRADE_SYMBOL}\n"
                                f"• Trade Amount: {TRADE_AMOUNT}\n"
                                "• Status: Running ✅\n\n"
                                "The bot will now resume trading operations."
                            )
                            bot.send_message(resume_msg, chat_id=chat_id, disable_notification=False)
                            if logger:
                                logger.info("Bot resumed by /resume")
                        else:
                            bot.send_message("▶️ Bot is already running.", chat_id=chat_id, disable_notification=False)
                    except Exception as e:
                        if logger:
                            logger.error(f"Error handling /resume: {e}")

                # Handle /drawdown command
                elif text == '/drawdown':
                    try:
                        bot.send_message(drawdown_report(), chat_id=chat_id, disable_notification=False)
                    except Exception as e:
                        if logger:
                            logger.error(f"Error handling /drawdown: {e}")

                # Handle /stopat HH:MM (GMT+7) or /stopat off
                elif text.startswith('/stopat'):
                    try:
                        parts = text.split()
                        if len(parts) == 2 and parts[1].lower() == 'off':
                            gStopAtDateTime = None
                            bot.send_message("🕒 Scheduled pause cleared.", chat_id=chat_id, disable_notification=False)
                        elif len(parts) == 2 and ':' in parts[1]:
                            hh, mm = parts[1].split(':', 1)
                            hh_i, mm_i = int(hh), int(mm)
                            if not (0 <= hh_i <= 23 and 0 <= mm_i <= 59):
                                raise ValueError('Invalid time')
                            tz = timezone(timedelta(hours=7))
                            now7 = datetime.now(tz)
                            sched = now7.replace(hour=hh_i, minute=mm_i, second=0, microsecond=0)
                            if sched <= now7:
                                sched += timedelta(days=1)
                            gStopAtDateTime = sched
                            bot.send_message(
                                f"🕒 Will pause at {sched.strftime('%Y-%m-%d %H:%M')} GMT+7.",
                                chat_id=chat_id,
                                disable_notification=False,
                            )
                        else:
                            bot.send_message("Usage: /stopat HH:MM or /stopat off", chat_id=chat_id, disable_notification=False)
                    except Exception as e:
                        if logger:
                            logger.error(f"Error handling /stopat: {e}")
                        bot.send_message("❌ Failed to schedule pause.", chat_id=chat_id, disable_notification=False)

                # Handle risk caps and thresholds
                elif text.startswith('/setmaxdd'):
                    try:
                        parts = text.split()
                        if len(parts) == 2:
                            gMaxDDThreshold = float(parts[1])
                            bot.send_message(f"🛡️ Max drawdown set to {gMaxDDThreshold}", chat_id=chat_id, disable_notification=False)
                        else:
                            bot.send_message("Usage: /setmaxdd X", chat_id=chat_id, disable_notification=False)
                    except Exception as e:
                        if logger:
                            logger.error(f"Error handling /setmaxdd: {e}")
                        bot.send_message("❌ Failed to set max drawdown.", chat_id=chat_id, disable_notification=False)

                elif text.startswith('/setmaxpos'):
                    try:
                        parts = text.split()
                        if len(parts) == 2:
                            gMaxPositions = int(parts[1])
                            bot.send_message(f"🛡️ Max positions set to {gMaxPositions}", chat_id=chat_id, disable_notification=False)
                        else:
                            bot.send_message("Usage: /setmaxpos N", chat_id=chat_id, disable_notification=False)
                    except Exception as e:
                        if logger:
                            logger.error(f"Error handling /setmaxpos: {e}")
                        bot.send_message("❌ Failed to set max positions.", chat_id=chat_id, disable_notification=False)

                elif text.startswith('/setmaxorders'):
                    try:
                        parts = text.split()
                        if len(parts) == 2:
                            gMaxOrders = int(parts[1])
                            bot.send_message(f"🛡️ Max pending orders set to {gMaxOrders}", chat_id=chat_id, disable_notification=False)
                        else:
                            bot.send_message("Usage: /setmaxorders N", chat_id=chat_id, disable_notification=False)
                    except Exception as e:
                        if logger:
                            logger.error(f"Error handling /setmaxorders: {e}")
                        bot.send_message("❌ Failed to set max pending orders.", chat_id=chat_id, disable_notification=False)

                elif text.startswith('/setspread'):
                    try:
                        parts = text.split()
                        if len(parts) == 2:
                            gMaxSpread = float(parts[1])
                            bot.send_message(f"🛡️ Max spread set to {gMaxSpread}", chat_id=chat_id, disable_notification=False)
                        else:
                            bot.send_message("Usage: /setspread X", chat_id=chat_id, disable_notification=False)
                    except Exception as e:
                        if logger:
                            logger.error(f"Error handling /setspread: {e}")
                        bot.send_message("❌ Failed to set max spread.", chat_id=chat_id, disable_notification=False)

                # Blackout window command
                elif text.startswith('/blackout'):
                    try:
                        parts = text.split()
                        if len(parts) == 1:
                            state = 'on' if gBlackoutEnabled else 'off'
                            bot.send_message(
                                f"⛔️ Blackout {state}. Window: {gBlackoutStart:02d}-{gBlackoutEnd:02d} GMT+7",
                                chat_id=chat_id,
                                disable_notification=False,
                            )
                        elif len(parts) == 2 and parts[1].lower() == 'off':
                            gBlackoutEnabled = False
                            bot.send_message("⛔️ Blackout disabled.", chat_id=chat_id, disable_notification=False)
                        elif len(parts) == 2 and '-' in parts[1]:
                            start_s, end_s = parts[1].split('-', 1)
                            start, end = int(start_s), int(end_s)
                            if not (0 <= start <= 23 and 0 <= end <= 23):
                                raise ValueError('Hours must be 0-23')
                            gBlackoutStart, gBlackoutEnd = start, end
                            gBlackoutEnabled = True
                            bot.send_message(
                                f"⛔️ Blackout set: {start:02d}-{end:02d} GMT+7 (enabled)",
                                chat_id=chat_id,
                                disable_notification=False,
                            )
                        else:
                            bot.send_message("Usage: /blackout HH-HH or /blackout off", chat_id=chat_id, disable_notification=False)
                    except Exception as e:
                        if logger:
                            logger.error(f"Error handling /blackout: {e}")
                        bot.send_message("❌ Failed to set blackout.", chat_id=chat_id, disable_notification=False)

                # History: /history N (last N deals)
                elif text.startswith('/history'):
                    try:
                        parts = text.split()
                        n = int(parts[1]) if len(parts) == 2 else 10
                        tz = timezone(timedelta(hours=7))
                        now = datetime.now(tz)
                        start = now - timedelta(days=30)
                        deals = mt5_api.history_deals_get(start, now) if mt5_api else []
                        items = []
                        for d in deals or []:
                            try:
                                if getattr(d, 'symbol', '') != TRADE_SYMBOL:
                                    continue
                                if getattr(d, 'magic', None) != 234002:
                                    continue
                                t = getattr(d, 'time', None)
                                ts = datetime.fromtimestamp(t, tz).strftime('%Y-%m-%d %H:%M') if isinstance(t, (int, float)) else str(t)
                                price = getattr(d, 'price', 0.0)
                                profit = getattr(d, 'profit', 0.0)
                                volume = getattr(d, 'volume', 0.0)
                                dtype = getattr(d, 'type', None)
                                side = 'BUY' if dtype == mt5_api.DEAL_TYPE_BUY else ('SELL' if dtype == mt5_api.DEAL_TYPE_SELL else str(dtype))
                                items.append((getattr(d, 'ticket', 0), ts, side, volume, price, profit))
                            except Exception:
                                continue
                        items = list(reversed(sorted(items, key=lambda x: x[0])))
                        items = items[:n]
                        if not items:
                            bot.send_message("ℹ️ No recent strategy deals found.", chat_id=chat_id, disable_notification=False)
                        else:
                            lines = [
                                f"#{tid} {ts} {side} {vol} @ {price:.2f} → PnL {pnl:+.2f}"
                                for (tid, ts, side, vol, price, pnl) in items
                            ]
                            bot.send_message("\n".join(lines), chat_id=chat_id, disable_notification=False)
                    except Exception as e:
                        if logger:
                            logger.error(f"Error handling /history: {e}")
                        bot.send_message("❌ Failed to fetch history.", chat_id=chat_id, disable_notification=False)

                # PnL aggregation: /pnl today|week|month
                elif text.startswith('/pnl'):
                    try:
                        parts = text.split()
                        scope = parts[1].lower() if len(parts) == 2 else 'today'
                        tz = timezone(timedelta(hours=7))
                        now = datetime.now(tz)
                        if scope == 'today':
                            start = now.replace(hour=0, minute=0, second=0, microsecond=0)
                        elif scope == 'week':
                            start = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
                        elif scope == 'month':
                            start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
                        else:
                            bot.send_message("Usage: /pnl today|week|month", chat_id=chat_id, disable_notification=False)
                            start = None
                        if start is not None:
                            deals = mt5_api.history_deals_get(start, now) if mt5_api else []
                            total = 0.0
                            count = 0
                            for d in deals or []:
                                if getattr(d, 'symbol', '') != TRADE_SYMBOL:
                                    continue
                                if getattr(d, 'magic', None) != 234002:
                                    continue
                                total += float(getattr(d, 'profit', 0.0))
                                count += 1
                            bot.send_message(f"📈 PnL {scope}: {total:+.2f} ({count} deals)", chat_id=chat_id, disable_notification=False)
                    except Exception as e:
                        if logger:
                            logger.error(f"Error handling /pnl: {e}")
                        bot.send_message("❌ Failed to compute PnL.", chat_id=chat_id, disable_notification=False)

                # Show filled orders summary
                elif text.strip().lower() == '/filled':
                    try:
                        bot.send_message(get_filled_orders_summary(logger), chat_id=chat_id, disable_notification=False)
                    except Exception as e:
                        if logger:
                            logger.error(f"Error handling /filled: {e}")
                        bot.send_message("❌ Failed to show filled orders.", chat_id=chat_id, disable_notification=False)

                # Show consecutive pattern detection
                elif text.strip().lower() == '/pattern':
                    try:
                        pd = check_consecutive_orders_pattern(logger)
                        msg = (
                            "🧩 <b>Consecutive Pattern</b>\n"
                            f"Detected: {'Yes' if pd.get('pattern_detected') else 'No'}\n"
                            f"Consecutive BUY pairs: {len(pd.get('consecutive_buys', []))}\n"
                            f"Consecutive SELL pairs: {len(pd.get('consecutive_sells', []))}\n"
                            f"Total filled: {pd.get('total_filled', 0)}\n"
                        )
                        bot.send_message(msg, chat_id=chat_id, disable_notification=False)
                    except Exception as e:
                        if logger:
                            logger.error(f"Error handling /pattern: {e}")
                        bot.send_message("❌ Failed to compute pattern.", chat_id=chat_id, disable_notification=False)
            
                # Clear the update so we don't process it again
                bot.bot.get_updates(offset=update.update_id + 1, timeout=0)
                
    except Exception as e:
        if logger:
            logger.debug(f"Error handling Telegram command: {e}")


###############################################################################################################
def main():
    global gDetailOrders, gCurrentIdx
    global gStartBalance
    global gNotifiedFilled
    global gTpExpected
    global gMaxDrawdown
    global gBotPaused
    global gStopRequested
    global gNextTradeAmount
    global gSessionStartTime
    
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s'
    )
    logger = logging.getLogger(__name__)
    logger.info("=== New Grid DCA Strategy for XAUUSD ===")
    script_start_time = datetime.now()
    gSessionStartTime = script_start_time
    try:
        credentials = config.get_mt5_credentials()
        symbol = TRADE_SYMBOL
        trade_amount = TRADE_AMOUNT
        gTpExpected = trade_amount * 1000 # Adjusted TP expected based on trade amount
        mt5 = MT5Connection(
            login=credentials['login'],
            password=credentials['password'],
            server=credentials['server'],
            path=credentials['path'],
        )
        if not mt5.connect():
            logger.error("❌ Failed to connect to MT5")
            return
        logger.info(f"✅ Connected to Exness MT5 Account (Symbol: {symbol}, Trade Amount: {trade_amount})")
        telegramBot.send_message(f"✅ Connected to Exness MT5 Account (Symbol: {symbol}, Trade Amount: {trade_amount})", chat_id=TELEGRAM_CHAT_ID)
        
        # return

        # Get start balance
        start_balance = get_current_balance(mt5.mt5, logger=logger)
        gStartBalance = start_balance
            
        # Step 1: Close all existing positions and pending orders for the symbol
        run_at_index(mt5.mt5, symbol, trade_amount, index=gCurrentIdx, price=0, logger=logger)
        
        gNotifiedFilled = set()
        notified_tp = set()
        closed_pnl = 0
        # Step 6: Monitor and notify if order filled or TP filled
        try:
            idx = 0
            while True:
                # Handle Telegram commands
                if telegramBot:
                    handle_telegram_command(telegramBot, mt5_api=mt5.mt5, logger=logger)
                
                # Enforce scheduled pause (/stopat)
                try:
                    if 'gStopAtDateTime' in globals() and gStopAtDateTime is not None:
                        now7 = datetime.now(timezone(timedelta(hours=7)))
                        if now7 >= gStopAtDateTime:
                            gBotPaused = True
                            gStopAtDateTime = None
                            msg = "🕒 Scheduled time reached. Bot paused."
                            logger.info(msg)
                            telegramBot.send_message(msg, chat_id=TELEGRAM_CHAT_ID)
                except Exception as e:
                    logger.debug(f"Scheduled pause check error: {e}")

                # Enforce max drawdown auto-pause if configured
                try:
                    if 'gMaxDDThreshold' in globals() and gMaxDDThreshold is not None and gStartBalance:
                        eq = get_current_equity(mt5.mt5, logger=logger)
                        dd = max(0.0, gStartBalance - eq)
                        if dd >= float(gMaxDDThreshold):
                            if not gBotPaused:
                                gBotPaused = True
                                warn = (
                                    f"🛑 Max drawdown reached: {dd:.2f} ≥ {gMaxDDThreshold:.2f}. Bot paused.\n"
                                    f"{drawdown_report()}"
                                )
                                logger.warning(warn)
                                telegramBot.send_message(warn, chat_id=TELEGRAM_CHAT_ID, disable_notification=False)
                except Exception as e:
                    logger.debug(f"Drawdown threshold check error: {e}")
                
                # Check if bot is paused
                if gBotPaused:
                    if idx % 100 == 0:  # Log every 100 iterations
                        logger.info("Bot is paused. Waiting for /start command...")
                    time.sleep(1)
                    idx += 1
                    continue
                
                # update list open order IDs
                saved_orders = []
                for key, val in gDetailOrders.items():
                    if val.get('status') == 'placed' and val.get('order') is not None:
                        saved_orders.append(val['order'].order)
                
                idx += 1
                positions = mt5.get_positions()
                open_pnl = 0
                # Calculate open P&L for all open positions matching saved order IDs
                for pos in positions:
                    if pos.get('ticket') in saved_orders:
                        open_pnl += pos.get('profit', 0)
                        
                # Check closed positions for TP filled
                history = []
                now = datetime.now()
                history = mt5.mt5.history_deals_get(script_start_time, now)
                
                # check if Pending order filled
                for oid in saved_orders:
                    if oid not in gNotifiedFilled:
                        if check_pending_order_filled(history, oid, logger):
                            # Determine side from comment
                            order_comment = None
                            order_price = 0
                            for key, val in gDetailOrders.items():
                                order_obj = val.get('order')
                                if hasattr(order_obj, 'order') and order_obj.order == oid:
                                    logger.info(f"DEBUG :: Checking order_obj {order_obj} for oid {oid}")
                                    order_comment = getattr(order_obj, 'comment', None)
                                    order_price = order_obj.request.price
                                    break
                            if order_comment:
                                side = 'BUY' if 'buy' in order_comment else 'SELL'
                            else:
                                side = '?'
                            logger.info(f"🔥 :: {order_comment} :: Pending order filled: ID {oid} | {side} | {order_price}")
                            gNotifiedFilled.add(oid)
                            logger.info(f"Filled order IDs: {gNotifiedFilled}")
                            
                            all_status_report = get_all_order_status_str(logger=logger)
                            msg = f"🔥 <b>Pending order filled - {order_comment}</b>\n"
                            msg += f"ID {oid} | {side} | {order_price:<.2f}\n"
                            msg += f"\n"
                            msg += f"{all_status_report}"
                            msg += f"\n{drawdown_report()}\n"
                            
                            telegramBot.send_message(msg, chat_id=TELEGRAM_CHAT_ID)
                            run_at_index(mt5.mt5, symbol, trade_amount, gCurrentIdx, price=order_price, logger=logger)
                            monitor_drawdown(mt5.mt5, logger=logger)
                        
                # check if Position closed (TP filled)
                for oid in gNotifiedFilled:
                    if oid not in notified_tp:
                        if check_position_closed(mt5.mt5, oid, logger):
                            pnl = pos_closed_pnl(mt5.mt5, oid, logger)
                            closed_pnl += pnl
                            notified_tp.add(oid)
                            hit_index = None
                            hit_side = None
                            hit_tp_price = None
                            order_comment = None
                            for key, val in gDetailOrders.items():
                                order_obj = val.get('order')
                                if order_obj and hasattr(order_obj, 'order') and order_obj.order == oid:
                                    hit_tp_price = order_obj.request.tp
                                    comment = getattr(order_obj, 'comment', '')
                                    order_comment = comment
                                    if 'buy' in comment:
                                        hit_side = 'BUY'
                                    elif 'sell' in comment:
                                        hit_side = 'SELL'
                                    try:
                                        idx_str = comment.split('_')[-1]
                                        hit_index = int(idx_str)
                                    except Exception:
                                        hit_index = None
                                    break
                            if hit_index is not None:
                                if hit_side == 'BUY': gCurrentIdx = hit_index + 1
                                elif hit_side == 'SELL': gCurrentIdx = hit_index - 1
                            
                            logger.info(f"❤️ :: {order_comment} :: TP filled: Position ID {oid} closed | P&L: ${pnl:.2f} All Closed P&L: ${closed_pnl:.2f}")
                            logger.info(f"TP filled order IDs: {notified_tp}")
                            logger.info(f"TP filled: {hit_side} order index {gCurrentIdx} (ID {oid}) closed. TP price: {hit_tp_price}")
                            msg = f"❤️❤️❤️ <b>TP filled - {order_comment}</b>\n\n"
                            msg += f"<b>Position ID:</b> {oid}\n"
                            msg += f"<b>P&L:</b> ${pnl:.2f}\n"
                            msg += f"<b>All Closed P&L:</b> ${closed_pnl:.2f}\n"
                            msg += f"<b>All P&L:</b> ${closed_pnl + open_pnl:.2f}\n"
                            msg += f"\n{drawdown_report()}\n"
                            
                            telegramBot.send_message(msg, chat_id=TELEGRAM_CHAT_ID)
                            run_at_index(mt5.mt5, symbol, trade_amount, gCurrentIdx, price=0, logger=logger)
                            monitor_drawdown(mt5.mt5, logger=logger)
                            # delete gDetailOrders
                            logger.info(f"⚠️ :: Deleting gDetailOrders entry for {hit_side.lower()}_{hit_index}")
                            gDetailOrders[f"{hit_side.lower()}_{hit_index}"] = {'status': None}
                
                if idx % 50 == 0:
                    logger.info(f"Current open positions P&L: ${open_pnl:.2f}")
                    logger.info(f"Closed positions (TP filled) P&L: ${closed_pnl:.2f}")
                    logger.info(f"All P&L: ${closed_pnl + open_pnl:.2f}")
                    logger.info(f"gCurrentIdx: {gCurrentIdx}")
                

                if closed_pnl + open_pnl > gTpExpected:
                    # Get current balance
                    close_all_positions(mt5.mt5, symbol, logger)
                    cancel_all_pending_orders(mt5.mt5, symbol, logger)
                    
                    current_balance = get_current_balance(mt5.mt5, logger=logger)
                    
                    # Calculate total pnl and run time
                    total_pnl = current_balance - start_balance
                    run_time = datetime.now() - script_start_time
                    run_time_str = str(run_time).split('.')[0]  # Remove microseconds

                    msg = (
                        f"✅✅✅✅✅ Target profit reached.\n"
                        f"Start balance: {start_balance}\n"
                        f"Current balance: {current_balance}\n"
                        f"Total PnL: {total_pnl}\n"
                        f"Session PnL: {closed_pnl + open_pnl}\n"
                        f"Run time: {run_time_str}"
                    )

                    logger.info(msg)
                    telegramBot.send_message(msg, chat_id=TELEGRAM_CHAT_ID, pin_msg=True, disable_notification=False)

                    # Check if any open positions or open orders remain
                    positions_left = mt5.get_positions()
                    open_orders_left = mt5.mt5.orders_get(symbol=symbol)
                    if positions_left:
                        logger.warning(f"⚠️ Open positions remain after TP: {positions_left}")
                        telegramBot.send_message(f"⚠️ Open positions remain after TP: {positions_left}", chat_id=TELEGRAM_CHAT_ID)
                        close_all_positions(mt5.mt5, symbol, logger)
                    if open_orders_left:
                        logger.warning(f"⚠️ Open orders remain after TP: {open_orders_left}")
                        telegramBot.send_message(f"⚠️ Open orders remain after TP: {open_orders_left}", chat_id=TELEGRAM_CHAT_ID)

                    gDetailOrders = {key: {'status': None} for key in gDetailOrders.keys()}
                    gNotifiedFilled.clear()
                    notified_tp.clear()
                    gCurrentIdx = 0
                    closed_pnl = 0
                    gMaxDrawdown = 0
                    
                    # Check if stop was requested
                    if gStopRequested:
                        gBotPaused = True
                        gStopRequested = False
                        pause_msg = f"⏸️ <b>Bot Paused</b>\n\n"
                        pause_msg += f"Target profit reached and bot is now paused.\n\n"
                        pause_msg += f"• All positions closed\n"
                        pause_msg += f"• All orders cancelled\n"
                        pause_msg += f"• Waiting for /start command to resume\n\n"
                        pause_msg += f"Send /start to resume trading."
                        
                        telegramBot.send_message(pause_msg, chat_id=TELEGRAM_CHAT_ID, pin_msg=True, disable_notification=False)
                        logger.info("Bot paused after reaching target profit (stop requested)")
                        continue
                    
                    # Apply new trade amount if set via /setamount command
                    if gNextTradeAmount is not None:
                        old_amount = TRADE_AMOUNT
                        trade_amount = gNextTradeAmount
                        gTpExpected = trade_amount * 1000
                        change_msg = f"💰 <b>Trade Amount Changed</b>\n\n"
                        change_msg += f"• Previous amount: {old_amount}\n"
                        change_msg += f"• New amount (override): {trade_amount}\n"
                        change_msg += f"• New TP expected: ${gTpExpected:.2f}\n\n"
                        change_msg += (
                            "The override is now active and will remain in effect for future runs until changed."
                        )
                    
                        telegramBot.send_message(change_msg, chat_id=TELEGRAM_CHAT_ID, disable_notification=False)
                        logger.info(f"Trade amount changed from {old_amount} to {trade_amount}")
                    else:
                        # Update trade amount based on time (GMT+7 timezone)
                        gmt_plus_7 = timezone(timedelta(hours=7))
                        current_time_gmt7 = datetime.now(gmt_plus_7)
                        current_hour = current_time_gmt7.hour
                    
                        in_quiet = (
                            gQuietHoursEnabled and
                            (
                                (gQuietHoursStart <= gQuietHoursEnd and gQuietHoursStart <= current_hour <= gQuietHoursEnd) or
                                (gQuietHoursStart > gQuietHoursEnd and (current_hour >= gQuietHoursStart or current_hour <= gQuietHoursEnd))
                            )
                        )
                        if in_quiet:
                            trade_amount = round(TRADE_AMOUNT * gQuietHoursFactor, 2)
                            gTpExpected = trade_amount * 1000 # Adjusted TP expected based on trade amount
                            logger.info(f"🕰️ Quiet-hours adjustment: trade amount {trade_amount} (factor x{gQuietHoursFactor}) (GMT+7: {current_hour}:00)")
                            telegramBot.send_message(f"🕰️ Quiet-hours adjustment: trade amount {trade_amount} (x{gQuietHoursFactor}) during {gQuietHoursStart:02d}-{gQuietHoursEnd:02d} GMT+7", chat_id=TELEGRAM_CHAT_ID)
                        else:
                            trade_amount = TRADE_AMOUNT
                            logger.info(f"🕰️ Normal trade amount: {trade_amount} (GMT+7: {current_hour}:00)")

                    script_start_time  = datetime.now()
                    gSessionStartTime = script_start_time
                    start_balance = get_current_balance(mt5.mt5, logger=logger)
                    gStartBalance = start_balance
                    run_at_index(mt5.mt5, symbol, trade_amount, gCurrentIdx, price=0, logger=logger)
                    
                time.sleep(0.5)
        except KeyboardInterrupt:
            logger.info("Interrupted by user. Disconnecting...")
        mt5.disconnect()
    except Exception as e:
        logger.error(f"Error: {e}")

if __name__ == "__main__":
    main()
