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
CONFIG_FILE = f"config/mt5_config_159684431.json"


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
def run_at_index(mt5_api, symbol, amount, index, price=0, logger=None):
    global gDetailOrders
    global gStartBalance
    global gNotifiedFilled

    try:
        current_balance = get_current_balance(mt5_api, logger=logger)
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
            res_buy_1 = place_pending_order(mt5_api, symbol, mt5_api.ORDER_TYPE_BUY_STOP, buy_entry_1, buy_tp_1, fibb_amount_1, buy_comment_1, logger)
            if res_buy_1:
                gDetailOrders[buy_comment_1] = {'status': 'placed', 'order': res_buy_1}
                new_orders.append(res_buy_1)
        if gDetailOrders.get(sell_comment_1, {}).get('status') != 'placed':
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
        order_ids = set()
        for key, val in gDetailOrders.items():
            if val.get('status') == 'placed' and val.get('order') is not None:
                order_obj = val['order']
                oid = getattr(order_obj, 'order', None)
                if oid is not None:
                    order_ids.add(oid)
        for pos in positions:
            ticket = getattr(pos, 'ticket', None)
            volume = getattr(pos, 'volume', None)
            type_ = getattr(pos, 'type', None)
            if ticket is None or volume is None or type_ is None:
                if logger:
                    logger.warning(f"Could not get ticket/volume/type for position: {pos}")
                continue
            # Only close positions matching gDetailOrders
            # if ticket not in order_ids:
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
                    "comment": f"close_all_positions (mode {fill_mode})",
                    "type_time": mt5_api.ORDER_TIME_GTC,
                    "type_filling": fill_mode,
                }
                result = mt5_api.order_send(request)
                if result is None:
                    if logger:
                        logger.error(f"Failed to close position {ticket} (mode {fill_mode}): {mt5_api.last_error()}")
                elif result.retcode == mt5_api.TRADE_RETCODE_DONE:
                    if logger:
                        logger.info(f"✅ Closed position {ticket} for {symbol}, volume {volume} (mode {fill_mode})")
                    success = True
                    break
                else:
                    if logger:
                        logger.error(f"Failed to close position {ticket} (mode {fill_mode}): retcode {result.retcode}, comment: {result.comment}")
            if not success and logger:
                logger.error(f"❌ Could not close position {ticket} for {symbol} with any supported filling mode.")
    except Exception as e:
        if logger:
            logger.error(f"Error closing all positions: {e}")


def cancel_all_pending_orders(mt5_api, symbol, logger=None):
    try:
        orders = mt5_api.orders_get(symbol=symbol)
        if not orders:
            if logger:
                logger.info(f"No pending orders to cancel for {symbol}.")
            return
        # Collect all order IDs from gDetailOrders with status 'placed'
        order_ids = set()
        for key, val in gDetailOrders.items():
            if val.get('status') == 'placed' and val.get('order') is not None:
                order_obj = val['order']
                oid = getattr(order_obj, 'order', None)
                if oid is not None:
                    order_ids.add(oid)
        for order in orders:
            ticket = getattr(order, 'ticket', None)
            if ticket is None:
                if logger:
                    logger.warning(f"Could not get ticket for order: {order}")
                continue
            # Only cancel pending orders matching gDetailOrders
            # if ticket not in order_ids:
            #     continue
            
            request = {
                "action": mt5_api.TRADE_ACTION_REMOVE,
                "order": ticket,
                "symbol": symbol,
                "magic": 234002,
                "comment": "cancel_all_pending_orders",
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
                    logger.info(f"✅ Cancelled pending order {ticket} for {symbol}")
                    # telegramBot.send_message(f"✅ Cancelled pending order {ticket} for {symbol}", chat_id=TELEGRAM_CHAT_ID)
    except Exception as e:
        if logger:
            logger.error(f"Error cancelling all pending orders: {e}")


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
def main():
    global gDetailOrders, gCurrentIdx
    global gStartBalance
    global gNotifiedFilled
    global gTpExpected
    
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s'
    )
    logger = logging.getLogger(__name__)
    logger.info("=== New Grid DCA Strategy for XAUUSD ===")
    script_start_time = datetime.now()
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

                    gDetailOrders = {key: {'status': None} for key in gDetailOrders.keys()}
                    gNotifiedFilled.clear()
                    notified_tp.clear()
                    gCurrentIdx = 0
                    closed_pnl = 0
                    logger.info(msg)
                    telegramBot.send_message(msg, chat_id=TELEGRAM_CHAT_ID, pin_msg=True, disable_notification=False)

                    # Update trade amount based on time (GMT+7 timezone)
                    gmt_plus_7 = timezone(timedelta(hours=7))
                    current_time_gmt7 = datetime.now(gmt_plus_7)
                    current_hour = current_time_gmt7.hour
                    
                    if 19 <= current_hour <= 23:  # 7 PM to 11 PM GMT+7
                        trade_amount = round(TRADE_AMOUNT / 2, 2)
                        gTpExpected = trade_amount * 1000 # Adjusted TP expected based on trade amount
                        logger.info(f"🕰️ Time-based adjustment: Reduced trade amount to {trade_amount} (GMT+7: {current_hour}:00)")
                        telegramBot.send_message(f"🕰️ Time-based adjustment: Reduced trade amount to {trade_amount} during high-risk hours ({current_hour}:00 GMT+7)", chat_id=TELEGRAM_CHAT_ID)
                    else:
                        trade_amount = TRADE_AMOUNT
                        logger.info(f"🕰️ Normal trade amount: {trade_amount} (GMT+7: {current_hour}:00)")

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

                    script_start_time  = datetime.now()
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
