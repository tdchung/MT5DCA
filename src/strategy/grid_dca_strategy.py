"""
Grid DCA Strategy - Reusable Strategy Module
Consolidates common logic used across all main_xxx.py instances.
"""

import logging
import time
from datetime import datetime, timedelta, timezone


class GridDCAStrategy:
    """
    Grid DCA Strategy with:
    - Fibonacci-scaled position sizing
    - Multi-layer buy/sell stop grids
    - Take-profit targets
    - Drawdown monitoring
    - Telegram control and notifications
    - Consecutive order pattern detection
    - Risk management guards (spread, blackout, capacity)
    """
    
    # Strategy Constants
    DEFAULT_MAGIC_NUMBER = 234002
    TP_MULTIPLIER = 1000  # trade_amount * TP_MULTIPLIER = tp_expected
    CONNECTION_CHECK_INTERVAL = 100  # Check every 100 iterations (~20 seconds)
    PAUSED_LOG_INTERVAL = 1000  # Log every 1000 iterations when paused
    STATUS_LOG_INTERVAL = 50  # Log status every 50 iterations
    CACHE_TTL_SECONDS = 1.0  # MT5 API cache Time-To-Live
    
    def __init__(self, config, mt5_connection, telegram_bot=None, logger=None):
        """
        Initialize strategy with configuration and connections.
        
        Args:
            config: ConfigManager instance with trading and telegram settings
            mt5_connection: MT5Connection instance
            telegram_bot: TelegramBot instance (optional)
            logger: logging.Logger instance (optional)
        """
        self.config = config
        self.mt5 = mt5_connection
        self.mt5_api = mt5_connection.mt5
        self.telegram_bot = telegram_bot
        self.logger = logger or logging.getLogger(__name__)
        
        # Load configuration
        trading_config = config.config.get('trading', {})
        self.fibonacci_levels = trading_config.get('fibonacci_levels', [1, 1, 2, 2, 3, 3, 5, 5, 8, 8, 13, 13, 13, 13, 13])
        self.trade_symbol = trading_config.get('trade_symbol', "XAUUSDc")
        self.delta_enter_price = trading_config.get('delta_enter_price', 0.8)
        self.target_profit = trading_config.get('target_profit', 2.0)
        self.trade_amount = trading_config.get('trade_amount', 0.1)
        self.percent_scale = trading_config.get('percent_scale', 12)
        # Dynamic risk management: max_reduce_balance = trade_amount * 10 * 2000
        self.max_reduce_balance = self.trade_amount * 10 * 2000
        self.min_free_margin = trading_config.get('min_free_margin', 100)
        
        telegram_config = config.config.get('telegram', {})
        self.telegram_chat_id = telegram_config.get('chat_id')
        
        # Strategy state
        self.tp_expected = 0
        self.detail_orders = {}
        self.current_idx = 0
        self.start_balance = 0
        self.max_drawdown = 0
        self.notified_filled = set()
        self.notified_tp = set()  # Track TP filled orders
        
        # Control flags
        self.bot_paused = True  # Start paused, require manual /start
        self.stop_requested = False
        self.user_started = False  # Track if user has started the strategy
        self.next_trade_amount = None
        
        # Quiet hours config
        self.quiet_hours_enabled = True
        self.quiet_hours_start = 19
        self.quiet_hours_end = 23
        self.quiet_hours_factor = 0.5
        
        # Session tracking
        self.session_start_time = None
        
        # Risk management
        self.max_dd_threshold = None
        self.max_positions = None
        self.max_orders = None
        self.max_spread = None
        self.max_total_exposure = None  # Maximum total lot size across all positions
        
        # Profit withdrawal management
        self.profit_withdrawal_threshold = None  # Dollar amount to trigger withdrawal pause
        self.profit_withdrawal_paused = False   # Track if paused for withdrawal
        self.total_session_profit = 0           # Track accumulated profit across cycles
        self.withdrawal_start_balance = 0       # Balance when withdrawal process started
        
        # Blackout window
        self.blackout_enabled = False
        self.blackout_start = 0
        self.blackout_end = 0
        self.blackout_paused = False  # Track if paused due to blackout
        self.blackout_pause_notified = False  # Prevent spam notifications
        
        # Trading halt (news/volatility protection)
        self.trading_halt_enabled = True  # Enabled by default for safety
        self.trading_halt_start = 4   # 4:30 AM
        self.trading_halt_start_minutes = 30  # Additional 30 minutes for 4:30
        self.trading_halt_end = 6     # 6:15 AM
        self.trading_halt_end_minutes = 15  # Additional 15 minutes for 6:15
        self.trading_halt_active = False  # Track if currently in halt period
        self.trading_halt_notified = False  # Prevent spam notifications
        
        # Scheduled pause
        self.stop_at_datetime = None
        
        # Magic number for strategy identification
        self.magic_number = trading_config.get('magic_number', self.DEFAULT_MAGIC_NUMBER)
        
        # Telegram update tracking (to avoid processing same command multiple times)
        self.last_telegram_update_id = None
        
        # MT5 API call caching (Performance optimization)
        self._account_info_cache = None
        self._account_info_cache_time = None
        self._account_info_cache_ttl = self.CACHE_TTL_SECONDS  # Cache for 1 second
        
        # Connection health tracking
        self.connection_check_interval = self.CONNECTION_CHECK_INTERVAL  # Check every 100 iterations (~20 seconds)
        self.connection_lost_count = 0
        self.max_connection_retries = 3
        self.last_connection_check = 0
        
        # Performance metrics tracking
        self.api_call_count = 0
        self.cache_hit_count = 0
        self.strategy_start_time = None
        self.total_iterations = 0
        
        # Trading metrics tracking
        self.orders_placed = 0
        self.orders_filled = 0
        self.tps_reached = 0
        self.errors = 0
    
    def check_pending_order_filled(self, history, order_id):
        """Check if a pending order has been filled by looking in history."""
        for record in history:
            if record.position_id == order_id and record.order == order_id:
                return True
        return False
    
    def check_position_closed(self, order_id):
        """Check if a position has been closed."""
        try:
            res = self.mt5_api.positions_get(ticket=order_id)
            if res is None or (hasattr(res, '__len__') and len(res) == 0):
                return True
        except Exception as e:
            self.logger.error(f"ERROR :: check_position_closed :: {e}")
        return False
    
    def pos_closed_pnl(self, position_id):
        """Get PnL from a closed position."""
        pnl = 0
        try:
            self.logger.debug(f"Getting PnL for position {position_id}")
            res = self.mt5_api.history_deals_get(position=position_id)
            if not res or len(res) == 0:
                self.logger.warning(f"No history found for position {position_id}")
                return 0
            info = res[-1]
            self.logger.debug(f"Position {position_id} PnL detail: {info}")
            pnl += info.profit
        except Exception as e:
            self.logger.error(f"ERROR :: pos_closed_pnl :: {e}")
        return pnl
    
    def get_cached_account_info(self):
        """
        Get cached account info with TTL (Time-To-Live).
        Reduces MT5 API calls by ~80% for balance/equity/margin queries.
        """
        now = time.time()
        if (self._account_info_cache is None or 
            self._account_info_cache_time is None or
            now - self._account_info_cache_time > self._account_info_cache_ttl):
            try:
                self._account_info_cache = self.mt5_api.account_info()
                self._account_info_cache_time = now
                self.api_call_count += 1  # Track actual API calls
                if self._account_info_cache is None:
                    error_code = self.mt5_api.last_error()
                    self.logger.warning(f"Failed to get account info: MT5 error {error_code}")
            except Exception as e:
                self.logger.error(f"Error getting account info: {e}")
                # Keep old cache if available, otherwise None
        else:
            self.cache_hit_count += 1  # Track cache hits
        return self._account_info_cache
    
    def get_current_balance(self):
        """Get current account balance (cached)."""
        acc_info = self.get_cached_account_info()
        if acc_info and hasattr(acc_info, 'balance'):
            return acc_info.balance
        return 0
    
    def get_current_equity(self):
        """Get current account equity (cached)."""
        acc_info = self.get_cached_account_info()
        if acc_info and hasattr(acc_info, 'equity'):
            return acc_info.equity
        return 0
    
    def get_current_free_margin(self):
        """Get current free margin (cached)."""
        acc_info = self.get_cached_account_info()
        if acc_info and hasattr(acc_info, 'margin_free'):
            return acc_info.margin_free
        return 0
    
    def check_mt5_connection(self):
        """
        Verify MT5 connection is still active.
        Returns True if connection is healthy, False otherwise.
        """
        try:
            acc_info = self.mt5_api.account_info()
            if acc_info is None:
                error_code = self.mt5_api.last_error()
                self.logger.warning(f"MT5 connection check failed: error code {error_code}")
                return False
            return True
        except Exception as e:
            self.logger.error(f"MT5 connection check exception: {e}")
            return False
    
    def attempt_mt5_reconnection(self):
        """
        Attempt to reconnect to MT5.
        Returns True if reconnection successful, False otherwise.
        """
        try:
            self.logger.info("Attempting MT5 reconnection...")
            
            # Disconnect first
            if hasattr(self.mt5, 'disconnect'):
                self.mt5.disconnect()
            
            # Reconnect
            if hasattr(self.mt5, 'connect'):
                if self.mt5.connect():
                    # Clear cache to force fresh data
                    self._account_info_cache = None
                    self._account_info_cache_time = None
                    self.connection_lost_count = 0
                    self.logger.info("✅ MT5 reconnection successful")
                    return True
                else:
                    self.logger.error("❌ MT5 reconnection failed")
                    return False
            else:
                self.logger.error("MT5 connection object missing 'connect' method")
                return False
        except Exception as e:
            self.logger.error(f"Error during MT5 reconnection: {e}")
            return False
    
    def get_performance_metrics(self):
        """
        Calculate and return comprehensive performance metrics.
        Includes both system metrics and trading metrics.
        """
        metrics = {
            # System metrics
            'api_calls': self.api_call_count,
            'cache_hits': self.cache_hit_count,
            'total_requests': self.api_call_count + self.cache_hit_count,
            'cache_hit_rate': 0.0,
            'connection_failures': self.connection_lost_count,
            'total_iterations': self.total_iterations,
            'uptime_seconds': 0,
            'avg_iteration_time': 0.0,
            # Trading metrics
            'orders_placed': self.orders_placed,
            'orders_filled': self.orders_filled,
            'tps_reached': self.tps_reached,
            'errors': self.errors,
            'fill_rate': 0.0,
            'tp_rate': 0.0
        }
        
        if metrics['total_requests'] > 0:
            metrics['cache_hit_rate'] = (metrics['cache_hits'] / metrics['total_requests']) * 100
        
        # Calculate trading rates
        if metrics['orders_placed'] > 0:
            metrics['fill_rate'] = (metrics['orders_filled'] / metrics['orders_placed']) * 100
        if metrics['orders_filled'] > 0:
            metrics['tp_rate'] = (metrics['tps_reached'] / metrics['orders_filled']) * 100
        
        if self.strategy_start_time:
            metrics['uptime_seconds'] = time.time() - self.strategy_start_time
            if self.total_iterations > 0:
                metrics['avg_iteration_time'] = metrics['uptime_seconds'] / self.total_iterations
        
        return metrics
    
    def _track_metric(self, metric_name, value=1):
        """
        Track a performance metric.
        
        Args:
            metric_name: Name of the metric to track (e.g., 'orders_placed', 'errors')
            value: Value to add (default: 1)
        """
        try:
            if hasattr(self, metric_name):
                current = getattr(self, metric_name, 0)
                if isinstance(current, (int, float)):
                    setattr(self, metric_name, current + value)
        except Exception as e:
            self.logger.debug(f"Error tracking metric {metric_name}: {e}")
    
    def get_gmt7_time(self):
        """Get current time in GMT+7 timezone."""
        return datetime.now(timezone(timedelta(hours=7)))
    
    def is_quiet_hours(self):
        """Check if current time is within quiet hours (reduced risk period)."""
        if not self.quiet_hours_enabled:
            return False
        
        current_time_gmt7 = self.get_gmt7_time()
        current_hour = current_time_gmt7.hour
        
        # Handle wrap-around (e.g., 23:00 to 02:00)
        if self.quiet_hours_start <= self.quiet_hours_end:
            return self.quiet_hours_start <= current_hour <= self.quiet_hours_end
        else:
            return current_hour >= self.quiet_hours_start or current_hour <= self.quiet_hours_end
    
    def get_adjusted_trade_amount(self):
        """Get trade amount adjusted for quiet hours and overrides."""
        # Check for permanent override first
        if self.next_trade_amount is not None:
            base_amount = self.next_trade_amount
        else:
            base_amount = self.trade_amount
        
        # Apply quiet hours reduction if active
        if self.is_quiet_hours():
            adjusted_amount = base_amount * self.quiet_hours_factor
            self.logger.info(f"🕰️ Quiet hours active: Trade amount reduced to {adjusted_amount} (factor: {self.quiet_hours_factor})")
            return adjusted_amount
        
        return base_amount
    
    def place_pending_order(self, symbol, order_type, price, tp_price, volume=0.01, comment=""):
        """Place a pending order (buy stop or sell stop)."""
        existing_orders = self.mt5_api.orders_get(symbol=symbol)
        for o in existing_orders or []:
            if abs(o.price_open - price) < 1e-4 and o.type == order_type:
                self.logger.info(f"⏩ Skipping duplicate order at {price:.2f} for {symbol}")
                return None
        
        request = {
            "action": self.mt5_api.TRADE_ACTION_PENDING,
            "symbol": symbol,
            "volume": volume,
            "type": order_type,
            "price": price,
            "tp": tp_price,
            "deviation": 20,
            "magic": self.magic_number,
            "comment": comment,
            "type_time": self.mt5_api.ORDER_TIME_GTC,
            "type_filling": self.mt5_api.ORDER_FILLING_RETURN,
        }
        result = self.mt5_api.order_send(request)
        if result is None:
            error_code = self.mt5_api.last_error()
            self.logger.error(f"Order send failed, error: {error_code}")
            self._track_metric('errors')  # Track order placement errors
            return None
        if result.retcode != self.mt5_api.TRADE_RETCODE_DONE:
            if self.telegram_bot:
                self.telegram_bot.send_message(
                    f"⭕️ :: {comment} :: Order failed, retcode: {result.retcode}, comment: {result.comment}",
                    chat_id=self.telegram_chat_id,
                )
            return None
        order_type_str = "BUY STOP" if order_type == self.mt5_api.ORDER_TYPE_BUY_STOP else "SELL STOP"
        self.logger.info(f"✅ :: {comment} :: {order_type_str} order placed: {volume} lots at {price:.2f}, TP: {tp_price:.2f}")
        self._track_metric('orders_placed')  # Track successful order placement
        return result
    
    def get_order_status_str(self, key, val):
        """Format a single order status string."""
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
            
            if order_id is not None and order_id in self.notified_filled:
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
            self.logger.error(f"ERROR in get_order_status_str: {e}")
        return msg
    
    def get_all_order_status_str(self):
        """Get formatted status string for all orders."""
        all_status_report = ''
        try:
            def order_sort_key(x):
                side, idx = x.split('_')
                idx = int(idx)
                return (0, idx)
            
            sorted_keys = sorted(self.detail_orders.keys(), key=order_sort_key)
            all_order_status_lines = []
            for key in sorted_keys:
                val = self.detail_orders.get(key, {})
                if val and val.get('order') is not None:
                    all_order_status_lines.append(self.get_order_status_str(key, val))
            all_status_report = '\n'.join(all_order_status_lines)
        except Exception as e:
            self.logger.error(f"Error in get_all_order_status_str: {e}")
        return all_status_report
    
    def get_filled_orders_list(self):
        """Get list of filled orders with details."""
        filled_orders = []
        try:
            for key, val in self.detail_orders.items():
                if val and val.get('order') is not None:
                    order_obj = val['order']
                    order_id = getattr(order_obj, 'order', None)
                    if order_id and order_id in self.notified_filled:
                        order_comment = getattr(order_obj, 'comment', key)
                        order_price = getattr(order_obj.request, 'price', None)
                        order_volume = getattr(order_obj.request, 'volume', None)
                        side = 'BUY' if 'buy' in key.lower() else 'SELL'
                        try:
                            index = int(key.split('_')[-1])
                        except Exception:
                            index = None
                        filled_order_info = {
                            'key': key,
                            'comment': order_comment,
                            'order_id': order_id,
                            'side': side,
                            'index': index,
                            'price': round(order_price, 3) if order_price else None,
                            'volume': round(order_volume, 2) if order_volume else None,
                        }
                        filled_orders.append(filled_order_info)
            filled_orders.sort(key=lambda x: (x['side'], x['index'] if x['index'] is not None else 0))
            self.logger.info(f"Found {len(filled_orders)} filled orders")
        except Exception as e:
            self.logger.error(f"Error getting filled orders list: {e}")
        return filled_orders
    
    def get_filled_orders_summary(self):
        """Get formatted summary of filled orders."""
        filled_orders = self.get_filled_orders_list()
        if not filled_orders:
            return "No filled orders found."
        summary_lines = []
        summary_lines.append(f"📋 <b>Filled Orders Summary ({len(filled_orders)} orders)</b>\n")
        buy_orders = [o for o in filled_orders if o['side'] == 'BUY']
        sell_orders = [o for o in filled_orders if o['side'] == 'SELL']
        if buy_orders:
            summary_lines.append("🟢 <b>BUY Orders Filled:</b>")
            for o in buy_orders:
                summary_lines.append(f"  • {o['comment']} | Price: {o['price']} | Vol: {o['volume']}")
            summary_lines.append("")
        if sell_orders:
            summary_lines.append("🔴 <b>SELL Orders Filled:</b>")
            for o in sell_orders:
                summary_lines.append(f"  • {o['comment']} | Price: {o['price']} | Vol: {o['volume']}")
        return '\n'.join(summary_lines)
    
    def check_consecutive_orders_pattern(self):
        """Detect consecutive filled-order patterns."""
        filled_orders = self.get_filled_orders_list()
        if len(filled_orders) < 2:
            return {"consecutive_buys": [], "consecutive_sells": [], "pattern_detected": False, "total_filled": 0}
        buy_orders = sorted([o for o in filled_orders if o['side'] == 'BUY'], key=lambda x: x['index'] if x['index'] is not None else 0)
        sell_orders = sorted([o for o in filled_orders if o['side'] == 'SELL'], key=lambda x: x['index'] if x['index'] is not None else 0)
        consecutive_buys = []
        consecutive_sells = []
        for i in range(len(buy_orders) - 1):
            if (buy_orders[i]['index'] is not None and buy_orders[i+1]['index'] is not None and buy_orders[i+1]['index'] == buy_orders[i]['index'] + 1):
                consecutive_buys.append((buy_orders[i], buy_orders[i+1]))
        for i in range(len(sell_orders) - 1):
            # SELL orders go downward (0, -1, -2), so when sorted they are consecutive if next = current + 1
            if (sell_orders[i]['index'] is not None and sell_orders[i+1]['index'] is not None and sell_orders[i+1]['index'] == sell_orders[i]['index'] + 1):
                consecutive_sells.append((sell_orders[i], sell_orders[i+1]))
        pattern_detected = len(consecutive_buys) > 0 or len(consecutive_sells) > 0
        if pattern_detected:
            self.logger.info(f"Consecutive patterns detected - Buys: {len(consecutive_buys)}, Sells: {len(consecutive_sells)}")
        return {
            "consecutive_buys": consecutive_buys,
            "consecutive_sells": consecutive_sells,
            "pattern_detected": pattern_detected,
            "total_filled": len(filled_orders),
        }
    
    def monitor_drawdown(self):
        """Monitor and update max drawdown."""
        try:
            current_equity = self.get_current_equity()
            if current_equity < self.start_balance:
                self.max_drawdown = max(self.max_drawdown, self.start_balance - current_equity)
                self.logger.info(f"New max drawdown recorded: {self.max_drawdown}")
        except Exception as e:
            self.logger.error(f"Error monitoring drawdown: {e}")
    
    def drawdown_report(self):
        """Generate drawdown report string."""
        msg = ''
        try:
            msg = f"📉 <b>Drawdown Report</b>\n\n"
            msg += f"Start Balance: {self.start_balance:.2f}\n"
            msg += f"Max Drawdown: {self.max_drawdown:.2f}\n"
            msg += f"Percentage Drawdown: {(self.max_drawdown / self.start_balance * 100):.2f}%\n"
        except Exception as e:
            self.logger.error(f"Error generating drawdown report: {e}")
        return msg
    
    def get_gmt7_time(self):
        """Get current time in GMT+7 timezone."""
        gmt_plus_7 = timezone(timedelta(hours=7))
        return datetime.now(gmt_plus_7)
    
    def calculate_total_exposure(self, symbol):
        """Calculate total lot size of all open positions."""
        total = 0.0
        try:
            positions = self.mt5_api.positions_get(symbol=symbol)
            for p in positions or []:
                if getattr(p, 'magic', None) == self.magic_number:
                    total += float(getattr(p, 'volume', 0.0))
        except Exception as e:
            self.logger.debug(f"Error calculating exposure: {e}")
        return total
    
    def _safe_mt5_call(self, func, *args, default=None, error_msg="MT5 call failed", **kwargs):
        """
        Wrapper for safe MT5 API calls with consistent error handling.
        
        Args:
            func: MT5 API function to call
            *args: Positional arguments for the function
            default: Default value to return on error
            error_msg: Custom error message prefix
            **kwargs: Keyword arguments for the function
        
        Returns:
            Function result or default value on error
        """
        try:
            result = func(*args, **kwargs)
            if result is None:
                error_code = self.mt5_api.last_error()
                self.logger.warning(f"{error_msg}: MT5 error {error_code}")
            return result
        except Exception as e:
            self.logger.error(f"{error_msg}: {e}", exc_info=True)
            return default
    
    def run_at_index(self, symbol, amount, index, price=0):
        """
        Main grid placement logic for given index.
        Places 3 layers of buy stop and 3 layers of sell stop orders.
        """
        try:
            # PRE-ORDER EQUITY VALIDATION (Critical for risk management)
            current_equity = self.get_current_equity()
            
            # Max reduce balance check (pre-order validation)
            if current_equity < self.start_balance - self.max_reduce_balance:
                self.logger.error(
                    f"⛔️ PRE-ORDER CHECK: Current equity ${current_equity:.2f} has reduced more than "
                    f"${self.max_reduce_balance:.2f} from start balance ${self.start_balance:.2f}. "
                    f"Blocking order placement."
                )
                if self.telegram_bot:
                    self.telegram_bot.send_message(
                        f"⛔️ PRE-ORDER CHECK: Current equity ${current_equity:.2f} has reduced more than "
                        f"${self.max_reduce_balance:.2f} from start balance ${self.start_balance:.2f}. "
                        f"Blocking order placement.",
                        chat_id=self.telegram_chat_id
                    )
                return
            
            # Free margin check
            current_free_margin = self.get_current_free_margin()
            if current_free_margin < self.min_free_margin:
                self.logger.error(f"⛔️ Current free margin {current_free_margin} is below minimum required {self.min_free_margin}. Stopping further trades.")
                if self.telegram_bot:
                    self.telegram_bot.send_message(f"⛔️ Current free margin {current_free_margin} is below minimum required {self.min_free_margin}. Stopping further trades.", chat_id=self.telegram_chat_id)
                return
            
            # Get current price from MT5
            tick = self.mt5_api.symbol_info_tick(symbol)
            if not tick:
                self.logger.error(f"Could not get tick for {symbol}")
                return
            
            # Spread cap
            try:
                spread = (tick.ask - tick.bid) if (hasattr(tick, 'ask') and hasattr(tick, 'bid')) else 0.0
            except Exception:
                spread = 0.0
            if self.max_spread is not None and spread > self.max_spread:
                self.logger.info(f"⛔️ Spread {spread:.3f} > max {self.max_spread:.3f}. Skipping grid build.")
                if self.telegram_bot:
                    self.telegram_bot.send_message(
                        f"⛔️ Spread {spread:.3f} > max {self.max_spread:.3f}. Skipping grid build.",
                        chat_id=self.telegram_chat_id,
                    )
                return
            
            if not price:
                price = (tick.bid + tick.ask) / 2
            self.logger.info(f"run_at_index: Current price for {symbol}: {price:.2f}")
            
            percent0 = abs(index) / 100 * self.percent_scale
            percent1 = abs(index + 1) / 100 * self.percent_scale
            percent2 = abs(index + 2) / 100 * self.percent_scale
            percent_1 = abs(index - 1) / 100 * self.percent_scale
            percent_2 = abs(index - 2) / 100 * self.percent_scale
            
            # Pattern-based exposure adjustment
            pypass_buy1 = False
            pypass_sell1 = False
            try:
                pattern_data = self.check_consecutive_orders_pattern()
                if pattern_data.get('pattern_detected'):
                    if len(pattern_data.get('consecutive_buys', [])) >= 2:
                        self.logger.warning("⚠️ Strong upward trend detected - consider reducing BUY exposure")
                        pypass_buy1 = True
                    if len(pattern_data.get('consecutive_sells', [])) >= 2:
                        self.logger.warning("⚠️ Strong downward trend detected - consider reducing SELL exposure")
                        pypass_sell1 = True
            except Exception as e:
                self.logger.debug(f"consecutive pattern check error: {e}")
            
            # Calculate buy stop entries and TP
            buy_entry_1 = price + self.delta_enter_price * (1 + percent0)
            buy_tp_1 = buy_entry_1 + self.target_profit * (1 + percent0)
            buy_entry_2 = price + self.target_profit * (1 + percent0) + self.delta_enter_price * (1 + percent1)
            buy_tp_2 = buy_entry_2 + self.target_profit * (1 + percent1)
            buy_entry_3 = price + self.target_profit * (1 + percent0) + self.target_profit * (1 + percent1) + self.delta_enter_price * (1 + percent2)
            buy_tp_3 = buy_entry_3 + self.target_profit * (1 + percent2)
            
            # Calculate sell stop entries and TP
            sell_entry_1 = price - self.delta_enter_price * (1 + percent0)
            sell_tp_1 = sell_entry_1 - self.target_profit * (1 + percent0)
            sell_entry_2 = price - self.target_profit * (1 + percent0) - self.delta_enter_price * (1 + percent_1)
            sell_tp_2 = sell_entry_2 - self.target_profit * (1 + percent_1)
            sell_entry_3 = price - self.target_profit * (1 + percent0) - self.target_profit * (1 + percent_1) - self.delta_enter_price * (1 + percent_2)
            sell_tp_3 = sell_entry_3 - self.target_profit * (1 + percent_2)
            
            # Use trade amount scaled by FIBONACCI_LEVELS
            fibb_amount_1 = amount * self.fibonacci_levels[abs(index)]
            fibb_amount_2 = amount * self.fibonacci_levels[abs(index+1)] if abs(index+1) < len(self.fibonacci_levels) else amount
            fibb_amount_3 = amount * self.fibonacci_levels[abs(index+2)] if abs(index+2) < len(self.fibonacci_levels) else amount
            
            fibs_amount_1 = amount * self.fibonacci_levels[abs(index)]
            fibs_amount_2 = amount * self.fibonacci_levels[abs(index-1)] if abs(index-1) < len(self.fibonacci_levels) else amount
            fibs_amount_3 = amount * self.fibonacci_levels[abs(index-2)] if abs(index-2) < len(self.fibonacci_levels) else amount
            
            # Maximum exposure limit check
            if self.max_total_exposure is not None:
                current_exposure = self.calculate_total_exposure(symbol)
                new_order_size = fibb_amount_1 + fibb_amount_2 + fibb_amount_3 + fibs_amount_1 + fibs_amount_2 + fibs_amount_3
                
                if current_exposure + new_order_size > self.max_total_exposure:
                    self.logger.warning(
                        f"⛔️ Max exposure limit reached: {current_exposure:.2f} + {new_order_size:.2f} > {self.max_total_exposure:.2f}. "
                        f"Blocking order placement."
                    )
                    if self.telegram_bot:
                        self.telegram_bot.send_message(
                            f"⛔️ <b>Max Exposure Limit Reached</b>\n\n"
                            f"Current Exposure: <code>{current_exposure:.2f}</code> lots\n"
                            f"New Orders: <code>{new_order_size:.2f}</code> lots\n"
                            f"Total Would Be: <code>{current_exposure + new_order_size:.2f}</code> lots\n"
                            f"Max Allowed: <code>{self.max_total_exposure:.2f}</code> lots\n\n"
                            f"Order placement blocked.",
                            chat_id=self.telegram_chat_id
                        )
                    return
            
            # Capacity caps for positions/orders
            try:
                pos_count = 0
                for p in (self.mt5_api.positions_get(symbol=symbol) or []):
                    if getattr(p, 'magic', None) == self.magic_number:
                        pos_count += 1
                ord_count = 0
                for o in (self.mt5_api.orders_get(symbol=symbol) or []):
                    if getattr(o, 'magic', None) == self.magic_number:
                        ord_count += 1
                if (self.max_positions is not None and pos_count >= self.max_positions) or (
                    self.max_orders is not None and ord_count >= self.max_orders
                ):
                    self.logger.info(f"⛔️ Capacity cap reached (pos {pos_count}/{self.max_positions or '∞'}, orders {ord_count}/{self.max_orders or '∞'}). Skipping grid build.")
                    if self.telegram_bot:
                        self.telegram_bot.send_message(
                            f"⛔️ Capacity cap reached (pos {pos_count}/{self.max_positions or '∞'}, orders {ord_count}/{self.max_orders or '∞'}). Skipping grid build.",
                            chat_id=self.telegram_chat_id,
                        )
                    return
            except Exception as e:
                self.logger.debug(f"Capacity cap check error: {e}")
            
            # Place buy stop orders
            buy_comment_1 = f"buy_{index}"
            buy_comment_2 = f"buy_{index+1}"
            buy_comment_3 = f"buy_{index+2}"
            sell_comment_1 = f"sell_{index}"
            sell_comment_2 = f"sell_{index-1}"
            sell_comment_3 = f"sell_{index-2}"
            
            new_orders = []
            if self.detail_orders.get(buy_comment_1, {}).get('status') != 'placed':
                if not pypass_buy1:
                    res_buy_1 = self.place_pending_order(symbol, self.mt5_api.ORDER_TYPE_BUY_STOP, buy_entry_1, buy_tp_1, fibb_amount_1, buy_comment_1)
                    if res_buy_1:
                        self.detail_orders[buy_comment_1] = {'status': 'placed', 'order': res_buy_1}
                        new_orders.append(res_buy_1)
            if self.detail_orders.get(sell_comment_1, {}).get('status') != 'placed':
                if not pypass_sell1:
                    res_sell_1 = self.place_pending_order(symbol, self.mt5_api.ORDER_TYPE_SELL_STOP, sell_entry_1, sell_tp_1, fibs_amount_1, sell_comment_1)
                    if res_sell_1:
                        self.detail_orders[sell_comment_1] = {'status': 'placed', 'order': res_sell_1}
                        new_orders.append(res_sell_1)
            
            if self.detail_orders.get(buy_comment_2, {}).get('status') != 'placed':
                res_buy_2 = self.place_pending_order(symbol, self.mt5_api.ORDER_TYPE_BUY_STOP, buy_entry_2, buy_tp_2, fibb_amount_2, buy_comment_2)
                if res_buy_2:
                    self.detail_orders[buy_comment_2] = {'status': 'placed', 'order': res_buy_2}
                    new_orders.append(res_buy_2)
            if self.detail_orders.get(sell_comment_2, {}).get('status') != 'placed':
                res_sell_2 = self.place_pending_order(symbol, self.mt5_api.ORDER_TYPE_SELL_STOP, sell_entry_2, sell_tp_2, fibs_amount_2, sell_comment_2)
                if res_sell_2:
                    self.detail_orders[sell_comment_2] = {'status': 'placed', 'order': res_sell_2}
                    new_orders.append(res_sell_2)
            
            if self.detail_orders.get(buy_comment_3, {}).get('status') != 'placed':
                res_buy_3 = self.place_pending_order(symbol, self.mt5_api.ORDER_TYPE_BUY_STOP, buy_entry_3, buy_tp_3, fibb_amount_3, buy_comment_3)
                if res_buy_3:
                    self.detail_orders[buy_comment_3] = {'status': 'placed', 'order': res_buy_3}
                    new_orders.append(res_buy_3)
            if self.detail_orders.get(sell_comment_3, {}).get('status') != 'placed':
                res_sell_3 = self.place_pending_order(symbol, self.mt5_api.ORDER_TYPE_SELL_STOP, sell_entry_3, sell_tp_3, fibs_amount_3, sell_comment_3)
                if res_sell_3:
                    self.detail_orders[sell_comment_3] = {'status': 'placed', 'order': res_sell_3}
                    new_orders.append(res_sell_3)
            
            # Show all new orders
            if len(new_orders) > 0 and self.telegram_bot:
                self.telegram_bot.send_message(
                    f"<b>New Orders Placed:</b>\n\n" + '\n'.join([self.get_order_status_str(k, self.detail_orders[k]) for k in sorted(self.detail_orders.keys()) if self.detail_orders[k].get('order') in new_orders]),
                    chat_id=self.telegram_chat_id
                )
                self.logger.info(f"Grid orders placed for index {index}: buy/sell stops at {buy_entry_1:.2f}, {buy_entry_2:.2f}, {buy_entry_3:.2f}, {sell_entry_1:.2f}, {sell_entry_2:.2f}, {sell_entry_3:.2f}")
        except Exception as e:
            self.logger.error(f"ERROR :: {e}")
    
    def close_all_positions(self, symbol):
        """Close all strategy positions."""
        try:
            positions = self.mt5_api.positions_get(symbol=symbol)
            if not positions:
                self.logger.info(f"No open positions to close for {symbol}.")
                return
            
            strategy_order_ids = set()
            for key, val in self.detail_orders.items():
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
                
                if ticket is None or volume is None or type_ is None:
                    self.logger.warning(f"Could not get ticket/volume/type for position: {pos}")
                    continue
                
                if type_ == self.mt5_api.POSITION_TYPE_BUY:
                    close_type = self.mt5_api.ORDER_TYPE_SELL
                elif type_ == self.mt5_api.POSITION_TYPE_SELL:
                    close_type = self.mt5_api.ORDER_TYPE_BUY
                else:
                    self.logger.warning(f"Unknown position type for ticket {ticket}: {type_}")
                    continue
                
                filling_modes = [self.mt5_api.ORDER_FILLING_IOC, self.mt5_api.ORDER_FILLING_FOK, self.mt5_api.ORDER_FILLING_RETURN]
                success = False
                for fill_mode in filling_modes:
                    request = {
                        "action": self.mt5_api.TRADE_ACTION_DEAL,
                        "symbol": symbol,
                        "volume": volume,
                        "type": close_type,
                        "position": ticket,
                        "deviation": 20,
                        "magic": self.magic_number,
                        "comment": "close_all_positions",
                        "type_time": self.mt5_api.ORDER_TIME_GTC,
                        "type_filling": fill_mode,
                    }
                    result = self.mt5_api.order_send(request)
                    if result is None:
                        self.logger.error(f"Failed to close position {ticket} (mode {fill_mode}): {self.mt5_api.last_error()}")
                    elif result.retcode == self.mt5_api.TRADE_RETCODE_DONE:
                        self.logger.info(f"✅ Closed position {ticket} for {symbol}, volume {volume} (mode {fill_mode})")
                        positions_closed += 1
                        success = True
                        break
                    else:
                        self.logger.error(f"Failed to close position {ticket} (mode {fill_mode}): retcode {result.retcode}, comment: {result.comment}")
                if not success:
                    self.logger.error(f"❌ Could not close position {ticket} for {symbol} with any supported filling mode.")
            
            self.logger.info(f"Strategy positions closed: {positions_closed} out of {len(positions)} total positions for {symbol}")
        except Exception as e:
            self.logger.error(f"Error closing strategy positions: {e}")
    
    def cancel_all_pending_orders(self, symbol):
        """Cancel all strategy pending orders."""
        try:
            orders = self.mt5_api.orders_get(symbol=symbol)
            if not orders:
                self.logger.info(f"No pending orders to cancel for {symbol}.")
                return
            
            strategy_order_ids = set()
            for key, val in self.detail_orders.items():
                if val.get('status') == 'placed' and val.get('order') is not None:
                    order_obj = val['order']
                    oid = getattr(order_obj, 'order', None)
                    if oid is not None:
                        strategy_order_ids.add(oid)
            
            orders_cancelled = 0
            for order in orders:
                ticket = getattr(order, 'ticket', None)
                if ticket is None:
                    self.logger.warning(f"Could not get ticket for order: {order}")
                    continue
                
                request = {
                    "action": self.mt5_api.TRADE_ACTION_REMOVE,
                    "order": ticket,
                    "symbol": symbol,
                    "magic": self.magic_number,
                    "comment": "cancel_all_pending_orders",
                }
                result = self.mt5_api.order_send(request)
                if result is None:
                    self.logger.error(f"Failed to cancel pending order {ticket}: {self.mt5_api.last_error()}")
                elif result.retcode != self.mt5_api.TRADE_RETCODE_DONE:
                    self.logger.error(f"Failed to cancel pending order {ticket}: retcode {result.retcode}, comment: {result.comment}")
                else:
                    self.logger.info(f"✅ Cancelled strategy order {ticket} for {symbol}")
                    orders_cancelled += 1
            
            self.logger.info(f"Strategy orders cancelled: {orders_cancelled} out of {len(orders)} total orders for {symbol}")
        except Exception as e:
            self.logger.error(f"Error cancelling strategy pending orders: {e}")
    
    def run(self):
        """
        Main strategy execution loop.
        Monitors filled orders, TP reached, handles Telegram commands, and manages trade cycles.
        """
        self.logger.info(f"=== Grid DCA Strategy for {self.trade_symbol} ===")
        script_start_time = datetime.now()
        self.session_start_time = script_start_time
        
        try:
            symbol = self.trade_symbol
            trade_amount = self.trade_amount
            self.tp_expected = trade_amount * self.TP_MULTIPLIER
            
            # Initialize performance tracking
            self.strategy_start_time = time.time()
            
            self.logger.info(f"✅ Connected to MT5 Account (Symbol: {symbol}, Trade Amount: {trade_amount})")
            if self.telegram_bot:
                self.telegram_bot.send_message(
                    f"✅ Connected to MT5 Account (Symbol: {symbol}, Trade Amount: {trade_amount})",
                    chat_id=self.telegram_chat_id
                )
            
            # Get start balance
            start_balance = self.get_current_balance()
            self.start_balance = start_balance
            
            # Send initial status message (no automatic trading)
            if self.telegram_bot:
                initial_msg = (
                    f"🤖 <b>Grid DCA Strategy Initialized</b>\n\n"
                    f"┌─────────────────────────────┐\n"
                    f"│     ⏸️ <b>AWAITING START</b>     │\n"
                    f"└─────────────────────────────┘\n\n"
                    f"📊 <b>Configuration:</b>\n"
                    f"┣━ 📈 Symbol: <code>{symbol}</code>\n"
                    f"┣━ 💰 Trade Amount: <code>{trade_amount}</code>\n"
                    f"┣━ 💳 Account Balance: <code>${start_balance:.2f}</code>\n"
                    f"┗━ ⚙️ Magic Number: <code>{self.magic_number}</code>\n\n"
                    f"🚀 <b>Ready to trade - Send <code>/start</code> to begin!</b>\n\n"
                    f"⚠️ <i>No orders will be placed until you start the strategy</i>"
                )
                self.telegram_bot.send_message(initial_msg, chat_id=self.telegram_chat_id)
            
            closed_pnl = 0
            
            idx = 0
            while True:
                self.total_iterations = idx + 1
                
                # Handle Telegram commands
                if self.telegram_bot:
                    self.handle_telegram_command()
                
                # CONNECTION HEALTH CHECK (Periodic)
                if idx % self.connection_check_interval == 0 and idx > 0:
                    if not self.check_mt5_connection():
                        self.connection_lost_count += 1
                        self.logger.warning(
                            f"⚠️ MT5 connection check failed (count: {self.connection_lost_count}/{self.max_connection_retries})"
                        )
                        
                        if self.connection_lost_count >= self.max_connection_retries:
                            self.logger.critical(
                                f"🚨 MT5 connection lost after {self.connection_lost_count} attempts! "
                                f"Pausing bot and attempting reconnection..."
                            )
                            self.bot_paused = True
                            
                            if self.telegram_bot:
                                self.telegram_bot.send_message(
                                    f"🚨 <b>MT5 CONNECTION LOST</b>\n\n"
                                    f"Connection check failed {self.connection_lost_count} times.\n"
                                    f"Bot paused. Attempting reconnection...\n\n"
                                    f"Please check your MT5 terminal connection.",
                                    chat_id=self.telegram_chat_id,
                                    disable_notification=False
                                )
                            
                            # Attempt reconnection
                            if self.attempt_mt5_reconnection():
                                self.bot_paused = False
                                self.connection_lost_count = 0
                                if self.telegram_bot:
                                    self.telegram_bot.send_message(
                                        "✅ <b>MT5 Reconnection Successful</b>\n\nBot resuming normal operation.",
                                        chat_id=self.telegram_chat_id,
                                        disable_notification=False
                                    )
                            else:
                                if self.telegram_bot:
                                    self.telegram_bot.send_message(
                                        "❌ <b>MT5 Reconnection Failed</b>\n\n"
                                        "Bot will remain paused. Please check MT5 terminal manually.",
                                        chat_id=self.telegram_chat_id,
                                        disable_notification=False
                                    )
                        else:
                            # Try to reconnect on first failure
                            if self.connection_lost_count == 1:
                                if self.attempt_mt5_reconnection():
                                    self.connection_lost_count = 0
                                    self.logger.info("✅ MT5 reconnection successful on first attempt")
                    else:
                        # Connection is healthy, reset counter
                        if self.connection_lost_count > 0:
                            self.connection_lost_count = 0
                            self.logger.info("✅ MT5 connection restored")
                
                # Enforce scheduled pause
                try:
                    if self.stop_at_datetime is not None:
                        now7 = self.get_gmt7_time()
                        if now7 >= self.stop_at_datetime:
                            self.bot_paused = True
                            self.stop_at_datetime = None
                            msg = "🕒 Scheduled time reached. Bot paused."
                            self.logger.info(msg)
                            if self.telegram_bot:
                                self.telegram_bot.send_message(msg, chat_id=self.telegram_chat_id)
                except Exception as e:
                    self.logger.debug(f"Scheduled pause check error: {e}")
                
                # Enforce max drawdown auto-pause
                try:
                    if self.max_dd_threshold is not None and self.start_balance:
                        eq = self.get_current_equity()
                        dd = max(0.0, self.start_balance - eq)
                        if dd >= float(self.max_dd_threshold):
                            if not self.bot_paused:
                                self.bot_paused = True
                                warn = (
                                    f"🛑 Max drawdown reached: {dd:.2f} ≥ {self.max_dd_threshold:.2f}. Bot paused.\n"
                                    f"{self.drawdown_report()}"
                                )
                                self.logger.warning(warn)
                                if self.telegram_bot:
                                    self.telegram_bot.send_message(warn, chat_id=self.telegram_chat_id, disable_notification=False)
                except Exception as e:
                    self.logger.debug(f"Drawdown threshold check error: {e}")
                
                # Enforce blackout pause/resume
                try:
                    current_time_gmt7 = self.get_gmt7_time()
                    current_hour = current_time_gmt7.hour
                    in_blackout = (
                        self.blackout_enabled and (
                            (self.blackout_start <= self.blackout_end and self.blackout_start <= current_hour <= self.blackout_end) or
                            (self.blackout_start > self.blackout_end and (current_hour >= self.blackout_start or current_hour <= self.blackout_end))
                        )
                    )
                    
                    # Pause during blackout
                    if in_blackout and not self.blackout_paused and not self.bot_paused:
                        self.blackout_paused = True
                        self.bot_paused = True
                        self.blackout_pause_notified = False
                        msg = f"⛔️ Blackout window started ({self.blackout_start:02d}:00-{self.blackout_end:02d}:00 GMT+7). Bot paused automatically."
                        self.logger.info(msg)
                        if self.telegram_bot:
                            self.telegram_bot.send_message(msg, chat_id=self.telegram_chat_id)
                    
                    # Auto-resume after blackout
                    elif not in_blackout and self.blackout_paused:
                        self.blackout_paused = False
                        self.bot_paused = False
                        self.blackout_pause_notified = False
                        msg = f"⛔️ Blackout window ended. Bot auto-resuming trading operations."
                        self.logger.info(msg)
                        if self.telegram_bot:
                            self.telegram_bot.send_message(msg, chat_id=self.telegram_chat_id)
                        
                        # Immediately place grid at current index after resuming
                        try:
                            self.run_at_index(symbol, trade_amount, self.current_idx, price=0)
                        except Exception as e:
                            self.logger.error(f"Error placing grid after blackout resume: {e}")
                    
                except Exception as e:
                    self.logger.debug(f"Blackout check error: {e}")

                # Automatic Trading Halt Check (4:30AM-6:15AM GMT+7 News Protection)
                try:
                    if self.trading_halt_enabled:
                        current_time_gmt7 = self.get_gmt7_time()
                        current_hour = current_time_gmt7.hour
                        current_minute = current_time_gmt7.minute
                        
                        # Check if we're in trading halt period (4:30 AM to 6:15 AM)
                        in_halt_period = (
                            (current_hour == self.trading_halt_start and current_minute >= self.trading_halt_start_minutes) or  # 4:30-4:59 AM
                            (self.trading_halt_start < current_hour < self.trading_halt_end) or  # 5:00-5:59 AM  
                            (current_hour == self.trading_halt_end and current_minute < self.trading_halt_end_minutes)  # 6:00-6:14 AM
                        )
                        
                        # Update trading halt status and notify if changed
                        previous_halt_status = self.trading_halt_active
                        self.trading_halt_active = in_halt_period
                        
                        # Notify status change
                        if previous_halt_status != self.trading_halt_active:
                            if self.trading_halt_active:
                                halt_msg = (
                                    f"🛑 <b>Trading Halt ACTIVATED</b>\n\n"
                                    f"┌─────────────────────────────┐\n"
                                    f"│  📰 <b>News Protection Period</b>  │\n"
                                    f"└─────────────────────────────┘\n\n"
                                    f"🕐 <b>Halt Schedule:</b>\n"
                                    f"┣━ 🚫 No new orders: <code>04:30-06:15 GMT+7</code>\n"
                                    f"┣━ ⏰ Current time: <code>{current_hour:02d}:{current_minute:02d} GMT+7</code>\n"
                                    f"┗━ 🔄 Auto-resume at: <code>06:15 GMT+7</code>\n\n"
                                    f"✅ <b>Active positions and orders remain untouched</b>\n"
                                    f"🛡️ <b>Risk management continues normally</b>"
                                )
                                self.logger.info(f"🛑 Trading halt ACTIVATED at {current_hour:02d}:{current_minute:02d} GMT+7")
                            else:
                                halt_msg = (
                                    f"✅ <b>Trading Halt DEACTIVATED</b>\n\n"
                                    f"┌─────────────────────────────┐\n"
                                    f"│    🔄 <b>NORMAL TRADING</b>     │\n"
                                    f"└─────────────────────────────┘\n\n"
                                    f"📈 <b>Trading Resumed:</b>\n"
                                    f"┣━ ✅ New orders: <b>ALLOWED</b>\n"
                                    f"┣━ ⏰ Current time: <code>{current_hour:02d}:{current_minute:02d} GMT+7</code>\n"
                                    f"┗━ 🛡️ News protection: <b>Completed</b>\n\n"
                                    f"🚀 <b>Strategy is now active for normal trading!</b>"
                                )
                                self.logger.info(f"✅ Trading halt DEACTIVATED at {current_hour:02d}:{current_minute:02d} GMT+7")
                            
                            if self.telegram_bot:
                                self.telegram_bot.send_message(halt_msg, chat_id=self.telegram_chat_id, disable_notification=False)
                except Exception as e:
                    self.logger.debug(f"Trading halt time check error: {e}")
                
                # Check if bot is paused
                if self.bot_paused:
                    if idx % self.PAUSED_LOG_INTERVAL == 0:
                        if self.profit_withdrawal_paused:
                            pause_reason = "profit withdrawal"
                        elif self.blackout_paused:
                            pause_reason = "blackout"
                        elif not self.user_started:
                            pause_reason = "awaiting start command"
                            self.logger.info("Strategy is paused. Send /start command to begin trading...")
                        else:
                            pause_reason = "manual/scheduled pause"
                        
                        if self.user_started or not (not self.user_started):  # Show general message for other pause types
                            if pause_reason != "awaiting start command":
                                self.logger.info(f"Bot is paused ({pause_reason}). Waiting...")
                    time.sleep(1)
                    idx += 1
                    continue
                
                # Check various halt conditions before placing new orders
                skip_new_orders = False
                skip_reason = ""
                
                if self.trading_halt_active:
                    skip_new_orders = True
                    skip_reason = "trading halt (4:30-6:15 AM news protection)"
                elif self.blackout_paused:
                    skip_new_orders = True
                    skip_reason = "blackout window"
                elif self.quiet_hours_enabled and self.is_quiet_hours():
                    # Note: quiet hours reduces trade amount but doesn't skip orders
                    # We don't set skip_new_orders=True for quiet hours
                    pass
                
                # Skip order placement if any halt condition is active
                if skip_new_orders:
                    if idx % 100 == 0:  # Log every 100 iterations to avoid spam
                        self.logger.info(f"Skipping new orders due to {skip_reason}")
                    time.sleep(1)
                    idx += 1
                    continue
                
                # Update list of open order IDs
                saved_orders = []
                for key, val in self.detail_orders.items():
                    if val.get('status') == 'placed' and val.get('order') is not None:
                        saved_orders.append(val['order'].order)
                
                idx += 1
                self.total_iterations += 1  # Track total iterations
                positions = self.mt5.get_positions()
                open_pnl = 0
                for pos in positions:
                    if pos.get('ticket') in saved_orders:
                        open_pnl += pos.get('profit', 0)
                
                # Check history for filled orders
                history = []
                now = datetime.now()
                history = self.mt5_api.history_deals_get(script_start_time, now)
                
                # Check if pending orders filled
                for oid in saved_orders:
                    if oid not in self.notified_filled:
                        if self.check_pending_order_filled(history, oid):
                            order_comment = None
                            order_price = 0
                            for key, val in self.detail_orders.items():
                                order_obj = val.get('order')
                                if hasattr(order_obj, 'order') and order_obj.order == oid:
                                    self.logger.info(f"DEBUG :: Checking order_obj {order_obj} for oid {oid}")
                                    order_comment = getattr(order_obj, 'comment', None)
                                    order_price = order_obj.request.price
                                    break
                            if order_comment:
                                side = 'BUY' if 'buy' in order_comment else 'SELL'
                            else:
                                side = '?'
                            self.logger.info(f"🔥 :: {order_comment} :: Pending order filled: ID {oid} | {side} | {order_price}")
                            self.notified_filled.add(oid)
                            self._track_metric('orders_filled')  # Track order fill
                            self.logger.info(f"Filled order IDs: {self.notified_filled}")
                            
                            all_status_report = self.get_all_order_status_str()
                            
                            # Calculate cycle time
                            cycle_time_str = "-"
                            try:
                                if self.session_start_time:
                                    cycle_time = datetime.now() - self.session_start_time
                                    cycle_time_str = str(cycle_time).split('.')[0]
                            except Exception:
                                pass
                            
                            msg = f"🔥 <b>Pending order filled - {order_comment}</b>\n"
                            msg += f"ID {oid} | {side} | {order_price:<.2f}\n\n"
                            msg += f"⏱️ <b>Cycle Time:</b> {cycle_time_str}\n\n"
                            msg += f"{all_status_report}\n{self.drawdown_report()}\n"
                            
                            # Add pattern detection info
                            try:
                                pd = self.check_consecutive_orders_pattern()
                                if pd.get('pattern_detected'):
                                    msg += f"\n<b>⚠️ Pattern Detected</b>\n"
                                    cb = len(pd.get('consecutive_buys', []))
                                    cs = len(pd.get('consecutive_sells', []))
                                    if cb > 0:
                                        msg += f"• Consecutive BUY pairs: {cb}\n"
                                    if cs > 0:
                                        msg += f"• Consecutive SELL pairs: {cs}\n"
                                    msg += f"• Total filled: {pd.get('total_filled', 0)}\n"
                            except Exception as e_pattern:
                                self.logger.debug(f"Pattern check error: {e_pattern}")
                            
                            if self.telegram_bot:
                                self.telegram_bot.send_message(msg, chat_id=self.telegram_chat_id)
                            self.run_at_index(symbol, trade_amount, self.current_idx, price=order_price)
                            self.monitor_drawdown()
                
                # Check if positions closed (TP filled)
                for oid in self.notified_filled:
                    if oid not in self.notified_tp:
                        if self.check_position_closed(oid):
                            pnl = self.pos_closed_pnl(oid)
                            closed_pnl += pnl
                            self.notified_tp.add(oid)
                            self._track_metric('tps_reached')  # Track TP reached
                            hit_index = None
                            hit_side = None
                            hit_tp_price = None
                            order_comment = None
                            for key, val in self.detail_orders.items():
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
                                if hit_side == 'BUY':
                                    self.current_idx = hit_index + 1
                                elif hit_side == 'SELL':
                                    self.current_idx = hit_index - 1
                            
                            self.logger.info(f"❤️ :: {order_comment} :: TP filled: Position ID {oid} closed | P&L: ${pnl:.2f} All Closed P&L: ${closed_pnl:.2f}")
                            self.notified_tp.add(oid)
                            self._track_metric('tps_reached')  # Track TP reached
                            self.logger.info(f"TP filled order IDs: {self.notified_tp}")
                            self.logger.info(f"TP filled: {hit_side} order index {self.current_idx} (ID {oid}) closed. TP price: {hit_tp_price}")
                            
                            # Calculate cycle time
                            cycle_time_str = "-"
                            try:
                                if self.session_start_time:
                                    cycle_time = datetime.now() - self.session_start_time
                                    cycle_time_str = str(cycle_time).split('.')[0]
                            except Exception:
                                pass
                            
                            msg = f"❤️❤️❤️ <b>TP filled - {order_comment}</b>\n\n"
                            msg += f"<b>Position ID:</b> {oid}\n"
                            msg += f"<b>P&L:</b> ${pnl:.2f}\n"
                            msg += f"<b>All Closed P&L:</b> ${closed_pnl:.2f}\n"
                            msg += f"<b>All P&L:</b> ${closed_pnl + open_pnl:.2f}\n"
                            msg += f"⏱️ <b>Cycle Time:</b> {cycle_time_str}\n\n"
                            msg += f"{self.drawdown_report()}\n"
                            
                            if self.telegram_bot:
                                self.telegram_bot.send_message(msg, chat_id=self.telegram_chat_id)
                            self.run_at_index(symbol, trade_amount, self.current_idx, price=0)
                            self.monitor_drawdown()
                            self.logger.info(f"⚠️ :: Deleting detail_orders entry for {hit_side.lower()}_{hit_index}")
                            self.detail_orders[f"{hit_side.lower()}_{hit_index}"] = {'status': None}
                
                if idx % self.STATUS_LOG_INTERVAL == 0:
                    self.logger.info(f"Current open positions P&L: ${open_pnl:.2f}")
                    self.logger.info(f"Closed positions (TP filled) P&L: ${closed_pnl:.2f}")
                    self.logger.info(f"All P&L: ${closed_pnl + open_pnl:.2f}")
                    self.logger.info(f"current_idx: {self.current_idx}")
                
                # Check if target profit reached
                if closed_pnl + open_pnl > self.tp_expected:
                    self.close_all_positions(symbol)
                    self.cancel_all_pending_orders(symbol)
                    
                    current_balance = self.get_current_balance()
                    total_pnl = current_balance - start_balance
                    cycle_pnl = closed_pnl + open_pnl
                    self.total_session_profit += cycle_pnl
                    
                    run_time = datetime.now() - script_start_time
                    run_time_str = str(run_time).split('.')[0]
                    
                    # Check profit withdrawal threshold
                    if (self.profit_withdrawal_threshold is not None and 
                        self.total_session_profit >= self.profit_withdrawal_threshold and 
                        not self.profit_withdrawal_paused):
                        
                        self.profit_withdrawal_paused = True
                        self.bot_paused = True
                        self.withdrawal_start_balance = current_balance
                        
                        withdrawal_msg = (
                            f"💰💰💰 <b>PROFIT WITHDRAWAL THRESHOLD REACHED</b>\n\n"
                            f"🎯 <b>Threshold:</b> ${self.profit_withdrawal_threshold:.2f}\n"
                            f"💵 <b>Total Session Profit:</b> ${self.total_session_profit:.2f}\n"
                            f"📈 <b>Current Cycle P&L:</b> ${cycle_pnl:.2f}\n\n"
                            f"📊 <b>Account Details:</b>\n"
                            f"• Start Balance: ${start_balance:.2f}\n"
                            f"• Current Balance: ${current_balance:.2f}\n"
                            f"• Total Account P&L: ${total_pnl:.2f}\n\n"
                            f"⏱️ <b>Session Info:</b>\n"
                            f"• Total Runtime: {run_time_str}\n"
                            f"• Profit Rate: ${(self.total_session_profit / (run_time.total_seconds() / 3600)):.2f}/hour\n\n"
                            f"🔄 <b>Next Steps:</b>\n"
                            f"1. Move profits to another account\n"
                            f"2. Send /withdrawalcomplete to restart strategy\n\n"
                            f"⚠️ <b>Strategy is PAUSED until you confirm withdrawal completion.</b>"
                        )
                        
                        self.logger.warning(f"Profit withdrawal threshold reached: ${self.total_session_profit:.2f} >= ${self.profit_withdrawal_threshold:.2f}")
                        if self.telegram_bot:
                            self.telegram_bot.send_message(withdrawal_msg, chat_id=self.telegram_chat_id, pin_msg=True, disable_notification=False)
                        
                        continue
                    
                    msg = (
                        f"✅✅✅✅✅ Target profit reached.\n"
                        f"Start balance: {start_balance}\n"
                        f"Current balance: {current_balance}\n"
                        f"Total PnL: {total_pnl}\n"
                        f"Session PnL: {cycle_pnl}\n"
                        f"Total Session Profit: ${self.total_session_profit:.2f}\n"
                        f"Run time: {run_time_str}"
                    )
                    
                    self.logger.info(msg)
                    if self.telegram_bot:
                        self.telegram_bot.send_message(msg, chat_id=self.telegram_chat_id, pin_msg=True, disable_notification=False)
                    
                    # Reset state
                    self.detail_orders = {key: {'status': None} for key in self.detail_orders.keys()}
                    self.notified_filled.clear()
                    self.notified_tp.clear()
                    self.current_idx = 0
                    closed_pnl = 0
                    self.max_drawdown = 0
                    
                    # Check if stop was requested
                    if self.stop_requested:
                        self.bot_paused = True
                        self.stop_requested = False
                        pause_msg = f"⏸️ <b>Bot Paused</b>\n\n"
                        pause_msg += f"Target profit reached and bot is now paused.\n\n"
                        pause_msg += f"• All positions closed\n"
                        pause_msg += f"• All orders cancelled\n"
                        pause_msg += f"• Waiting for /start command to resume\n\n"
                        pause_msg += f"Send /start to resume trading."
                        if self.telegram_bot:
                            self.telegram_bot.send_message(pause_msg, chat_id=self.telegram_chat_id, pin_msg=True, disable_notification=False)
                        self.logger.info("Bot paused after reaching target profit (stop requested)")
                        continue
                    
                    # Apply override or quiet hours
                    if self.next_trade_amount is not None:
                        old_amount = self.trade_amount
                        trade_amount = self.next_trade_amount
                        self.tp_expected = trade_amount * self.TP_MULTIPLIER
                        change_msg = f"💰 <b>Trade Amount Changed</b>\n\n"
                        change_msg += f"• Previous amount: {old_amount}\n"
                        change_msg += f"• New amount (override): {trade_amount}\n"
                        change_msg += f"• New TP expected: ${self.tp_expected:.2f}\n\n"
                        change_msg += "The override is now active and will remain in effect for future runs until changed."
                        if self.telegram_bot:
                            self.telegram_bot.send_message(change_msg, chat_id=self.telegram_chat_id, disable_notification=False)
                        self.logger.info(f"Trade amount changed from {old_amount} to {trade_amount}")
                    else:
                        current_time_gmt7 = self.get_gmt7_time()
                        current_hour = current_time_gmt7.hour
                        in_quiet = (
                            self.quiet_hours_enabled and (
                                (self.quiet_hours_start <= self.quiet_hours_end and self.quiet_hours_start <= current_hour <= self.quiet_hours_end) or
                                (self.quiet_hours_start > self.quiet_hours_end and (current_hour >= self.quiet_hours_start or current_hour <= self.quiet_hours_end))
                            )
                        )
                        if in_quiet:
                            trade_amount = round(self.trade_amount * self.quiet_hours_factor, 2)
                            self.tp_expected = trade_amount * self.TP_MULTIPLIER
                            self.logger.info(f"🕰️ Quiet-hours adjustment: trade amount {trade_amount} (factor x{self.quiet_hours_factor}) (GMT+7: {current_hour}:00)")
                            if self.telegram_bot:
                                self.telegram_bot.send_message(f"🕰️ Quiet-hours adjustment: trade amount {trade_amount} (x{self.quiet_hours_factor}) during {self.quiet_hours_start:02d}-{self.quiet_hours_end:02d} GMT+7", chat_id=self.telegram_chat_id)
                        else:
                            trade_amount = self.trade_amount
                            self.tp_expected = trade_amount * self.TP_MULTIPLIER
                            self.logger.info(f"🕰️ Normal trade amount: {trade_amount} (GMT+7: {current_hour}:00)")
                    
                    # Check for remaining positions/orders
                    positions_left = self.mt5.get_positions()
                    open_orders_left = self.mt5_api.orders_get(symbol=symbol)
                    if positions_left:
                        self.logger.warning(f"⚠️ Open positions remain after TP: {positions_left}")
                        if self.telegram_bot:
                            self.telegram_bot.send_message(f"⚠️ Open positions remain after TP: {positions_left}", chat_id=self.telegram_chat_id)
                        self.close_all_positions(symbol)
                    if open_orders_left:
                        self.logger.warning(f"⚠️ Open orders remain after TP: {open_orders_left}")
                        if self.telegram_bot:
                            self.telegram_bot.send_message(f"⚠️ Open orders remain after TP: {open_orders_left}", chat_id=self.telegram_chat_id)
                    
                    script_start_time = datetime.now()
                    self.session_start_time = script_start_time
                    start_balance = self.get_current_balance()
                    self.start_balance = start_balance
                    self.run_at_index(symbol, trade_amount, self.current_idx, price=0)
                
                time.sleep(0.2)
        
        except KeyboardInterrupt:
            self.logger.info("Interrupted by user. Disconnecting...")
        except Exception as e:
            self.logger.error(f"Error in strategy run: {e}")
        
        self.mt5.disconnect()
    
    def handle_telegram_command(self):
        """
        Handle incoming Telegram commands and update strategy state.
        Supports all bot control, configuration, and insights commands.
        """
        if not self.telegram_bot:
            return

        try:
            # Get updates from Telegram with reduced timeout to prevent blocking
            offset = self.last_telegram_update_id + 1 if self.last_telegram_update_id else None
            updates = self.telegram_bot.bot.get_updates(timeout=0.5, offset=offset)  # Reduced from 1 to 0.5
            
            for update in updates:
                # Update the last processed update_id
                if update.update_id:
                    self.last_telegram_update_id = update.update_id
                
                if update.message and update.message.text:
                    chat_id = update.message.chat.id
                    text = update.message.text.strip()
                    
                    self.logger.info(f"Received Telegram command: {text} from chat_id: {chat_id}")
                    
                    # Handle /start command
                    if text == '/start':
                        account_number = "N/A"
                        try:
                            acc_info = self.mt5_api.account_info()
                            if acc_info and hasattr(acc_info, 'login'):
                                account_number = acc_info.login
                        except Exception as e:
                            self.logger.debug(f"Could not get account info: {e}")
                        
                        if self.bot_paused:
                            self.bot_paused = False
                            self.stop_requested = False
                            self.blackout_paused = False  # Reset blackout pause state
                            
                            # Check if this is the first start
                            if not self.user_started:
                                self.user_started = True
                                current_equity = self.get_current_equity()
                                welcome_msg = (
                                    f"🚀 <b>Grid DCA Strategy Started</b> 🚀\n\n"
                                    f"┌─────────────────────────────┐\n"
                                    f"│    🟢 <b>STRATEGY ACTIVATED</b>     │\n"
                                    f"└─────────────────────────────┘\n\n"
                                    f"📊 <b>Account Overview:</b>\n"
                                    f"┣━ 🟢 Status: <b>Active & Ready</b>\n"
                                    f"┣━ 💰 Current Equity: <code>${current_equity:.2f}</code>\n"
                                    f"┗━ ⚙️ Magic Number: <code>{self.magic_number}</code>\n\n"
                                    f"🎯 <b>Strategy Launch:</b>\n"
                                    f"┣━ 📈 Initial grid deployment in progress\n"
                                    f"┣━ 🛡️ Risk management systems active\n"
                                    f"┗━ 📱 Use <code>/status</code> to monitor progress\n\n"
                                    f"✅ <b>Ready for trading operations!</b>"
                                )
                                self.telegram_bot.send_message(welcome_msg, chat_id=chat_id, disable_notification=False)
                                
                                # Place initial grid only after /start command
                                try:
                                    self.run_at_index(self.trade_symbol, self.trade_amount, index=self.current_idx, price=0)
                                    self.logger.info(f"✅ Initial grid placed after /start command")
                                except Exception as grid_error:
                                    self.logger.error(f"Error placing initial grid: {grid_error}")
                                    error_msg = f"⚠️ <b>Grid Placement Error</b>\n\nFailed to place initial orders. Check logs for details.\n\nError: {str(grid_error)[:100]}"
                                    self.telegram_bot.send_message(error_msg, chat_id=chat_id, disable_notification=False)
                            else:
                                resume_msg = (
                                    f"▶️ <b>Bot Resumed!</b> ▶️\n\n"
                                    f"┌─────────────────────────────┐\n"
                                    f"│     🔄 <b>TRADING RESUMED</b>     │\n"
                                    f"└─────────────────────────────┘\n\n"
                                    f"📊 <b>Account Details:</b>\n"
                                    f"┣━ 🏦 Account: <code>{account_number}</code>\n"
                                    f"┣━ 📈 Symbol: <code>{self.trade_symbol}</code>\n"
                                    f"┣━ 💰 Trade Amount: <code>{self.trade_amount}</code>\n"
                                    f"┗━ 🟢 Status: <b>Running ✅</b>\n\n"
                                    f"🚀 <b>Trading operations are now active!</b>"
                                )
                                self.telegram_bot.send_message(resume_msg, chat_id=chat_id, disable_notification=False)
                            self.logger.info(f"Bot resumed by user command from chat_id: {chat_id}")
                        else:
                            welcome_msg = f"👋 <b>Hello!</b>\n\n"
                            welcome_msg += f"• Account: {account_number}\n\n"
                            welcome_msg += f"Welcome to the Grid DCA Trading Bot for {self.trade_symbol}!\n\n"
                            welcome_msg += f"<b>Bot Status:</b>\n"
                            welcome_msg += f"• Strategy: Grid DCA\n"
                            welcome_msg += f"• Symbol: {self.trade_symbol}\n"
                            welcome_msg += f"• Trade Amount: {self.trade_amount}\n"
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
                            self.telegram_bot.send_message(welcome_msg, chat_id=chat_id, disable_notification=False)
                            self.logger.info(f"Sent welcome message to chat_id: {chat_id}")
                    
                    # Handle /stop command
                    elif text == '/stop':
                        if not self.stop_requested:
                            self.stop_requested = True
                            stop_msg = (
                                f"⏸️ <b>Graceful Stop Requested</b>\n\n"
                                f"┌─────────────────────────────┐\n"
                                f"│    🎯 <b>CYCLE COMPLETION</b>    │\n"
                                f"└─────────────────────────────┘\n\n"
                                f"📋 <b>Stop Sequence:</b>\n"
                                f"┣━ 1️⃣ Continue until next Target Profit\n"
                                f"┣━ 2️⃣ Close all positions at TP\n"
                                f"┣━ 3️⃣ Auto-pause and await commands\n"
                                f"┗━ 🔄 Resume anytime with <code>/start</code>\n\n"
                                f"⏳ <b>Status:</b> Waiting for TP completion... 💤"
                            )
                            self.telegram_bot.send_message(stop_msg, chat_id=chat_id, disable_notification=False)
                            self.logger.info(f"Stop requested by user from chat_id: {chat_id}")
                        else:
                            already_stopped_msg = f"⏸️ <b>Stop already requested</b>\n\nStrategy will pause after next TP completion."
                            self.telegram_bot.send_message(already_stopped_msg, chat_id=chat_id, disable_notification=False)
                    
                    # Handle /setamount command
                    elif text.startswith('/setamount'):
                        try:
                            parts = text.split()
                            if len(parts) == 2:
                                new_amount = float(parts[1])
                                if new_amount > 0:
                                    self.next_trade_amount = new_amount
                                    amount_msg = f"💰 <b>Trade Amount Updated</b>\n\n"
                                    amount_msg += f"• Configured amount: {self.trade_amount}\n"
                                    amount_msg += f"• Override amount (persistent): {self.next_trade_amount}\n\n"
                                    amount_msg += (
                                        "The override will be applied after the next target profit is reached "
                                        "and will persist for all subsequent runs until you change it again."
                                    )
                                    self.telegram_bot.send_message(amount_msg, chat_id=chat_id, disable_notification=False)
                                    self.logger.info(f"Trade amount set to {self.next_trade_amount} for next run")
                                else:
                                    error_msg = f"❌ Invalid amount. Please provide a positive number.\nExample: /setamount 0.05"
                                    self.telegram_bot.send_message(error_msg, chat_id=chat_id, disable_notification=False)
                            else:
                                error_msg = f"❌ Invalid format.\nUsage: /setamount X.XX\nExample: /setamount 0.05"
                                self.telegram_bot.send_message(error_msg, chat_id=chat_id, disable_notification=False)
                        except ValueError:
                            error_msg = f"❌ Invalid number format.\nUsage: /setamount X.XX\nExample: /setamount 0.05"
                            self.telegram_bot.send_message(error_msg, chat_id=chat_id, disable_notification=False)
                        except Exception as e:
                            error_msg = f"❌ Error setting trade amount: {str(e)}"
                            self.telegram_bot.send_message(error_msg, chat_id=chat_id, disable_notification=False)
                            self.logger.error(f"Error in /setamount command: {e}")
                    
                    # Handle /status command
                    elif text == '/status':
                        try:
                            acc_info = self.mt5_api.account_info()
                            login = getattr(acc_info, 'login', 'N/A') if acc_info else 'N/A'
                            balance = getattr(acc_info, 'balance', 0.0) if acc_info else 0.0
                            equity = getattr(acc_info, 'equity', 0.0) if acc_info else 0.0
                            free_margin = getattr(acc_info, 'margin_free', 0.0) if acc_info else 0.0

                            open_positions = self.mt5_api.positions_get(symbol=self.trade_symbol)
                            pos_count = 0
                            open_pnl = 0.0
                            for p in open_positions or []:
                                if getattr(p, 'magic', None) == self.magic_number:
                                    pos_count += 1
                                    open_pnl += float(getattr(p, 'profit', 0.0))

                            pending_orders = self.mt5_api.orders_get(symbol=self.trade_symbol)
                            order_count = 0
                            for o in pending_orders or []:
                                if getattr(o, 'magic', None) == self.magic_number:
                                    order_count += 1

                            if self.bot_paused:
                                if self.profit_withdrawal_paused:
                                    status_str = 'Paused (Profit Withdrawal) 💰⏸️'
                                elif self.blackout_paused:
                                    status_str = 'Paused (Blackout) ⛔️⏸️'
                                elif not self.user_started:
                                    status_str = 'Paused (Awaiting Start) 🔶⏸️'
                                else:
                                    status_str = 'Paused ⏸️'
                            else:
                                status_str = 'Stopping after TP ⏳' if self.stop_requested else 'Running ✅'
                            next_amount_str = f"{self.next_trade_amount}" if self.next_trade_amount else '-'
                            
                            run_time_str = '-'
                            try:
                                if self.session_start_time:
                                    run_time = datetime.now() - self.session_start_time
                                    run_time_str = str(run_time).split('.')[0]
                            except Exception:
                                pass

                            msg = (
                                f"🤖 <b>Grid DCA Strategy Status</b> 🤖\n\n"
                                f"┌─────────────────────────────┐\n"
                                f"│     📊 <b>SYSTEM OVERVIEW</b>     │\n"
                                f"└─────────────────────────────┘\n\n"
                                f"🏦 <b>Account Information:</b>\n"
                                f"┣━ 🎯 Account: <code>{login}</code>\n"
                                f"┣━ 📈 Symbol: <code>{self.trade_symbol}</code>\n"
                                f"┣━ ⚙️ Status: <b>{status_str}</b>\n"
                            )
                            try:
                                if self.stop_at_datetime:
                                    msg += f"┣━ 🕒 Stop at: <code>{self.stop_at_datetime.strftime('%Y-%m-%d %H:%M')} GMT+7</code>\n"
                            except Exception:
                                pass
                            msg += (
                                f"┣━ 📏 Current Index: <code>{self.current_idx}</code>\n"
                                f"┗━ 🎯 Target Profit: <code>${self.tp_expected:.2f}</code>\n\n"
                                f"🕰️ <b>Session Metrics:</b>\n"
                                f"┣━ ⏱️ Run Time: <code>{run_time_str}</code>\n"
                                f"┗━ 💰 Session Profit: <code>${self.total_session_profit:.2f}</code>\n\n"
                                f"📊 <b>Account Balance:</b>\n"
                                f"┣━ 💳 Balance: <code>${balance:.2f}</code>\n"
                                f"┣━ 💰 Equity: <code>${equity:.2f}</code>\n"
                                f"┗━ 🛡️ Free Margin: <code>${free_margin:.2f}</code>\n\n"
                                f"📈 <b>Trading Activity:</b>\n"
                                f"┣━ 🗺️ Open Positions: <code>{pos_count}</code>\n"
                                f"┣━ 📄 Pending Orders: <code>{order_count}</code>\n"
                                f"┗━ 💹 Open PnL: <code>${open_pnl:.2f}</code>\n\n"
                                f"💼 <b>Trade Configuration:</b>\n"
                                f"┣━ ⚙️ Config Amount: <code>{self.trade_amount}</code>\n"
                                f"┗━ 🔄 Override Amount: <code>{next_amount_str}</code>\n\n"
                            )
                            msg += f"🛡️ <b>Risk Management:</b>\n"
                            try:
                                qh_state = '🟢 ON' if self.quiet_hours_enabled else '⚪ OFF'
                                msg += f"┣━ 🔇 Quiet Hours: <b>{qh_state}</b> <code>({self.quiet_hours_start:02d}-{self.quiet_hours_end:02d} x{self.quiet_hours_factor})</code>\n"
                                bl_state = '🟢 ON' if self.blackout_enabled else '⚪ OFF'
                                msg += f"┣━ ⛔ Blackout: <b>{bl_state}</b> <code>({self.blackout_start:02d}-{self.blackout_end:02d})</code>\n"
                                halt_state = '🟢 ON' if self.trading_halt_enabled else '⚪ OFF'
                                halt_status = '🛑 ACTIVE' if self.trading_halt_active else '⚪ Inactive'
                                msg += f"┣━ 🛑 Trading Halt: <b>{halt_state}</b> - {halt_status} <code>(04:30-06:15)</code>\n"
                                msg += f"┣━ 🎛️ Limits: <code>DD={self.max_dd_threshold} | Pos={self.max_positions} | Orders={self.max_orders} | Spread={self.max_spread}</code>\n"
                                msg += f"┣━ 💼 Max Reduce: <code>${self.max_reduce_balance:.2f}</code>\n"
                                if self.profit_withdrawal_threshold:
                                    msg += f"┗━ 💰 Withdrawal: <code>${self.profit_withdrawal_threshold:.2f}</code>\n\n"
                                else:
                                    msg = msg.replace('┣━ 💼 Max Reduce:', '┗━ 💼 Max Reduce:') + "\n"
                            except Exception:
                                msg += "┗━ ⚠️ Risk config error\n\n"
                            
                            msg += f"🔍 <b>Pattern Analysis:</b>\n"
                            try:
                                pd = self.check_consecutive_orders_pattern()
                                pattern_status = '🟢 Detected' if pd.get('pattern_detected') else '⚪ None'
                                msg += f"┣━ 🎯 Pattern: <b>{pattern_status}</b>\n"
                                msg += f"┣━ 🟢 BUY Pairs: <code>{len(pd.get('consecutive_buys', []))}</code>\n"
                                msg += f"┣━ 🔴 SELL Pairs: <code>{len(pd.get('consecutive_sells', []))}</code>\n"
                                msg += f"┗━ 📈 Total Filled: <code>{pd.get('total_filled', 0)}</code>\n\n"
                            except Exception as e_pattern:
                                msg += f"┗━ ⚠️ Analysis Error: <code>{str(e_pattern)[:30]}...</code>\n\n"
                            
                            # Add performance metrics hint
                            msg += f"🚀 <i>Use <code>/metrics</code> for detailed performance analytics</i>"

                            self.telegram_bot.send_message(msg, chat_id=chat_id, disable_notification=False)
                        except Exception as e:
                            self.logger.error(f"Error building /status: {e}")
                            self.telegram_bot.send_message("❌ Failed to get status.", chat_id=chat_id, disable_notification=False)

                    # Handle /help command
                    elif text == '/help':
                        try:
                            help_msg = (
                                "📖 <b>Available Commands</b>\n\n"
                                "<b>Control</b>\n"
                                "• /start — Start trading or resume bot (if paused)\n"
                                "• /resume — Alias of /start\n"
                                "• /pause — Pause immediately (no new grids)\n"
                                "• /stop — Finish current cycle, pause after TP\n"
                                "• /stopat HH:MM — Schedule pause at time (GMT+7)\n"
                                "• /panic — Emergency stop (requires '/panic confirm')\n\n"
                                "<b>Configuration</b>\n"
                                "• /setamount X.XX — Set persistent override (applies after next TP)\n"
                                "• /clearamount — Remove persistent override\n"
                                "• /quiethours — Show or set quiet-hours window and factor\n"
                                "• /tradinghalt — Control 4:30-6:15 AM news protection\n\n"
                                "• /setmaxdd X — Auto-pause if drawdown exceeds X\n"
                                "• /setmaxpos N — Cap concurrent positions\n"
                                "• /setmaxorders N — Cap concurrent pending orders\n"
                                "• /setspread X — Max allowed spread\n"
                                "• /setmaxreducebalance X — Max equity reduction allowed\n"
                                "• /setmaxexposure X.XX — Max total position exposure (lots)\n"
                                "• /setwithdrawal X — Set profit withdrawal threshold\n"
                                "• /withdrawalcomplete — Restart after profit withdrawal\n"
                                "• /blackout — Show or set a full trade blackout window\n\n"
                                "<b>Insights</b>\n"
                                "• /status — Bot and account status\n"
                                "• /drawdown — Show drawdown report\n\n"
                                "• /history N — Last N deals\n"
                                "• /pnl today|week|month — Aggregated PnL\n"
                                "• /filled — Show filled orders summary\n"
                                "• /pattern — Show consecutive filled-order pattern\n\n"
                                "<b>Examples</b>\n"
                                "• /setamount 0.05\n"
                                "• /stopat 21:00\n"
                                "• /setmaxdd 300\n"
                                "• /setspread 0.30\n"
                                "• /setmaxreducebalance 5000\n"
                                "• /setmaxexposure 10.0\n"
                                "• /setwithdrawal 500\n"
                                "• /panic confirm\n"
                            )
                            self.telegram_bot.send_message(help_msg, chat_id=chat_id, disable_notification=False)
                        except Exception as e:
                            self.logger.error(f"Error building /help: {e}")
                            self.telegram_bot.send_message("❌ Failed to build help.", chat_id=chat_id, disable_notification=False)

                    # Handle /pause command
                    elif text == '/pause':
                        try:
                            if not self.bot_paused:
                                self.bot_paused = True
                                self.stop_requested = False
                                self.blackout_paused = False  # Manual pause overrides blackout
                                self.telegram_bot.send_message(
                                    "⏸️ <b>Strategy Paused</b>\n\n"
                                    "┌─────────────────────────────┐\n"
                                    "│     🛑 <b>MANUAL PAUSE</b>      │\n"
                                    "└─────────────────────────────┘\n\n"
                                    "🚫 <b>Trading Status:</b>\n"
                                    "┣━ ⏸️ Strategy: <b>Paused immediately</b>\n"
                                    "┣━ 🚫 New Grids: <b>Disabled</b>\n"
                                    "┗━ 🔄 Resume: <code>/start</code> or <code>/resume</code>\n\n"
                                    "✅ <b>Existing positions remain active</b>",
                                    chat_id=chat_id,
                                    disable_notification=False,
                                )
                                self.logger.info("Bot paused by user command")
                            else:
                                self.telegram_bot.send_message("⏸️ Strategy is already paused.", chat_id=chat_id, disable_notification=False)
                        except Exception as e:
                            self.logger.error(f"Error handling /pause: {e}")

                    # Handle /panic command (requires confirmation)
                    elif text.startswith('/panic'):
                        try:
                            if text.strip().lower() == '/panic confirm':
                                self.close_all_positions(self.trade_symbol)
                                self.cancel_all_pending_orders(self.trade_symbol)
                                self.bot_paused = True
                                self.stop_requested = False
                                self.detail_orders.clear()
                                self.notified_filled.clear()
                                self.notified_tp.clear()
                                
                                self.telegram_bot.send_message(
                                    "🛑 <b>PANIC STOP Executed</b>\n\n"
                                    "┌─────────────────────────────┐\n"
                                    "│    🚨 <b>EMERGENCY HALT</b>     │\n"
                                    "└─────────────────────────────┘\n\n"
                                    "⚡ <b>Actions Completed:</b>\n"
                                    "┣━ 🗑️ All positions: <b>CLOSED</b>\n"
                                    "┣━ ❌ Pending orders: <b>CANCELLED</b>\n"
                                    "┣━ ⏸️ Strategy: <b>PAUSED</b>\n"
                                    "┗━ 🧹 Order tracking: <b>CLEARED</b>\n\n"
                                    "🔄 <b>To Resume:</b> Use <code>/start</code> or <code>/resume</code>\n\n"
                                    "⚠️ <i>All strategy positions have been safely closed</i>",
                                    chat_id=chat_id,
                                    disable_notification=False,
                                )
                                self.logger.warning("PANIC STOP executed: closed positions, cancelled orders, paused bot")
                            else:
                                self.telegram_bot.send_message(
                                    "⚠️ This will close all strategy positions and cancel all strategy orders immediately.\n\n"
                                    "If you are sure, send:\n<b>/panic confirm</b>",
                                    chat_id=chat_id,
                                    disable_notification=False,
                                )
                        except Exception as e:
                            self.logger.error(f"Error handling /panic: {e}")

                    # Handle /resume command (alias of /start)
                    elif text == '/resume':
                        try:
                            account_number = "N/A"
                            try:
                                acc_info = self.mt5_api.account_info()
                                if acc_info and hasattr(acc_info, 'login'):
                                    account_number = acc_info.login
                            except Exception as e:
                                self.logger.debug(f"Could not get account info: {e}")
                            if self.bot_paused:
                                self.bot_paused = False
                                self.stop_requested = False
                                self.blackout_paused = False  # Reset blackout pause state
                                resume_msg = (
                                    "▶️ <b>Bot Resumed!</b>\n\n"
                                    f"• Account: {account_number}\n"
                                    f"• Symbol: {self.trade_symbol}\n"
                                    f"• Trade Amount: {self.trade_amount}\n"
                                    "• Status: Running ✅\n\n"
                                    "The bot will now resume trading operations."
                                )
                                self.telegram_bot.send_message(resume_msg, chat_id=chat_id, disable_notification=False)
                                self.logger.info("Bot resumed by /resume")
                            else:
                                self.telegram_bot.send_message("▶️ Bot is already running.", chat_id=chat_id, disable_notification=False)
                        except Exception as e:
                            self.logger.error(f"Error handling /resume: {e}")

                    # Handle /drawdown command
                    elif text == '/drawdown':
                        try:
                            self.telegram_bot.send_message(self.drawdown_report(), chat_id=chat_id, disable_notification=False)
                        except Exception as e:
                            self.logger.error(f"Error handling /drawdown: {e}")
                    
                    # Handle /metrics command
                    elif text == '/metrics':
                        try:
                            metrics = self.get_performance_metrics()
                            
                            # Format uptime nicely
                            uptime_str = "-"
                            if metrics['uptime_seconds'] > 0:
                                hours = int(metrics['uptime_seconds'] // 3600)
                                minutes = int((metrics['uptime_seconds'] % 3600) // 60)
                                uptime_str = f"{hours}h {minutes}m"
                            
                            # Format iteration time
                            iter_time_str = f"{metrics['avg_iteration_time']:.3f}s" if metrics['avg_iteration_time'] > 0 else "-"
                            
                            # Calculate performance efficiency
                            efficiency_percent = 100 - metrics['cache_hit_rate'] if metrics['cache_hit_rate'] > 0 else 0
                            
                            metrics_msg = (
                                f"🚀 <b>Performance Analytics</b> 🚀\n\n"
                                f"┌─────────────────────────────┐\n"
                                f"│   📊 <b>SYSTEM METRICS</b>    │\n"
                                f"└─────────────────────────────┘\n\n"
                                f"📈 <b>TRADING METRICS</b>\n"
                                f"┣━ 📤 Orders Placed: <code>{metrics['orders_placed']:,}</code>\n"
                                f"┣━ ✅ Orders Filled: <code>{metrics['orders_filled']:,}</code>\n"
                                f"┣━ 🎯 TPs Reached: <code>{metrics['tps_reached']:,}</code>\n"
                                f"┣━ 📊 Fill Rate: <code>{metrics['fill_rate']:.1f}%</code>\n"
                                f"┣━ 🎯 TP Rate: <code>{metrics['tp_rate']:.1f}%</code>\n"
                                f"┗━ ⚠️ Errors: <code>{metrics['errors']:,}</code>\n\n"
                                f"🔋 <b>API Performance:</b>\n"
                                f"┣━ 📞 Total Requests: <code>{metrics['total_requests']:,}</code>\n"
                                f"┣━ 🔴 Direct API Calls: <code>{metrics['api_calls']:,}</code>\n"
                                f"┣━ 🟢 Cache Hits: <code>{metrics['cache_hits']:,}</code>\n"
                                f"┣━ 🚀 Cache Efficiency: <code>{metrics['cache_hit_rate']:.1f}%</code>\n"
                                f"┗━ ⚡ API Reduction: <code>~{metrics['cache_hit_rate']:.0f}% fewer calls</code>\n\n"
                                f"🔌 <b>Connection Health:</b>\n"
                                f"┣━ ❌ Connection Failures: <code>{metrics['connection_failures']}</code>\n"
                                f"┣━ 🔄 Auto-reconnections: <code>{max(0, metrics['connection_failures'] - 1)}</code>\n"
                                f"┗━ 🟢 Current Status: <b>{'Healthy ✅' if metrics['connection_failures'] == 0 else 'Recovering 🔄'}</b>\n\n"
                                f"⏱️ <b>Runtime Statistics:</b>\n"
                                f"┣━ 🕒 Uptime: <code>{uptime_str}</code>\n"
                                f"┣━ 🔄 Total Iterations: <code>{metrics['total_iterations']:,}</code>\n"
                                f"┗━ ⚡ Avg Iteration: <code>{iter_time_str}</code>\n\n"
                                f"📊 <b>Performance Summary:</b>\n"
                                f"┣━ 🚀 API Optimization: <b>Active ({'High' if metrics['cache_hit_rate'] > 75 else 'Medium' if metrics['cache_hit_rate'] > 50 else 'Low'})</b>\n"
                                f"┣━ 🛡️ Connection Stability: <b>{'Excellent' if metrics['connection_failures'] == 0 else 'Good' if metrics['connection_failures'] < 3 else 'Fair'}</b>\n"
                                f"┗━ ⚙️ Overall Health: <b>{'Optimal 🟢' if metrics['connection_failures'] == 0 and metrics['cache_hit_rate'] > 70 else 'Good 🟡' if metrics['connection_failures'] < 2 else 'Fair 🟠'}</b>\n\n"
                                f"<i>📝 Use <code>/status</code> for trading overview</i>"
                            )
                            
                            self.telegram_bot.send_message(metrics_msg, chat_id=chat_id, disable_notification=False)
                            self.logger.info(f"Performance metrics sent: {metrics['cache_hit_rate']:.1f}% cache efficiency")
                            
                        except Exception as e:
                            self.logger.error(f"Error handling /metrics: {e}")
                            self.telegram_bot.send_message("❌ Failed to get performance metrics.", chat_id=chat_id, disable_notification=False)

                    # Handle /tradinghalt command
                    elif text.startswith('/tradinghalt'):
                        try:
                            parts = text.split()
                            if len(parts) == 1:
                                status = 'ON' if self.trading_halt_enabled else 'OFF'
                                current_status = '🛑 ACTIVE' if self.trading_halt_active else '⚪ Inactive'
                                now_local = self.get_gmt7_time()
                                info_msg = (
                                    f"🛑 <b>TRADING HALT CONFIGURATION</b>\n\n"
                                    f"📊 <b>Current Status:</b>\n"
                                    f"┣━ 🔧 Feature: <b>{status}</b>\n"
                                    f"┣━ 📅 Schedule: <code>04:30-06:15 GMT+7</code>\n"
                                    f"┣━ ⚡ Current: <b>{current_status}</b>\n"
                                    f"┗━ 🕐 Now: <code>{now_local.strftime('%H:%M')}</code>\n\n"
                                    f"💡 <b>Commands:</b>\n"
                                    f"┣━ <code>/tradinghalt on</code> - Enable protection\n"
                                    f"┗━ <code>/tradinghalt off</code> - Disable protection\n\n"
                                    f"<i>🛡️ Protects against early morning news volatility</i>"
                                )
                                self.telegram_bot.send_message(info_msg, chat_id=chat_id, disable_notification=False)
                            elif len(parts) == 2 and parts[1].lower() == 'on':
                                self.trading_halt_enabled = True
                                self.telegram_bot.send_message(
                                    f"✅ <b>Trading Halt Enabled</b>\n\n"
                                    f"🛑 No new orders during 04:30-06:15 GMT+7\n"
                                    f"🛡️ News volatility protection active", 
                                    chat_id=chat_id, disable_notification=False
                                )
                            elif len(parts) == 2 and parts[1].lower() == 'off':
                                self.trading_halt_enabled = False
                                self.trading_halt_active = False  # Clear current halt if active
                                self.telegram_bot.send_message(
                                    f"❌ <b>Trading Halt Disabled</b>\n\n"
                                    f"⚠️ Bot will trade during all hours\n"
                                    f"🚨 Higher risk during news periods", 
                                    chat_id=chat_id, disable_notification=False
                                )
                            else:
                                self.telegram_bot.send_message(
                                    f"📖 <b>Trading Halt Usage</b>\n\n"
                                    f"<code>/tradinghalt</code> - Show status\n"
                                    f"<code>/tradinghalt on</code> - Enable\n"
                                    f"<code>/tradinghalt off</code> - Disable\n\n"
                                    f"🛑 Prevents new orders 04:30-06:15", 
                                    chat_id=chat_id, disable_notification=False
                                )
                        except Exception as e:
                            self.logger.error(f"Error handling /tradinghalt: {e}")
                            self.telegram_bot.send_message("❌ Failed to configure trading halt.", chat_id=chat_id, disable_notification=False)

                    # Handle /clearamount command
                    elif text.strip().lower() == '/clearamount':
                        try:
                            if self.next_trade_amount is not None:
                                cleared = self.next_trade_amount
                                self.next_trade_amount = None
                                self.telegram_bot.send_message(
                                    f"🧹 Cleared persistent amount override (was: {cleared}).\n"
                                    f"Bot will use configured/time-based amount going forward.",
                                    chat_id=chat_id,
                                    disable_notification=False,
                                )
                                self.logger.info("Persistent trade amount override cleared")
                            else:
                                self.telegram_bot.send_message("ℹ️ No persistent override set.", chat_id=chat_id, disable_notification=False)
                        except Exception as e:
                            self.logger.error(f"Error handling /clearamount: {e}")
                            self.telegram_bot.send_message("❌ Failed to clear override.", chat_id=chat_id, disable_notification=False)

                    # Handle /stopat HH:MM (GMT+7) or /stopat off
                    elif text.startswith('/stopat'):
                        try:
                            parts = text.split()
                            if len(parts) == 2 and parts[1].lower() == 'off':
                                self.stop_at_datetime = None
                                self.telegram_bot.send_message("🕒 Scheduled pause cleared.", chat_id=chat_id, disable_notification=False)
                            elif len(parts) == 2 and ':' in parts[1]:
                                hh, mm = parts[1].split(':', 1)
                                hh_i, mm_i = int(hh), int(mm)
                                if not (0 <= hh_i <= 23 and 0 <= mm_i <= 59):
                                    raise ValueError('Invalid time')
                                now7 = self.get_gmt7_time()
                                sched = now7.replace(hour=hh_i, minute=mm_i, second=0, microsecond=0)
                                if sched <= now7:
                                    sched += timedelta(days=1)
                                self.stop_at_datetime = sched
                                self.telegram_bot.send_message(
                                    f"🕒 Will pause at {sched.strftime('%Y-%m-%d %H:%M')} GMT+7.",
                                    chat_id=chat_id,
                                    disable_notification=False,
                                )
                            else:
                                self.telegram_bot.send_message("Usage: /stopat HH:MM or /stopat off", chat_id=chat_id, disable_notification=False)
                        except Exception as e:
                            self.logger.error(f"Error handling /stopat: {e}")
                            self.telegram_bot.send_message("❌ Failed to schedule pause.", chat_id=chat_id, disable_notification=False)

                    # Handle risk caps
                    elif text.startswith('/setmaxdd'):
                        try:
                            parts = text.split()
                            if len(parts) == 2:
                                self.max_dd_threshold = float(parts[1])
                                self.telegram_bot.send_message(f"🛡️ Max drawdown set to {self.max_dd_threshold}", chat_id=chat_id, disable_notification=False)
                            else:
                                self.telegram_bot.send_message("Usage: /setmaxdd X", chat_id=chat_id, disable_notification=False)
                        except Exception as e:
                            self.logger.error(f"Error handling /setmaxdd: {e}")
                            self.telegram_bot.send_message("❌ Failed to set max drawdown.", chat_id=chat_id, disable_notification=False)

                    elif text.startswith('/setmaxpos'):
                        try:
                            parts = text.split()
                            if len(parts) == 2:
                                self.max_positions = int(parts[1])
                                self.telegram_bot.send_message(f"🛡️ Max positions set to {self.max_positions}", chat_id=chat_id, disable_notification=False)
                            else:
                                self.telegram_bot.send_message("Usage: /setmaxpos N", chat_id=chat_id, disable_notification=False)
                        except Exception as e:
                            self.logger.error(f"Error handling /setmaxpos: {e}")
                            self.telegram_bot.send_message("❌ Failed to set max positions.", chat_id=chat_id, disable_notification=False)

                    elif text.startswith('/setmaxorders'):
                        try:
                            parts = text.split()
                            if len(parts) == 2:
                                self.max_orders = int(parts[1])
                                self.telegram_bot.send_message(f"🛡️ Max pending orders set to {self.max_orders}", chat_id=chat_id, disable_notification=False)
                            else:
                                self.telegram_bot.send_message("Usage: /setmaxorders N", chat_id=chat_id, disable_notification=False)
                        except Exception as e:
                            self.logger.error(f"Error handling /setmaxorders: {e}")
                            self.telegram_bot.send_message("❌ Failed to set max pending orders.", chat_id=chat_id, disable_notification=False)

                    elif text.startswith('/setspread'):
                        try:
                            parts = text.split()
                            if len(parts) == 2:
                                self.max_spread = float(parts[1])
                                self.telegram_bot.send_message(f"🛡️ Max spread set to {self.max_spread}", chat_id=chat_id, disable_notification=False)
                            else:
                                self.telegram_bot.send_message("Usage: /setspread X", chat_id=chat_id, disable_notification=False)
                        except Exception as e:
                            self.logger.error(f"Error handling /setspread: {e}")
                            self.telegram_bot.send_message("❌ Failed to set max spread.", chat_id=chat_id, disable_notification=False)

                    # Handle /setwithdrawal command (set profit withdrawal threshold)
                    elif text.startswith('/setwithdrawal'):
                        try:
                            parts = text.split()
                            if len(parts) == 2:
                                new_threshold = float(parts[1])
                                if new_threshold > 0:
                                    old_threshold = self.profit_withdrawal_threshold
                                    self.profit_withdrawal_threshold = new_threshold
                                    success_msg = (
                                        f"💰 <b>Profit Withdrawal Threshold Set</b>\n\n"
                                        f"• Previous: ${old_threshold:.2f}" if old_threshold else "• Previous: Not set\n"
                                        f"• New Threshold: ${new_threshold:.2f}\n\n"
                                        f"Strategy will pause when total session profit reaches ${new_threshold:.2f}\n"
                                        f"Current Session Profit: ${self.total_session_profit:.2f}"
                                    )
                                    self.telegram_bot.send_message(success_msg, chat_id=chat_id, disable_notification=False)
                                    self.logger.info(f"Profit withdrawal threshold set to ${new_threshold:.2f}")
                                else:
                                    error_msg = "❌ Amount must be greater than 0.\nExample: /setwithdrawal 500"
                                    self.telegram_bot.send_message(error_msg, chat_id=chat_id, disable_notification=False)
                            else:
                                help_msg = (
                                    "💰 <b>Set Profit Withdrawal Threshold</b>\n\n"
                                    "Usage: /setwithdrawal AMOUNT\n"
                                    "Bot will pause when total session profit reaches this amount.\n\n"
                                    "Examples:\n"
                                    "• /setwithdrawal 500 (pause at $500 profit)\n"
                                    "• /setwithdrawal 1000 (pause at $1000 profit)\n\n"
                                    f"Current threshold: ${self.profit_withdrawal_threshold:.2f}" if self.profit_withdrawal_threshold else "Current threshold: Not set\n"
                                    f"Current session profit: ${self.total_session_profit:.2f}"
                                )
                                self.telegram_bot.send_message(help_msg, chat_id=chat_id, disable_notification=False)
                        except ValueError:
                            error_msg = "❌ Invalid number format.\nUsage: /setwithdrawal AMOUNT\nExample: /setwithdrawal 500"
                            self.telegram_bot.send_message(error_msg, chat_id=chat_id, disable_notification=False)
                        except Exception as e:
                            self.logger.error(f"Error in /setwithdrawal command: {e}")
                    
                    # Handle /withdrawalcomplete command (restart after profit withdrawal)
                    elif text == '/withdrawalcomplete':
                        if self.profit_withdrawal_paused:
                            # Reset all strategy state for fresh restart
                            self.profit_withdrawal_paused = False
                            self.bot_paused = False
                            self.stop_requested = False
                            self.blackout_paused = False
                            
                            # Reset profit tracking
                            self.total_session_profit = 0
                            
                            # Reset strategy state
                            self.detail_orders = {}
                            self.notified_filled.clear()
                            self.notified_tp.clear()
                            self.current_idx = 0
                            self.max_drawdown = 0
                            
                            # Get fresh balance and restart
                            new_start_balance = self.get_current_balance()
                            balance_difference = new_start_balance - self.withdrawal_start_balance
                            
                            restart_msg = (
                                f"🔄 <b>STRATEGY RESTARTED AFTER WITHDRAWAL</b>\n\n"
                                f"💰 <b>Balance Update:</b>\n"
                                f"• Balance before withdrawal: ${self.withdrawal_start_balance:.2f}\n"
                                f"• Current balance: ${new_start_balance:.2f}\n"
                                f"• Difference: ${balance_difference:.2f}\n\n"
                                f"🔄 <b>Fresh Start:</b>\n"
                                f"• Profit tracking reset to $0\n"
                                f"• All positions and orders cleared\n"
                                f"• Grid index reset to 0\n"
                                f"• Drawdown tracking reset\n\n"
                                f"✅ <b>Strategy is now running with clean state!</b>"
                            )
                            
                            self.telegram_bot.send_message(restart_msg, chat_id=chat_id, pin_msg=True, disable_notification=False)
                            self.logger.info(f"Strategy restarted after profit withdrawal. New balance: ${new_start_balance:.2f}")
                            
                        else:
                            error_msg = (
                                "❌ <b>No withdrawal in progress</b>\n\n"
                                "This command is only available when the bot is paused for profit withdrawal.\n\n"
                                f"Current session profit: ${self.total_session_profit:.2f}\n"
                                f"Withdrawal threshold: ${self.profit_withdrawal_threshold:.2f}" if self.profit_withdrawal_threshold else "Withdrawal threshold: Not set"
                            )
                            self.telegram_bot.send_message(error_msg, chat_id=chat_id, disable_notification=False)

                    elif text.startswith('/setmaxreducebalance'):
                        try:
                            parts = text.split()
                            if len(parts) == 2:
                                new_max_reduce = float(parts[1])
                                if new_max_reduce > 0:
                                    self.max_reduce_balance = new_max_reduce
                                    self.telegram_bot.send_message(f"🛡️ Max reduce balance set to ${self.max_reduce_balance:.2f}", chat_id=chat_id, disable_notification=False)
                                    self.logger.info(f"Max reduce balance updated to {self.max_reduce_balance}")
                                else:
                                    self.telegram_bot.send_message("❌ Max reduce balance must be positive.", chat_id=chat_id, disable_notification=False)
                            else:
                                self.telegram_bot.send_message("Usage: /setmaxreducebalance XXXX\nExample: /setmaxreducebalance 5000", chat_id=chat_id, disable_notification=False)
                        except ValueError:
                            self.telegram_bot.send_message("❌ Invalid number format.\nUsage: /setmaxreducebalance XXXX\nExample: /setmaxreducebalance 5000", chat_id=chat_id, disable_notification=False)
                        except Exception as e:
                            self.logger.error(f"Error handling /setmaxreducebalance: {e}")
                            self.telegram_bot.send_message("❌ Failed to set max reduce balance.", chat_id=chat_id, disable_notification=False)
                        
                        # Check current index for new orders after setting max reduce balance
                        try:
                            self.run_at_index(self.trade_symbol, self.trade_amount, self.current_idx, price=0)
                        except Exception as e:
                            self.logger.error(f"Error checking index after /setmaxreducebalance: {e}")

                    # Handle /setmaxexposure command
                    elif text.startswith('/setmaxexposure'):
                        try:
                            parts = text.split()
                            if len(parts) == 2:
                                if parts[1].lower() == 'off':
                                    self.max_total_exposure = None
                                    self.telegram_bot.send_message("🛡️ Max exposure limit disabled.", chat_id=chat_id, disable_notification=False)
                                    self.logger.info("Max total exposure limit disabled")
                                else:
                                    new_max_exposure = float(parts[1])
                                    if new_max_exposure > 0:
                                        self.max_total_exposure = new_max_exposure
                                        current_exposure = self.calculate_total_exposure(self.trade_symbol)
                                        self.telegram_bot.send_message(
                                            f"🛡️ <b>Max Exposure Limit Set</b>\n\n"
                                            f"Max Total Exposure: <code>{self.max_total_exposure:.2f}</code> lots\n"
                                            f"Current Exposure: <code>{current_exposure:.2f}</code> lots\n"
                                            f"Remaining Capacity: <code>{max(0, self.max_total_exposure - current_exposure):.2f}</code> lots\n\n"
                                            f"Orders will be blocked if total exposure exceeds this limit.",
                                            chat_id=chat_id,
                                            disable_notification=False
                                        )
                                        self.logger.info(f"Max total exposure updated to {self.max_total_exposure} lots")
                                    else:
                                        self.telegram_bot.send_message("❌ Max exposure must be positive.", chat_id=chat_id, disable_notification=False)
                            else:
                                current_exposure = self.calculate_total_exposure(self.trade_symbol)
                                status = f"<code>{self.max_total_exposure:.2f}</code> lots" if self.max_total_exposure else "Disabled"
                                self.telegram_bot.send_message(
                                    f"🛡️ <b>Max Exposure Limit</b>\n\n"
                                    f"Current Setting: {status}\n"
                                    f"Current Exposure: <code>{current_exposure:.2f}</code> lots\n\n"
                                    f"Usage: /setmaxexposure X.XX\n"
                                    f"Example: /setmaxexposure 10.0\n"
                                    f"To disable: /setmaxexposure off",
                                    chat_id=chat_id,
                                    disable_notification=False
                                )
                        except ValueError:
                            self.telegram_bot.send_message(
                                "❌ Invalid number format.\n\n"
                                "Usage: /setmaxexposure X.XX\n"
                                "Example: /setmaxexposure 10.0\n"
                                "To disable: /setmaxexposure off",
                                chat_id=chat_id,
                                disable_notification=False
                            )
                        except Exception as e:
                            self.logger.error(f"Error handling /setmaxexposure: {e}")
                            self.telegram_bot.send_message("❌ Failed to set max exposure limit.", chat_id=chat_id, disable_notification=False)

                    # Blackout window
                    elif text.startswith('/blackout'):
                        try:
                            parts = text.split()
                            if len(parts) == 1:
                                state = 'on' if self.blackout_enabled else 'off'
                                self.telegram_bot.send_message(
                                    f"⛔️ Blackout {state}. Window: {self.blackout_start:02d}-{self.blackout_end:02d} GMT+7",
                                    chat_id=chat_id,
                                    disable_notification=False,
                                )
                            elif len(parts) == 2 and parts[1].lower() == 'off':
                                self.blackout_enabled = False
                                self.telegram_bot.send_message("⛔️ Blackout disabled.", chat_id=chat_id, disable_notification=False)
                            elif len(parts) == 2 and '-' in parts[1]:
                                start_s, end_s = parts[1].split('-', 1)
                                start, end = int(start_s), int(end_s)
                                if not (0 <= start <= 23 and 0 <= end <= 23):
                                    raise ValueError('Hours must be 0-23')
                                self.blackout_start, self.blackout_end = start, end
                                self.blackout_enabled = True
                                self.telegram_bot.send_message(
                                    f"⛔️ Blackout set: {start:02d}-{end:02d} GMT+7 (enabled)",
                                    chat_id=chat_id,
                                    disable_notification=False,
                                )
                            else:
                                self.telegram_bot.send_message("Usage: /blackout HH-HH or /blackout off", chat_id=chat_id, disable_notification=False)
                        except Exception as e:
                            self.logger.error(f"Error handling /blackout: {e}")
                            self.telegram_bot.send_message("❌ Failed to set blackout.", chat_id=chat_id, disable_notification=False)

                    # Quiet hours
                    elif text.startswith('/quiethours'):
                        try:
                            parts = text.split()
                            if len(parts) == 1:
                                state = 'on' if self.quiet_hours_enabled else 'off'
                                self.telegram_bot.send_message(
                                    (
                                        f"🕰️ <b>Quiet Hours</b> {state}\n"
                                        f"Window: {self.quiet_hours_start:02d}-{self.quiet_hours_end:02d} GMT+7\n"
                                        f"Factor: x{self.quiet_hours_factor}\n\n"
                                        "Usage:\n"
                                        "/quiethours on|off\n"
                                        "/quiethours HH-HH [factor]\n"
                                        "Example: /quiethours 19-23 0.5"
                                    ),
                                    chat_id=chat_id,
                                    disable_notification=False,
                                )
                            elif len(parts) == 2 and parts[1].lower() in ('on', 'off'):
                                self.quiet_hours_enabled = (parts[1].lower() == 'on')
                                self.telegram_bot.send_message(f"🕰️ Quiet hours {'enabled' if self.quiet_hours_enabled else 'disabled'}.", chat_id=chat_id, disable_notification=False)
                            elif len(parts) >= 2 and '-' in parts[1]:
                                start_s, end_s = parts[1].split('-', 1)
                                start, end = int(start_s), int(end_s)
                                if not (0 <= start <= 23 and 0 <= end <= 23):
                                    raise ValueError('Hours must be 0-23')
                                self.quiet_hours_start, self.quiet_hours_end = start, end
                                if len(parts) == 3:
                                    self.quiet_hours_factor = float(parts[2])
                                self.quiet_hours_enabled = True
                                self.telegram_bot.send_message(
                                    f"🕰️ Quiet hours set: {start:02d}-{end:02d} x{self.quiet_hours_factor} (enabled)",
                                    chat_id=chat_id,
                                    disable_notification=False,
                                )
                            else:
                                self.telegram_bot.send_message("Usage: /quiethours [on|off] or /quiethours HH-HH [factor]", chat_id=chat_id, disable_notification=False)
                        except Exception as e:
                            self.logger.error(f"Error handling /quiethours: {e}")
                            self.telegram_bot.send_message("❌ Failed to configure quiet hours.", chat_id=chat_id, disable_notification=False)

                    # History
                    elif text.startswith('/history'):
                        try:
                            parts = text.split()
                            n = int(parts[1]) if len(parts) == 2 else 10
                            now = self.get_gmt7_time()
                            start = now - timedelta(days=30)
                            deals = self.mt5_api.history_deals_get(start, now)
                            items = []
                            for d in deals or []:
                                try:
                                    if getattr(d, 'symbol', '') != self.trade_symbol:
                                        continue
                                    if getattr(d, 'magic', None) != self.magic_number:
                                        continue
                                    t = getattr(d, 'time', None)
                                    if isinstance(t, (int, float)):
                                        tz_gmt7 = timezone(timedelta(hours=7))
                                        ts = datetime.fromtimestamp(t, tz_gmt7).strftime('%Y-%m-%d %H:%M')
                                    else:
                                        ts = str(t)
                                    price = getattr(d, 'price', 0.0)
                                    profit = getattr(d, 'profit', 0.0)
                                    volume = getattr(d, 'volume', 0.0)
                                    dtype = getattr(d, 'type', None)
                                    side = 'BUY' if dtype == self.mt5_api.DEAL_TYPE_BUY else ('SELL' if dtype == self.mt5_api.DEAL_TYPE_SELL else str(dtype))
                                    items.append((getattr(d, 'ticket', 0), ts, side, volume, price, profit))
                                except Exception:
                                    continue
                            items = list(reversed(sorted(items, key=lambda x: x[0])))
                            items = items[:n]
                            if not items:
                                self.telegram_bot.send_message("ℹ️ No recent strategy deals found.", chat_id=chat_id, disable_notification=False)
                            else:
                                lines = [
                                    f"#{tid} {ts} {side} {vol} @ {price:.2f} → PnL {pnl:+.2f}"
                                    for (tid, ts, side, vol, price, pnl) in items
                                ]
                                self.telegram_bot.send_message("\n".join(lines), chat_id=chat_id, disable_notification=False)
                        except Exception as e:
                            self.logger.error(f"Error handling /history: {e}")
                            self.telegram_bot.send_message("❌ Failed to fetch history.", chat_id=chat_id, disable_notification=False)

                    # PnL aggregation
                    elif text.startswith('/pnl'):
                        try:
                            parts = text.split()
                            scope = parts[1].lower() if len(parts) == 2 else 'today'
                            now = self.get_gmt7_time()
                            if scope == 'today':
                                start = now.replace(hour=0, minute=0, second=0, microsecond=0)
                            elif scope == 'week':
                                start = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
                            elif scope == 'month':
                                start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
                            else:
                                self.telegram_bot.send_message("Usage: /pnl today|week|month", chat_id=chat_id, disable_notification=False)
                                start = None
                            if start is not None:
                                deals = self.mt5_api.history_deals_get(start, now)
                                total = 0.0
                                count = 0
                                for d in deals or []:
                                    if getattr(d, 'symbol', '') != self.trade_symbol:
                                        continue
                                    if getattr(d, 'magic', None) != self.magic_number:
                                        continue
                                    total += float(getattr(d, 'profit', 0.0))
                                    count += 1
                                self.telegram_bot.send_message(f"📈 PnL {scope}: {total:+.2f} ({count} deals)", chat_id=chat_id, disable_notification=False)
                        except Exception as e:
                            self.logger.error(f"Error handling /pnl: {e}")
                            self.telegram_bot.send_message("❌ Failed to compute PnL.", chat_id=chat_id, disable_notification=False)

                    # Filled orders summary
                    elif text.strip().lower() == '/filled':
                        try:
                            self.telegram_bot.send_message(self.get_filled_orders_summary(), chat_id=chat_id, disable_notification=False)
                        except Exception as e:
                            self.logger.error(f"Error handling /filled: {e}")
                            self.telegram_bot.send_message("❌ Failed to show filled orders.", chat_id=chat_id, disable_notification=False)

                    # Pattern detection
                    elif text.strip().lower() == '/pattern':
                        try:
                            pd = self.check_consecutive_orders_pattern()
                            msg = (
                                "🧩 <b>Consecutive Pattern</b>\n"
                                f"Detected: {'Yes' if pd.get('pattern_detected') else 'No'}\n"
                                f"Consecutive BUY pairs: {len(pd.get('consecutive_buys', []))}\n"
                                f"Consecutive SELL pairs: {len(pd.get('consecutive_sells', []))}\n"
                                f"Total filled: {pd.get('total_filled', 0)}\n"
                            )
                            self.telegram_bot.send_message(msg, chat_id=chat_id, disable_notification=False)
                        except Exception as e:
                            self.logger.error(f"Error handling /pattern: {e}")
                            self.telegram_bot.send_message("❌ Failed to compute pattern.", chat_id=chat_id, disable_notification=False)

        except Exception as telegram_error:
            # Handle various Telegram errors gracefully
            error_msg = str(telegram_error).lower()
            if 'timed out' in error_msg:
                # Timeout is normal, just continue without logging as error
                pass
            elif 'connection pool is full' in error_msg:
                # Connection pool issue - brief pause to let connections recover
                time.sleep(0.1)
            elif 'network' in error_msg or 'connection' in error_msg:
                # Network issues - brief pause and continue
                time.sleep(0.2)
            else:
                # Log other unexpected errors
                self.logger.debug(f"Telegram command error: {telegram_error}")
