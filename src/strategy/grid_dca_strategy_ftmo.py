"""
Grid DCA Strategy - Reusable Strategy Module
Consolidates common logic used across all main_xxx.py instances.
"""
import logging
import time
import csv
import os
from datetime import datetime, timedelta, timezone
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import plotly.io as pio
import pandas as pd
import io
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
        self.max_reduce_balance = trading_config.get('max_reduce_balance', 5000)
        self.max_reduce_enabled = trading_config.get('max_reduce_enabled', True)
        self.max_reduce_percentage = trading_config.get('max_reduce_percentage', None)  # Optional percentage-based limit
        self.max_reduce_use_percentage = trading_config.get('max_reduce_use_percentage', False)
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
        # Control flags
        self.bot_paused = False
        self.stop_requested = False
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
        # Blackout window (2am-6am GMT+7 for risk management)
        self.blackout_enabled = True
        self.blackout_start = 2  # 2am GMT+7
        self.blackout_end = 6    # 6am GMT+7
        # Weekend blackout (Saturday and Sunday)
        self.weekend_blackout_enabled = True
        # Scheduled pause
        self.stop_at_datetime = None
        # Magic number for strategy identification
        self.magic_number = 234002
        # Telegram update tracking (to avoid processing same command multiple times)
        self.last_telegram_update_id = None
        # Balance/equity tracking for periodic logging
        self.last_balance_log_time = None
        self.balance_log_interval = 60  # 1 minute in seconds
        self.balance_log_file = None
        # Blackout state tracking for notifications
        self.in_blackout_period = False
        self.blackout_allow_cycle_completion = True  # Allow current cycle to complete during blackout
        # Max reduce emergency stop tracking
        self.max_reduce_triggered = False
        self.max_reduce_threshold_equity = None
        self.max_reduce_warning_sent = False  # Track if warning was sent
        self.max_reduce_warning_threshold = 0.8  # Send warning at 80% of limit
        self.max_reduce_trigger_time = None  # Track when emergency stop was triggered
        self.max_reduce_peak_drawdown = 0  # Track peak drawdown before reset
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
            self.logger.info(f"DEBUG :: pos_closed_pnl {position_id}")
            res = self.mt5_api.history_deals_get(position=position_id)
            info = res[-1]
            self.logger.info(f"DEBUG :: pos_closed_pnl :: detail {info}")
            pnl += info.profit
        except Exception as e:
            self.logger.error(f"ERROR :: pos_closed_pnl :: {e}")
        return pnl
    def get_current_balance(self):
        """Get current account balance."""
        current_balance = 0
        try:
            acc_info_mt5 = self.mt5_api.account_info()
            self.logger.info(f"DEBUG :: acc_info_mt5 {acc_info_mt5}")
            if acc_info_mt5 and hasattr(acc_info_mt5, 'balance'):
                current_balance = acc_info_mt5.balance
        except Exception as e:
            self.logger.error(f"Error getting balance: {e}")
        return current_balance
    def get_current_equity(self):
        """Get current account equity."""
        current_equity = 0
        try:
            acc_info_mt5 = self.mt5_api.account_info()
            self.logger.info(f"DEBUG :: acc_info_mt5 {acc_info_mt5}")
            if acc_info_mt5 and hasattr(acc_info_mt5, 'equity'):
                current_equity = acc_info_mt5.equity
        except Exception as e:
            self.logger.error(f"Error getting equity: {e}")
        return current_equity
    def get_current_free_margin(self):
        """Get current free margin."""
        current_free_margin = 0
        try:
            acc_info_mt5 = self.mt5_api.account_info()
            self.logger.info(f"DEBUG :: acc_info_mt5 {acc_info_mt5}")
            if acc_info_mt5 and hasattr(acc_info_mt5, 'margin_free'):
                current_free_margin = acc_info_mt5.margin_free
        except Exception as e:
            self.logger.error(f"Error getting free margin: {e}")
        return current_free_margin
    def initialize_balance_log(self):
        """Initialize the balance/equity log file."""
        try:
            # Create data/balances directory if it doesn't exist
            balances_dir = os.path.join("data", "balances")
            if not os.path.exists(balances_dir):
                os.makedirs(balances_dir)
            # Get account info for filename
            account_number = "unknown"
            try:
                acc_info = self.mt5_api.account_info()
                if acc_info and hasattr(acc_info, 'login'):
                    account_number = str(acc_info.login)
            except Exception:
                pass
            # Create filename with account number only (no timestamp for continuous logging)
            filename = f"balance_equity_{account_number}.csv"
            self.balance_log_file = os.path.join(balances_dir, filename)
            # Write CSV header only if file doesn't exist
            file_exists = os.path.exists(self.balance_log_file)
            if not file_exists:
                with open(self.balance_log_file, 'w', newline='', encoding='utf-8') as f:
                    writer = csv.writer(f)
                    writer.writerow(['timestamp', 'datetime_gmt7', 'balance', 'equity', 'free_margin', 
                                   'drawdown', 'pnl_from_start', 'session_runtime_minutes'])
            if file_exists:
                self.logger.info(f"📊 Balance log continuing: {self.balance_log_file}")
            else:
                self.logger.info(f"✅ Balance log initialized: {self.balance_log_file}")
            return True
        except Exception as e:
            self.logger.error(f"Failed to initialize balance log: {e}")
            return False
    def log_balance_equity(self):
        """Log current balance and equity data to CSV file."""
        try:
            if not self.balance_log_file:
                if not self.initialize_balance_log():
                    return
            # Get current account data
            balance = self.get_current_balance()
            equity = self.get_current_equity()
            free_margin = self.get_current_free_margin()
            # Calculate derived metrics
            drawdown = max(0, self.start_balance - equity) if self.start_balance > 0 else 0
            pnl_from_start = balance - self.start_balance if self.start_balance > 0 else 0
            # Calculate session runtime
            runtime_minutes = 0
            if self.session_start_time:
                runtime_delta = datetime.now() - self.session_start_time
                runtime_minutes = runtime_delta.total_seconds() / 60
            # Create timestamp
            now = datetime.now()
            gmt7_time = now + timedelta(hours=7)  # Convert to GMT+7
            # Write data to CSV
            with open(self.balance_log_file, 'a', newline='', encoding='utf-8') as f:
                writer = csv.writer(f)
                writer.writerow([
                    now.strftime('%Y-%m-%d %H:%M:%S'),  # UTC timestamp
                    gmt7_time.strftime('%Y-%m-%d %H:%M:%S'),  # GMT+7 datetime
                    f"{balance:.2f}",
                    f"{equity:.2f}",
                    f"{free_margin:.2f}",
                    f"{drawdown:.2f}",
                    f"{pnl_from_start:.2f}",
                    f"{runtime_minutes:.1f}"
                ])
            # Update last log time
            self.last_balance_log_time = now
            # Log every 10 minutes for visibility (but save every minute)
            if int(runtime_minutes) % 10 == 0:
                self.logger.info(f"📊 Balance/Equity logged - Balance: ${balance:.2f}, Equity: ${equity:.2f}, PnL: ${pnl_from_start:.2f}")
        except Exception as e:
            self.logger.error(f"Error logging balance/equity: {e}")
    def should_log_balance(self):
        """Check if it's time to log balance/equity data."""
        if not self.last_balance_log_time:
            return True
        now = datetime.now()
        time_diff = (now - self.last_balance_log_time).total_seconds()
        return time_diff >= self.balance_log_interval
    def generate_balance_chart(self, hours=24):
        """Generate balance/equity chart from CSV data using Plotly."""
        try:
            if not self.balance_log_file or not os.path.exists(self.balance_log_file):
                return None, "No balance log file found. Start the strategy to begin logging."
            # Read CSV data
            df = pd.read_csv(self.balance_log_file)
            if df.empty:
                return None, "No data found in balance log file."
            # Convert timestamp to datetime
            df['datetime'] = pd.to_datetime(df['timestamp'])
            # Filter recent data based on hours parameter
            if hours > 0:
                cutoff_time = datetime.now() - timedelta(hours=hours)
                df = df[df['datetime'] >= cutoff_time]
            if df.empty:
                return None, f"No data found in the last {hours} hours."
            # Create subplots with Plotly
            fig = make_subplots(
                rows=2, cols=1,
                subplot_titles=('Balance & Equity', 'Drawdown & PnL'),
                vertical_spacing=0.1,
                shared_xaxes=True
            )
            # Add balance and equity traces to top subplot
            fig.add_trace(
                go.Scatter(
                    x=df['datetime'], 
                    y=df['balance'],
                    mode='lines',
                    name='Balance',
                    line=dict(color='#ffffff', width=3, shape='linear'),
                    hovertemplate='<b>Balance</b><br>Time: %{x}<br>Amount: $%{y:.2f}<extra></extra>'
                ),
                row=1, col=1
            )
            fig.add_trace(
                go.Scatter(
                    x=df['datetime'], 
                    y=df['equity'],
                    mode='lines',
                    name='Equity',
                    line=dict(color='#00ff88', width=3, shape='linear'),
                    hovertemplate='<b>Equity</b><br>Time: %{x}<br>Amount: $%{y:.2f}<extra></extra>'
                ),
                row=1, col=1
            )
            # Add drawdown and PnL traces to bottom subplot
            fig.add_trace(
                go.Scatter(
                    x=df['datetime'], 
                    y=df['drawdown'],
                    mode='lines',
                    name='Drawdown',
                    line=dict(color='#ff4444', width=2, shape='linear'),
                    hovertemplate='<b>Drawdown</b><br>Time: %{x}<br>Amount: $%{y:.2f}<extra></extra>'
                ),
                row=2, col=1
            )
            fig.add_trace(
                go.Scatter(
                    x=df['datetime'], 
                    y=df['pnl_from_start'],
                    mode='lines',
                    name='PnL from Start',
                    line=dict(color='#ffaa00', width=2, shape='linear'),
                    hovertemplate='<b>PnL from Start</b><br>Time: %{x}<br>Amount: $%{y:.2f}<extra></extra>'
                ),
                row=2, col=1
            )
            # Update layout with dark theme
            fig.update_layout(
                title=dict(
                    text=f'Balance & Equity Chart (Last {hours}h)',
                    font=dict(size=18, color='#ffffff', family='Arial Black'),
                    x=0.05,
                    y=0.95
                ),
                paper_bgcolor='#2c3e50',  # Dark blue-gray background
                plot_bgcolor='#34495e',   # Slightly lighter plot area
                height=600,
                width=1000,
                showlegend=True,
                legend=dict(
                    orientation="h",
                    yanchor="bottom",
                    y=-0.15,
                    xanchor="center",
                    x=0.5,
                    font=dict(color='#ffffff', size=12),
                    bgcolor='rgba(0,0,0,0)'
                ),
                margin=dict(l=80, r=40, t=80, b=80),
                font=dict(color='#ffffff', family='Arial')
            )
            # Update axes with dark theme styling
            fig.update_yaxes(
                title_text="Amount ($)", 
                title_font=dict(color='#ffffff', size=14),
                gridcolor='#4a5a6a', 
                gridwidth=1,
                tickfont=dict(color='#ffffff', size=11),
                linecolor='#4a5a6a',
                row=1, col=1
            )
            fig.update_yaxes(
                title_text="Amount ($)", 
                title_font=dict(color='#ffffff', size=14),
                gridcolor='#4a5a6a', 
                gridwidth=1,
                tickfont=dict(color='#ffffff', size=11),
                linecolor='#4a5a6a',
                row=2, col=1
            )
            fig.update_xaxes(
                title_text="Time", 
                title_font=dict(color='#ffffff', size=14),
                gridcolor='#4a5a6a', 
                gridwidth=1,
                tickfont=dict(color='#ffffff', size=11),
                linecolor='#4a5a6a',
                row=2, col=1
            )
            # Convert to PNG using kaleido engine
            img_bytes = pio.to_image(fig, format='png', width=1000, height=600, scale=2)
            buf = io.BytesIO(img_bytes)
            buf.seek(0)
            # Generate summary stats
            latest = df.iloc[-1]
            oldest = df.iloc[0]
            duration_hours = (latest['datetime'] - oldest['datetime']).total_seconds() / 3600
            stats = (
                f"📊 <b>Balance Chart Summary</b>\n\n"
                f"• Period: {duration_hours:.1f} hours ({len(df)} data points)\n"
                f"• Current Balance: ${latest['balance']:.2f}\n"
                f"• Current Equity: ${latest['equity']:.2f}\n"
                f"• Total PnL: ${latest['pnl_from_start']:.2f}\n"
                f"• Max Drawdown: ${df['drawdown'].max():.2f}\n"
                f"• Free Margin: ${latest['free_margin']:.2f}\n\n"
                f"• Balance Range: ${df['balance'].min():.2f} - ${df['balance'].max():.2f}\n"
                f"• Equity Range: ${df['equity'].min():.2f} - ${df['equity'].max():.2f}"
            )
            return buf, stats
        except Exception as e:
            self.logger.error(f"Error generating balance chart: {e}")
            return None, f"Error generating chart: {str(e)}"
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
            self.logger.error(f"Order send failed, error: {self.mt5_api.last_error()}")
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
                        # Safety check for key being None or not a string
                        if key and isinstance(key, str):
                            side = 'BUY' if 'buy' in key.lower() else 'SELL'
                        else:
                            side = 'UNKNOWN'
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
    def run_at_index(self, symbol, amount, index, price=0):
        """
        Main grid placement logic for given index.
        Places 3 layers of buy stop and 3 layers of sell stop orders.
        """
        try:
            # Blackout check (GMT+7) - Enhanced for cycle completion with weekend support
            gmt_plus_7 = timezone(timedelta(hours=7))
            now_gmt7 = datetime.now(gmt_plus_7)
            current_hour = now_gmt7.hour
            current_weekday = now_gmt7.weekday()  # Monday=0, Sunday=6
            # Time-based blackout (2am-6am)
            time_blackout = (
                self.blackout_enabled and (
                    (self.blackout_start <= self.blackout_end and self.blackout_start <= current_hour < self.blackout_end) or
                    (self.blackout_start > self.blackout_end and (current_hour >= self.blackout_start or current_hour < self.blackout_end))
                )
            )
            # Weekend blackout (Saturday=5, Sunday=6)
            weekend_blackout = self.weekend_blackout_enabled and current_weekday in [5, 6]
            # Combined blackout condition
            in_blackout = time_blackout or weekend_blackout
            if in_blackout:
                # Check if this is continuing an existing strategy cycle
                has_active_positions = False
                has_pending_orders = False
                try:
                    # Check for active positions with our magic number
                    positions = self.mt5_api.positions_get(symbol=symbol)
                    if positions:
                        for pos in positions:
                            if getattr(pos, 'magic', None) == self.magic_number:
                                has_active_positions = True
                                break
                    # Check for pending orders with our magic number
                    orders = self.mt5_api.orders_get(symbol=symbol)
                    if orders:
                        for order in orders:
                            if getattr(order, 'magic', None) == self.magic_number:
                                has_pending_orders = True
                                break
                    # Also check our detail_orders for active entries
                    for key, val in self.detail_orders.items():
                        if val.get('status') == 'placed':
                            has_pending_orders = True
                            break
                except Exception as e:
                    self.logger.debug(f"Error checking positions/orders during blackout: {e}")
                # Allow continuation if we have active strategy elements, block new starts
                strategy_is_active = has_active_positions or has_pending_orders
                if strategy_is_active and self.blackout_allow_cycle_completion:
                    # Allow continuing current strategy cycle during blackout
                    blackout_reason = "Weekend" if weekend_blackout else f"Time {self.blackout_start:02d}:00-{self.blackout_end:02d}:00"
                    self.logger.info(f"🟡 Blackout active ({blackout_reason}) but continuing existing strategy cycle (positions: {has_active_positions}, orders: {has_pending_orders})")
                    if self.telegram_bot and price > 0:  # Only notify on order fills (price > 0), not periodic checks
                        self.telegram_bot.send_message(
                            f"🟡 Blackout active ({blackout_reason}) - continuing existing strategy cycle\n"
                            f"📊 Completing current trades until TP\n"
                            f"⏹️ No new strategy cycles will start",
                            chat_id=self.telegram_chat_id,
                        )
                else:
                    # Block new strategy starts during blackout
                    if weekend_blackout:
                        blackout_msg = f"⛔️ Weekend blackout active (Saturday-Sunday). New strategy cycles blocked, monitoring existing positions."
                        telegram_msg = (
                            f"⛔️ Weekend blackout active (Saturday-Sunday)\n"
                            f"📊 New strategy cycles suspended\n"
                            f"👁️ Still monitoring existing positions for TP/SL"
                        )
                    else:
                        blackout_msg = f"⛔️ Time-based blackout active {self.blackout_start:02d}:00-{self.blackout_end:02d}:00 GMT+7. New strategy cycles blocked, monitoring existing positions."
                        telegram_msg = (
                            f"⛔️ Time-based blackout active {self.blackout_start:02d}:00-{self.blackout_end:02d}:00 GMT+7\n"
                            f"📊 New strategy cycles suspended\n"
                            f"👁️ Still monitoring existing positions for TP/SL"
                        )
                    self.logger.info(blackout_msg)
                    if self.telegram_bot:
                        self.telegram_bot.send_message(telegram_msg, chat_id=self.telegram_chat_id)
                    return
            current_equity = self.get_current_equity()
            current_free_margin = self.get_current_free_margin()
            if current_equity < self.start_balance - self.max_reduce_balance:
                self.logger.error(f"⛔️ Current equity {current_equity} has reduced more than {self.max_reduce_balance} from start balance {self.start_balance}. Stopping further trades.")
                if self.telegram_bot:
                    self.telegram_bot.send_message(f"⛔️ Current equity {current_equity} has reduced more than {self.max_reduce_balance} from start balance {self.start_balance}. Stopping further trades.", chat_id=self.telegram_chat_id)
                return
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
    def execute_max_reduce_emergency_stop(self, symbol, current_equity):
        """Execute emergency stop when max reduce threshold is breached."""
        try:
            self.logger.critical(f"🚨 MAX REDUCE EMERGENCY STOP TRIGGERED!")
            self.logger.critical(f"Current equity: {current_equity:.2f}, Threshold: {self.max_reduce_threshold_equity:.2f}")
            
            # Record metrics
            self.max_reduce_triggered = True
            self.max_reduce_trigger_time = datetime.now()
            self.max_reduce_peak_drawdown = self.start_balance - current_equity
            
            # Close all positions
            positions_closed = self.close_all_positions(symbol)
            self.logger.info(f"Emergency stop: Closed {positions_closed} positions")
            
            # Cancel all pending orders  
            orders_cancelled = self.cancel_all_pending_orders(symbol)
            self.logger.info(f"Emergency stop: Cancelled {orders_cancelled} pending orders")
            
            # Pause the strategy
            self.bot_paused = True
            
            # Calculate loss statistics
            total_loss = self.start_balance - current_equity
            loss_percentage = (total_loss / self.start_balance) * 100 if self.start_balance > 0 else 0
            
            # Send detailed Telegram notification
            if self.telegram_bot and self.telegram_chat_id:
                msg = (
                    f"🚨 <b>MAX REDUCE EMERGENCY STOP</b>\n\n"
                    f"• Start balance: ${self.start_balance:.2f}\n"
                    f"• Current equity: ${current_equity:.2f}\n"
                    f"• Total loss: ${total_loss:.2f} ({loss_percentage:.1f}%)\n"
                    f"• Max reduce limit: ${self.max_reduce_balance:.2f}\n"
                    f"• Trigger time: {self.max_reduce_trigger_time.strftime('%Y-%m-%d %H:%M:%S')}\n\n"
                    f"✅ All positions closed\n"
                    f"✅ All pending orders cancelled\n"
                    f"✅ Strategy paused\n\n"
                    f"Send /start to resume (will reset start balance)"
                )
                self.telegram_bot.send_message(msg, chat_id=self.telegram_chat_id)
            
            self.logger.info("Max reduce emergency stop completed successfully")
            
        except Exception as e:
            self.logger.error(f"Error during max reduce emergency stop: {e}")
            if self.telegram_bot and self.telegram_chat_id:
                self.telegram_bot.send_message(
                    f"❌ Error during max reduce emergency stop: {str(e)}", 
                    chat_id=self.telegram_chat_id
                )
    def send_max_reduce_warning(self, current_equity, current_drawdown, max_allowed_drawdown):
        """Send warning when approaching max reduce limit."""
        try:
            warning_percentage = (current_drawdown / max_allowed_drawdown) * 100
            remaining_buffer = max_allowed_drawdown - current_drawdown
            
            msg = (
                f"⚠️ <b>MAX REDUCE WARNING</b>\n\n"
                f"• Approaching drawdown limit!\n"
                f"• Current equity: {current_equity:.2f}\n"
                f"• Current drawdown: {current_drawdown:.2f} ({warning_percentage:.1f}% of limit)\n"
                f"• Remaining buffer: {remaining_buffer:.2f}\n"
                f"• Emergency threshold: {self.max_reduce_threshold_equity:.2f}\n\n"
                f"🚨 Emergency stop will trigger if equity drops below threshold"
            )
            
            if self.telegram_bot and self.telegram_chat_id:
                self.telegram_bot.send_message(msg, chat_id=self.telegram_chat_id)
            
            self.logger.warning(f"MAX REDUCE WARNING: {warning_percentage:.1f}% of limit reached")
            
        except Exception as e:
            self.logger.error(f"Error sending max reduce warning: {e}")
    def check_max_reduce_threshold(self, symbol):
        """Check if current equity has breached the max reduce threshold."""
        if not self.max_reduce_enabled or self.max_reduce_triggered:
            return False
        try:
            current_equity = self.get_current_equity()  # Using actual equity for real-time monitoring
            current_drawdown = self.start_balance - current_equity
            
            # Track peak drawdown
            if current_drawdown > self.max_reduce_peak_drawdown:
                self.max_reduce_peak_drawdown = current_drawdown
            
            # Check for emergency stop
            if current_equity <= self.max_reduce_threshold_equity:
                self.execute_max_reduce_emergency_stop(symbol, current_equity)
                return True
            
            # Check for warning threshold (80% of way to limit)
            if not self.max_reduce_warning_sent:
                if self.max_reduce_use_percentage and self.max_reduce_percentage:
                    max_allowed_drawdown = self.start_balance * (self.max_reduce_percentage / 100.0)
                else:
                    max_allowed_drawdown = self.max_reduce_balance
                
                warning_drawdown = max_allowed_drawdown * self.max_reduce_warning_threshold
                
                if current_drawdown >= warning_drawdown:
                    self.send_max_reduce_warning(current_equity, current_drawdown, max_allowed_drawdown)
                    self.max_reduce_warning_sent = True
            else:
                # Check if equity has recovered enough to reset warning
                if self.max_reduce_use_percentage and self.max_reduce_percentage:
                    max_allowed_drawdown = self.start_balance * (self.max_reduce_percentage / 100.0)
                else:
                    max_allowed_drawdown = self.max_reduce_balance
                
                recovery_threshold = max_allowed_drawdown * (self.max_reduce_warning_threshold - 0.1)  # 10% recovery buffer
                
                if current_drawdown < recovery_threshold:
                    self.max_reduce_warning_sent = False  # Reset warning when equity recovers
                    self.logger.info(f"Max reduce warning reset - equity recovered to ${current_equity:.2f}")
                    
        except Exception as e:
            self.logger.error(f"Error checking max reduce threshold: {e}")
        return False
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
            self.tp_expected = trade_amount * 1000
            self.logger.info(f"✅ Connected to MT5 Account (Symbol: {symbol}, Trade Amount: {trade_amount})")
            if self.telegram_bot:
                self.telegram_bot.send_message(
                    f"✅ Connected to MT5 Account (Symbol: {symbol}, Trade Amount: {trade_amount})",
                    chat_id=self.telegram_chat_id
                )
            # Log blackout schedule
            blackout_msg = ""
            if self.blackout_enabled or self.weekend_blackout_enabled:
                blackout_msg = "🕐 Risk management blackout schedule:\n"
                if self.blackout_enabled:
                    blackout_msg += f"• Time-based: {self.blackout_start:02d}:00-{self.blackout_end:02d}:00 GMT+7 daily\n"
                if self.weekend_blackout_enabled:
                    blackout_msg += f"• Weekend: Saturday-Sunday GMT+7\n"
                blackout_msg += f"📊 New strategy cycles suspended during blackout\n"
                self.logger.info(blackout_msg)
                if self.telegram_bot:
                    self.telegram_bot.send_message(blackout_msg, chat_id=self.telegram_chat_id)
            # Get start balance
            start_balance = self.get_current_balance()
            self.start_balance = start_balance
            # Calculate max reduce threshold
            if self.max_reduce_enabled:
                if self.max_reduce_use_percentage and self.max_reduce_percentage:
                    # Percentage-based limit
                    max_reduce_amount = self.start_balance * (self.max_reduce_percentage / 100.0)
                    self.max_reduce_threshold_equity = self.start_balance - max_reduce_amount
                    self.logger.info(f"Max reduce protection enabled (percentage): {self.max_reduce_percentage}% = ${max_reduce_amount:.2f}, threshold={self.max_reduce_threshold_equity:.2f}")
                else:
                    # Fixed amount limit
                    self.max_reduce_threshold_equity = self.start_balance - self.max_reduce_balance
                    self.logger.info(f"Max reduce protection enabled (fixed): threshold={self.max_reduce_threshold_equity:.2f}, max_reduce={self.max_reduce_balance}")
            # Initialize balance/equity logging
            self.initialize_balance_log()
            # Initial grid placement
            self.run_at_index(symbol, trade_amount, index=self.current_idx, price=0)
            notified_tp = set()
            closed_pnl = 0
            idx = 0
            while True:
                # Handle Telegram commands
                if self.telegram_bot:
                    self.handle_telegram_command()
                # Enforce scheduled pause
                try:
                    if self.stop_at_datetime is not None:
                        now7 = datetime.now(timezone(timedelta(hours=7)))
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
                # Check blackout period transitions for notifications
                try:
                    if self.blackout_enabled or self.weekend_blackout_enabled:
                        gmt_plus_7 = timezone(timedelta(hours=7))
                        now_gmt7 = datetime.now(gmt_plus_7)
                        current_hour = now_gmt7.hour
                        current_weekday = now_gmt7.weekday()  # Monday=0, Sunday=6
                        # Check current blackout status
                        time_blackout = (
                            self.blackout_enabled and (
                                (self.blackout_start <= self.blackout_end and self.blackout_start <= current_hour < self.blackout_end) or
                                (self.blackout_start > self.blackout_end and (current_hour >= self.blackout_start or current_hour < self.blackout_end))
                            )
                        )
                        weekend_blackout = self.weekend_blackout_enabled and current_weekday in [5, 6]
                        currently_in_blackout = time_blackout or weekend_blackout
                        # Check for blackout period transitions
                        if currently_in_blackout and not self.in_blackout_period:
                            # Entering blackout period
                            if weekend_blackout and not time_blackout:
                                msg = f"🌙 Entering weekend blackout (Saturday-Sunday GMT+7)\n📊 New strategy cycles suspended\n👁️ Monitoring existing positions"
                            elif time_blackout and not weekend_blackout:
                                msg = f"🌙 Entering time-based blackout ({self.blackout_start:02d}:00-{self.blackout_end:02d}:00 GMT+7)\n📊 New strategy cycles suspended\n👁️ Monitoring existing positions"
                            else:
                                msg = f"🌙 Entering combined blackout (Weekend + {self.blackout_start:02d}:00-{self.blackout_end:02d}:00 GMT+7)\n📊 New strategy cycles suspended\n👁️ Monitoring existing positions"
                            self.logger.info(msg)
                            if self.telegram_bot:
                                self.telegram_bot.send_message(msg, chat_id=self.telegram_chat_id)
                            self.in_blackout_period = True
                        elif not currently_in_blackout and self.in_blackout_period:
                            # Exiting blackout period
                            day_name = now_gmt7.strftime('%A')
                            msg = f"☀️ Exiting blackout period - {day_name} {now_gmt7.strftime('%H:%M')} GMT+7\n📊 Normal strategy cycles resumed\n🕐 Next blackout: "
                            # Calculate next blackout info
                            if self.weekend_blackout_enabled and current_weekday < 5:  # Weekday, weekend coming
                                days_until_weekend = 5 - current_weekday  # Days until Saturday
                                msg += f"Weekend (in {days_until_weekend} day{'s' if days_until_weekend > 1 else ''})"
                            elif self.blackout_enabled:  # Time-based blackout info
                                if current_hour < self.blackout_start:
                                    hours_until = self.blackout_start - current_hour
                                    msg += f"{self.blackout_start:02d}:00-{self.blackout_end:02d}:00 (in {hours_until}h)"
                                else:
                                    hours_until = 24 - current_hour + self.blackout_start
                                    msg += f"{self.blackout_start:02d}:00-{self.blackout_end:02d}:00 (in {hours_until}h)"
                            self.logger.info(msg)
                            if self.telegram_bot:
                                self.telegram_bot.send_message(msg, chat_id=self.telegram_chat_id)
                            self.in_blackout_period = False
                except Exception as e:
                    self.logger.debug(f"Blackout transition check error: {e}")
                # Check max reduce emergency stop threshold
                if self.check_max_reduce_threshold(symbol):
                    continue  # Emergency stop triggered, continue to next iteration
                # Check if bot is paused
                if self.bot_paused:
                    if idx % 1000 == 0:
                        self.logger.info("Bot is paused. Waiting for /start command...")
                    time.sleep(1)
                    idx += 1
                    continue
                # Update list of open order IDs
                saved_orders = []
                for key, val in self.detail_orders.items():
                    if val.get('status') == 'placed' and val.get('order') is not None:
                        saved_orders.append(val['order'].order)
                idx += 1
                positions = self.mt5.get_positions()
                open_pnl = 0
                for pos in positions:
                    if pos.get('ticket') in saved_orders:
                        open_pnl += pos.get('profit', 0)
                # Check history for filled orders
                history = []
                now = datetime.now()
                history = self.mt5_api.history_deals_get(script_start_time - timedelta(hours=8), now + timedelta(hours=8))
                # Check if pending orders filled
                for oid in saved_orders:
                    if oid not in self.notified_filled:
                        if self.check_pending_order_filled(history, oid):
                            order_comment = None
                            order_price = 0
                            side = '?'
                            matching_key = None
                            for key, val in self.detail_orders.items():
                                order_obj = val.get('order')
                                if hasattr(order_obj, 'order') and order_obj.order == oid:
                                    self.logger.info(f"DEBUG :: Checking order_obj {order_obj} for oid {oid}")
                                    order_comment = getattr(order_obj, 'comment', None)
                                    order_price = order_obj.request.price
                                    matching_key = key
                                    break
                            # Determine side from comment or key
                            if order_comment and ('buy' in order_comment or 'sell' in order_comment):
                                side = 'BUY' if 'buy' in order_comment else 'SELL'
                                self.logger.info(f"DEBUG :: Order {oid} side determined from comment: {side}")
                            elif matching_key and '_' in matching_key:
                                # Fallback: extract side from detail_orders key
                                try:
                                    side_str = matching_key.split('_')[0]
                                    side = 'BUY' if side_str == 'buy' else ('SELL' if side_str == 'sell' else '?')
                                    if not order_comment:
                                        order_comment = matching_key  # Use key as comment if none available
                                    self.logger.info(f"DEBUG :: Order {oid} side determined from key '{matching_key}': {side} (broker doesn't provide comment)")
                                except Exception as e:
                                    self.logger.warning(f"DEBUG :: Could not parse key '{matching_key}' for order {oid}: {e}")
                            else:
                                self.logger.warning(f"DEBUG :: Could not determine side for order {oid} - comment: '{order_comment}', key: '{matching_key}'")
                            self.logger.info(f"🔥 :: {order_comment} :: Pending order filled: ID {oid} | {side} | {order_price}")
                            self.notified_filled.add(oid)
                            self.logger.info(f"Filled order IDs: {self.notified_filled}")
                            all_status_report = self.get_all_order_status_str()
                            msg = f"🔥 <b>Pending order filled - {order_comment}</b>\n"
                            msg += f"ID {oid} | {side} | {order_price:<.2f}\n\n"
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
                    if oid not in notified_tp:
                        if self.check_position_closed(oid):
                            pnl = self.pos_closed_pnl(oid)
                            closed_pnl += pnl
                            notified_tp.add(oid)
                            hit_index = None
                            hit_side = None
                            hit_tp_price = None
                            order_comment = None
                            for key, val in self.detail_orders.items():
                                order_obj = val.get('order')
                                if order_obj and hasattr(order_obj, 'order') and order_obj.order == oid:
                                    hit_tp_price = order_obj.request.tp
                                    comment = getattr(order_obj, 'comment', '') or ''
                                    order_comment = comment or key  # Fallback to key if comment is empty
                                    # Try to determine side from comment first, then from key
                                    if comment and ('buy' in comment or 'sell' in comment):
                                        if 'buy' in comment:
                                            hit_side = 'BUY'
                                        elif 'sell' in comment:
                                            hit_side = 'SELL'
                                        # Try to extract index from comment
                                        try:
                                            idx_str = comment.split('_')[-1]
                                            hit_index = int(idx_str)
                                            self.logger.info(f"DEBUG :: TP filled order {oid} side/index from comment: {hit_side}_{hit_index}")
                                        except Exception:
                                            hit_index = None
                                    else:
                                        # Fallback: extract side and index from detail_orders key
                                        # Keys are like: "buy_0", "sell_1", etc.
                                        if key and '_' in key:
                                            try:
                                                side_str, idx_str = key.split('_', 1)
                                                if side_str == 'buy':
                                                    hit_side = 'BUY'
                                                elif side_str == 'sell':
                                                    hit_side = 'SELL'
                                                hit_index = int(idx_str)
                                                self.logger.info(f"DEBUG :: TP filled order {oid} side/index from key '{key}': {hit_side}_{hit_index} (broker doesn't provide comment)")
                                            except Exception as e:
                                                self.logger.warning(f"Could not parse detail_orders key '{key}': {e}")
                                                hit_index = None
                                        else:
                                            self.logger.warning(f"DEBUG :: Could not determine side/index for TP filled order {oid} - comment: '{comment}', key: '{key}'")
                                    break
                            if hit_index is not None:
                                if hit_side == 'BUY':
                                    self.current_idx = hit_index + 1
                                elif hit_side == 'SELL':
                                    self.current_idx = hit_index - 1
                            self.logger.info(f"❤️ :: {order_comment} :: TP filled: Position ID {oid} closed | P&L: ${pnl:.2f} All Closed P&L: ${closed_pnl:.2f}")
                            self.logger.info(f"TP filled order IDs: {notified_tp}")
                            self.logger.info(f"TP filled: {hit_side} order index {self.current_idx} (ID {oid}) closed. TP price: {hit_tp_price}")
                            msg = f"❤️❤️❤️ <b>TP filled - {order_comment}</b>\n\n"
                            msg += f"<b>Position ID:</b> {oid}\n"
                            msg += f"<b>P&L:</b> ${pnl:.2f}\n"
                            msg += f"<b>All Closed P&L:</b> ${closed_pnl:.2f}\n"
                            msg += f"<b>All P&L:</b> ${closed_pnl + open_pnl:.2f}\n"
                            msg += f"\n{self.drawdown_report()}\n"
                            if self.telegram_bot:
                                self.telegram_bot.send_message(msg, chat_id=self.telegram_chat_id)
                            self.run_at_index(symbol, trade_amount, self.current_idx, price=0)
                            self.monitor_drawdown()
                            # Clean up detail_orders entry if we have valid hit_side and hit_index
                            if hit_side is not None and hit_index is not None:
                                self.logger.info(f"⚠️ :: Deleting detail_orders entry for {hit_side.lower()}_{hit_index}")
                                self.detail_orders[f"{hit_side.lower()}_{hit_index}"] = {'status': None}
                            else:
                                self.logger.warning(f"⚠️ :: Could not clean up detail_orders entry - hit_side: {hit_side}, hit_index: {hit_index}, order_comment: {order_comment}")
                if idx % 50 == 0:
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
                    run_time = datetime.now() - script_start_time
                    run_time_str = str(run_time).split('.')[0]
                    msg = (
                        f"✅✅✅✅✅ Target profit reached.\n"
                        f"Start balance: {start_balance}\n"
                        f"Current balance: {current_balance}\n"
                        f"Total PnL: {total_pnl}\n"
                        f"Session PnL: {closed_pnl + open_pnl}\n"
                        f"Run time: {run_time_str}"
                    )
                    self.logger.info(msg)
                    if self.telegram_bot:
                        self.telegram_bot.send_message(msg, chat_id=self.telegram_chat_id, pin_msg=True, disable_notification=False)
                    # Reset state
                    self.detail_orders = {key: {'status': None} for key in self.detail_orders.keys()}
                    self.notified_filled.clear()
                    notified_tp.clear()
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
                        self.tp_expected = trade_amount * 1000
                        change_msg = f"💰 <b>Trade Amount Changed</b>\n\n"
                        change_msg += f"• Previous amount: {old_amount}\n"
                        change_msg += f"• New amount (override): {trade_amount}\n"
                        change_msg += f"• New TP expected: ${self.tp_expected:.2f}\n\n"
                        change_msg += "The override is now active and will remain in effect for future runs until changed."
                        if self.telegram_bot:
                            self.telegram_bot.send_message(change_msg, chat_id=self.telegram_chat_id, disable_notification=False)
                        self.logger.info(f"Trade amount changed from {old_amount} to {trade_amount}")
                    else:
                        gmt_plus_7 = timezone(timedelta(hours=7))
                        current_time_gmt7 = datetime.now(gmt_plus_7)
                        current_hour = current_time_gmt7.hour
                        in_quiet = (
                            self.quiet_hours_enabled and (
                                (self.quiet_hours_start <= self.quiet_hours_end and self.quiet_hours_start <= current_hour <= self.quiet_hours_end) or
                                (self.quiet_hours_start > self.quiet_hours_end and (current_hour >= self.quiet_hours_start or current_hour <= self.quiet_hours_end))
                            )
                        )
                        if in_quiet:
                            trade_amount = round(self.trade_amount * self.quiet_hours_factor, 2)
                            self.tp_expected = trade_amount * 1000
                            self.logger.info(f"🕰️ Quiet-hours adjustment: trade amount {trade_amount} (factor x{self.quiet_hours_factor}) (GMT+7: {current_hour}:00)")
                            if self.telegram_bot:
                                self.telegram_bot.send_message(f"🕰️ Quiet-hours adjustment: trade amount {trade_amount} (x{self.quiet_hours_factor}) during {self.quiet_hours_start:02d}-{self.quiet_hours_end:02d} GMT+7", chat_id=self.telegram_chat_id)
                        else:
                            trade_amount = self.trade_amount
                            self.tp_expected = trade_amount * 1000
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
                    # Calculate max reduce threshold
                    if self.max_reduce_enabled:
                        self.max_reduce_threshold_equity = self.start_balance - self.max_reduce_balance
                        self.logger.info(f"Max reduce protection reset: threshold={self.max_reduce_threshold_equity:.2f}, max_reduce={self.max_reduce_balance}")
                        self.max_reduce_triggered = False  # Reset on new cycle
                    self.run_at_index(symbol, trade_amount, self.current_idx, price=0)
                # # Periodic balance/equity logging (every 1 minute)
                # if self.should_log_balance():
                #     self.log_balance_equity()
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
            # Get updates from Telegram with offset to avoid processing same updates
            offset = self.last_telegram_update_id + 1 if self.last_telegram_update_id else None
            updates = self.telegram_bot.bot.get_updates(timeout=1, offset=offset)
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
                            # Reset max reduce protection if it was triggered
                            was_max_reduce_triggered = self.max_reduce_triggered
                            if self.max_reduce_triggered:
                                self.max_reduce_triggered = False
                                new_balance = self.get_current_balance()
                                self.start_balance = new_balance
                                if self.max_reduce_enabled:
                                    self.max_reduce_threshold_equity = self.start_balance - self.max_reduce_balance
                                    self.logger.info(f"Max reduce protection reset: new start balance={self.start_balance:.2f}, threshold={self.max_reduce_threshold_equity:.2f}")
                            resume_msg = f"▶️ <b>Bot Resumed!</b>\n\n"
                            resume_msg += f"• Account: {account_number}\n"
                            resume_msg += f"• Symbol: {self.trade_symbol}\n"
                            resume_msg += f"• Trade Amount: {self.trade_amount}\n"
                            resume_msg += f"• Status: Running ✅\n\n"
                            if was_max_reduce_triggered:
                                resume_msg += f"🛡️ Max reduce protection reset with new start balance: ${self.start_balance:.2f}\n\n"
                            resume_msg += f"The bot will now resume trading operations."
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
                            stop_msg = f"⏸️ <b>Stop Requested</b>\n\n"
                            stop_msg += f"The bot will:\n"
                            stop_msg += f"1. Continue running until next target profit\n"
                            stop_msg += f"2. Close all positions when TP is reached\n"
                            stop_msg += f"3. Pause and wait for /start command\n\n"
                            stop_msg += f"Current status: Waiting for TP... 💤"
                            self.telegram_bot.send_message(stop_msg, chat_id=chat_id, disable_notification=False)
                            self.logger.info(f"Stop requested by user from chat_id: {chat_id}")
                        else:
                            already_stopped_msg = f"⏸️ Stop already requested. Bot will pause after next TP."
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
                            status_str = 'Paused ⏸️' if self.bot_paused else ('Stopping after TP ⏳' if self.stop_requested else 'Running ✅')
                            next_amount_str = f"{self.next_trade_amount}" if self.next_trade_amount else '-'
                            run_time_str = '-'
                            try:
                                if self.session_start_time:
                                    run_time = datetime.now() - self.session_start_time
                                    run_time_str = str(run_time).split('.')[0]
                            except Exception:
                                pass
                            msg = f"🤖 <b>Bot Status</b>\n\n"
                            msg += f"• Account: {login}\n"
                            msg += f"• Symbol: {self.trade_symbol}\n"
                            msg += f"• Status: {status_str}\n"
                            try:
                                if self.stop_at_datetime:
                                    msg += f"• Stop at: {self.stop_at_datetime.strftime('%Y-%m-%d %H:%M')} GMT+7\n"
                            except Exception:
                                pass
                            msg += f"• Current Index: {self.current_idx}\n"
                            msg += f"• Target Profit Threshold: ${self.tp_expected:.2f}\n\n"
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
                            msg += f"• Configured amount: {self.trade_amount}\n"
                            msg += f"• Next run override: {next_amount_str}\n\n"
                            msg += f"<b>Guards</b>\n"
                            try:
                                qh_state = 'on' if self.quiet_hours_enabled else 'off'
                                msg += f"• Quiet hours: {qh_state} ({self.quiet_hours_start:02d}-{self.quiet_hours_end:02d} x{self.quiet_hours_factor})\n"
                                # Enhanced blackout status with current state
                                if self.blackout_enabled:
                                    gmt_plus_7 = timezone(timedelta(hours=7))
                                    now_gmt7 = datetime.now(gmt_plus_7)
                                    current_hour = now_gmt7.hour
                                    currently_in_blackout = (
                                        (self.blackout_start <= self.blackout_end and self.blackout_start <= current_hour < self.blackout_end) or
                                        (self.blackout_start > self.blackout_end and (current_hour >= self.blackout_start or current_hour < self.blackout_end))
                                    )
                                    if currently_in_blackout:
                                        if current_hour < self.blackout_end or (self.blackout_start > self.blackout_end and current_hour < self.blackout_end):
                                            hours_left = (self.blackout_end - current_hour) if current_hour < self.blackout_end else (24 - current_hour + self.blackout_end)
                                        else:
                                            hours_left = self.blackout_end - current_hour
                                        msg += f"• Blackout: 🔴 ACTIVE ({self.blackout_start:02d}:00-{self.blackout_end:02d}:00) - {hours_left}h left\n"
                                    else:
                                        if current_hour < self.blackout_start:
                                            hours_until = self.blackout_start - current_hour
                                        else:
                                            hours_until = 24 - current_hour + self.blackout_start
                                        msg += f"• Blackout: 🟢 inactive ({self.blackout_start:02d}:00-{self.blackout_end:02d}:00) - starts in {hours_until}h\n"
                                else:
                                    msg += f"• Blackout: disabled\n"
                                msg += f"• Caps: maxDD={self.max_dd_threshold}, maxPos={self.max_positions}, maxOrders={self.max_orders}, maxSpread={self.max_spread}\n"
                                # Max reduce protection status
                                if self.max_reduce_enabled:
                                    current_equity = self.get_current_equity()  # Use equity for accurate drawdown
                                    current_drawdown = self.start_balance - current_equity
                                    remaining_buffer = current_equity - self.max_reduce_threshold_equity if self.max_reduce_threshold_equity else 0
                                    if self.max_reduce_triggered:
                                        msg += f"• Max reduce: 🚨 EMERGENCY STOP TRIGGERED (limit: ${self.max_reduce_balance:.2f})\n"
                                    elif remaining_buffer <= 0:
                                        msg += f"• Max reduce: ⚠️ AT THRESHOLD (limit: ${self.max_reduce_balance:.2f}, drawdown: ${current_drawdown:.2f})\n"
                                    elif remaining_buffer < 100:
                                        msg += f"• Max reduce: 🟡 NEAR LIMIT (limit: ${self.max_reduce_balance:.2f}, buffer: ${remaining_buffer:.2f})\n"
                                    else:
                                        msg += f"• Max reduce: 🛡️ PROTECTED (limit: ${self.max_reduce_balance:.2f}, buffer: ${remaining_buffer:.2f})\n"
                                else:
                                    msg += f"• Max reduce: disabled\n"
                            except Exception:
                                pass
                            msg += f"\n<b>Pattern Detection</b>\n"
                            try:
                                pd = self.check_consecutive_orders_pattern()
                                pattern_status = '🟢 Yes' if pd.get('pattern_detected') else '⚪ No'
                                msg += f"• Pattern detected: {pattern_status}\n"
                                msg += f"• Consecutive BUY pairs: {len(pd.get('consecutive_buys', []))}\n"
                                msg += f"• Consecutive SELL pairs: {len(pd.get('consecutive_sells', []))}\n"
                                msg += f"• Total filled orders: {pd.get('total_filled', 0)}\n"
                            except Exception as e_pattern:
                                msg += f"• Pattern check error: {str(e_pattern)[:50]}\n"
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
                                "• /start — Resume bot (if paused)\n"
                                "• /resume — Alias of /start\n"
                                "• /pause — Pause immediately (no new grids)\n"
                                "• /stop — Finish current cycle, pause after TP\n"
                                "• /stopat HH:MM — Schedule pause at time (GMT+7)\n"
                                "• /panic — Emergency stop (requires '/panic confirm')\n\n"
                                "<b>Configuration</b>\n"
                                "• /setamount X.XX — Set persistent override (applies after next TP)\n"
                                "• /clearamount — Remove persistent override\n"
                                "• /quiethours — Show or set quiet-hours window and factor\n\n"
                                "• /setmaxdd X — Auto-pause if drawdown exceeds X\n"
                                "• /setmaxpos N — Cap concurrent positions\n"
                                "• /setmaxorders N — Cap concurrent pending orders\n"
                                "• /setspread X — Max allowed spread\n"
                                "• /setmaxreducebalance X — Max equity reduction allowed\n"
                                "• /blackout — Show blackout status or set 2am-6am risk window\n\n"
                                "<b>Insights</b>\n"
                                "• /status — Bot and account status\n"
                                "• /drawdown — Show drawdown report\n\n"
                                "• /history N — Last N deals\n"
                                "• /pnl today|week|month — Aggregated PnL\n"
                                "• /filled — Show filled orders summary\n"
                                "• /pattern — Show consecutive filled-order pattern\n"
                                "• /balance [hours] — Generate balance/equity chart\n"
                                "• /balancelog — Show balance/equity log file info\n\n"
                                "<b>Examples</b>\n"
                                "• /setamount 0.05\n"
                                "• /stopat 21:00\n"
                                "• /setmaxdd 300\n"
                                "• /setspread 0.30\n"
                                "• /setmaxreducebalance 5000\n"
                                "• /balance 12\n"
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
                                self.telegram_bot.send_message(
                                    "⏸️ <b>Bot Paused</b>\n\nTrading is paused immediately. No new grids will be placed. Send /start or /resume to continue.",
                                    chat_id=chat_id,
                                    disable_notification=False,
                                )
                                self.logger.info("Bot paused by user command")
                            else:
                                self.telegram_bot.send_message("⏸️ Bot is already paused.", chat_id=chat_id, disable_notification=False)
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
                                self.telegram_bot.send_message(
                                    "🛑 <b>PANIC STOP executed</b>\n\nAll strategy positions closed, pending orders cancelled, and bot paused. Send /start or /resume to continue.",
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
                                tz = timezone(timedelta(hours=7))
                                now7 = datetime.now(tz)
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
                    # Blackout window
                    elif text.startswith('/blackout'):
                        try:
                            parts = text.split()
                            if len(parts) == 1:
                                # Show detailed blackout status
                                gmt_plus_7 = timezone(timedelta(hours=7))
                                now_gmt7 = datetime.now(gmt_plus_7)
                                current_hour = now_gmt7.hour
                                current_weekday = now_gmt7.weekday()  # Monday=0, Sunday=6
                                current_time_str = now_gmt7.strftime('%A %H:%M')
                                # Check current blackout status
                                time_blackout = (
                                    self.blackout_enabled and (
                                        (self.blackout_start <= self.blackout_end and self.blackout_start <= current_hour < self.blackout_end) or
                                        (self.blackout_start > self.blackout_end and (current_hour >= self.blackout_start or current_hour < self.blackout_end))
                                    )
                                )
                                weekend_blackout = self.weekend_blackout_enabled and current_weekday in [5, 6]
                                currently_in_blackout = time_blackout or weekend_blackout
                                status_icon = "🔴" if currently_in_blackout else "🟢"
                                status_text = "ACTIVE" if currently_in_blackout else "Inactive"
                                msg = f"⛔️ <b>Risk Management Blackout</b>\n\n"
                                msg += f"• Status: {status_icon} {status_text}\n"
                                msg += f"• Current time: {current_time_str} GMT+7\n\n"
                                # Show configurations
                                msg += f"<b>Configuration:</b>\n"
                                if self.blackout_enabled:
                                    msg += f"• Time-based: {self.blackout_start:02d}:00-{self.blackout_end:02d}:00 GMT+7 daily\n"
                                if self.weekend_blackout_enabled:
                                    msg += f"• Weekend: Saturday-Sunday GMT+7\n"
                                if not self.blackout_enabled and not self.weekend_blackout_enabled:
                                    msg += f"• All blackouts disabled\n"
                                msg += f"\n"
                                # Current status and timing
                                if currently_in_blackout:
                                    action_info = "📊 New strategy cycles SUSPENDED\n🔄 Existing cycles continue until TP\n👁️ Monitoring all positions for TP/SL"
                                else:
                                    action_info = "📊 Normal strategy cycles active\n🕐 Blackouts suspend new cycles, existing complete"
                                msg += f"<b>Current Effect:</b>\n{action_info}\n\n"
                                msg += f"<b>Controls:</b>\n"
                                msg += f"• /blackout time HH-HH - Set time window\n"
                                msg += f"• /blackout weekend on/off - Toggle weekend\n"
                                msg += f"• /blackout off - Disable all blackouts"
                                self.telegram_bot.send_message(msg, chat_id=chat_id, disable_notification=False)
                            elif len(parts) >= 2:
                                command = parts[1].lower()
                                if command == "weekend" and len(parts) >= 3:
                                    # /blackout weekend on/off
                                    toggle = parts[2].lower()
                                    if toggle in ["on", "off"]:
                                        self.weekend_blackout_enabled = (toggle == "on")
                                        status_icon = "🔴" if self.weekend_blackout_enabled else "🔘"
                                        status_text = "Enabled" if self.weekend_blackout_enabled else "Disabled"
                                        msg = f"⛔️ <b>Weekend Blackout Updated</b>\n\n"
                                        msg += f"• Status: {status_icon} {status_text}\n"
                                        msg += f"• Effect: {'Saturday-Sunday blocked' if self.weekend_blackout_enabled else 'Weekend trading allowed'}\n\n"
                                        msg += f"<b>Combined Blackout Status:</b>\n"
                                        if self.blackout_enabled:
                                            msg += f"• Time-based: {self.blackout_start:02d}:00-{self.blackout_end:02d}:00 GMT+7 daily\n"
                                        if self.weekend_blackout_enabled:
                                            msg += f"• Weekend: Saturday-Sunday GMT+7\n"
                                        if not self.blackout_enabled and not self.weekend_blackout_enabled:
                                            msg += f"• All blackouts disabled\n"
                                        self.telegram_bot.send_message(msg, chat_id=chat_id, disable_notification=False)
                                    else:
                                        msg = "❌ Invalid weekend setting. Use: /blackout weekend on or /blackout weekend off"
                                        self.telegram_bot.send_message(msg, chat_id=chat_id, disable_notification=False)
                                elif command == "time" and len(parts) >= 3:
                                    # /blackout time HH-HH
                                    time_range = parts[2]
                                    if '-' in time_range and time_range.count('-') == 1:
                                        try:
                                            start_str, end_str = time_range.split('-')
                                            start_hour = int(start_str)
                                            end_hour = int(end_str)
                                            if 0 <= start_hour <= 23 and 0 <= end_hour <= 23:
                                                self.blackout_start = start_hour
                                                self.blackout_end = end_hour
                                                self.blackout_enabled = True
                                                msg = f"⛔️ <b>Time Blackout Updated</b>\n\n"
                                                msg += f"• Status: 🔴 Enabled\n"
                                                msg += f"• Window: {self.blackout_start:02d}:00-{self.blackout_end:02d}:00 GMT+7 daily\n\n"
                                                msg += f"<b>Combined Blackout Status:</b>\n"
                                                if self.blackout_enabled:
                                                    msg += f"• Time-based: {self.blackout_start:02d}:00-{self.blackout_end:02d}:00 GMT+7 daily\n"
                                                if self.weekend_blackout_enabled:
                                                    msg += f"• Weekend: Saturday-Sunday GMT+7\n"
                                                self.telegram_bot.send_message(msg, chat_id=chat_id, disable_notification=False)
                                            else:
                                                msg = "❌ Invalid time range. Hours must be 0-23. Example: /blackout time 02-06"
                                                self.telegram_bot.send_message(msg, chat_id=chat_id, disable_notification=False)
                                        except ValueError:
                                            msg = "❌ Invalid time format. Use: /blackout time HH-HH (Example: /blackout time 02-06)"
                                            self.telegram_bot.send_message(msg, chat_id=chat_id, disable_notification=False)
                                    else:
                                        msg = "❌ Invalid time format. Use: /blackout time HH-HH (Example: /blackout time 02-06)"
                                        self.telegram_bot.send_message(msg, chat_id=chat_id, disable_notification=False)
                                elif command == "off":
                                    # /blackout off - disable all blackouts
                                    self.blackout_enabled = False
                                    self.weekend_blackout_enabled = False
                                    msg = f"⛔️ <b>All Blackouts Disabled</b>\n\n"
                                    msg += f"• Status: 🔘 Disabled\n"
                                    msg += f"• Normal grid placement active 24/7\n\n"
                                    msg += f"<b>Controls:</b>\n"
                                    msg += f"• /blackout time HH-HH - Enable time window\n"
                                    msg += f"• /blackout weekend on - Enable weekend blackout"
                                    self.telegram_bot.send_message(msg, chat_id=chat_id, disable_notification=False)
                                elif '-' in parts[1] and parts[1].count('-') == 1:
                                    # Legacy format: /blackout HH-HH (backward compatibility)
                                    try:
                                        start_str, end_str = parts[1].split('-')
                                        start_hour = int(start_str)
                                        end_hour = int(end_str)
                                        if 0 <= start_hour <= 23 and 0 <= end_hour <= 23:
                                            self.blackout_start = start_hour
                                            self.blackout_end = end_hour
                                            self.blackout_enabled = True
                                            msg = f"⛔️ <b>Time Blackout Updated</b>\n\n"
                                            msg += f"• Status: 🔴 Enabled\n"
                                            msg += f"• Window: {self.blackout_start:02d}:00-{self.blackout_end:02d}:00 GMT+7 daily\n\n"
                                            msg += f"<b>Combined Blackout Status:</b>\n"
                                            if self.blackout_enabled:
                                                msg += f"• Time-based: {self.blackout_start:02d}:00-{self.blackout_end:02d}:00 GMT+7 daily\n"
                                            if self.weekend_blackout_enabled:
                                                msg += f"• Weekend: Saturday-Sunday GMT+7\n"
                                            self.telegram_bot.send_message(msg, chat_id=chat_id, disable_notification=False)
                                        else:
                                            msg = "❌ Invalid time range. Hours must be 0-23. Example: /blackout 02-06"
                                            self.telegram_bot.send_message(msg, chat_id=chat_id, disable_notification=False)
                                    except ValueError:
                                        msg = "❌ Invalid format. Use: /blackout HH-HH or /blackout time HH-HH or /blackout weekend on/off"
                                        self.telegram_bot.send_message(msg, chat_id=chat_id, disable_notification=False)
                                else:
                                    msg = "❌ Unknown command. Available:\n"
                                    msg += "• /blackout - Show status\n"
                                    msg += "• /blackout time HH-HH - Set time window\n" 
                                    msg += "• /blackout weekend on/off - Toggle weekend\n"
                                    msg += "• /blackout off - Disable all"
                                    self.telegram_bot.send_message(msg, chat_id=chat_id, disable_notification=False)
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
                    # Max reduce protection
                    elif text.startswith('/maxreduce'):
                        try:
                            parts = text.split()
                            if len(parts) == 1:
                                # Show current max reduce status
                                status_icon = "🛡️" if self.max_reduce_enabled else "🔘"
                                status_text = "Enabled" if self.max_reduce_enabled else "Disabled"
                                
                                msg = f"🛡️ <b>Max Reduce Protection</b>\n\n"
                                msg += f"• Status: {status_icon} {status_text}\n"
                                msg += f"• Start balance: ${self.start_balance:.2f}\n"
                                
                                # Show current mode and limits
                                if self.max_reduce_use_percentage and self.max_reduce_percentage:
                                    msg += f"• Mode: Percentage-based ({self.max_reduce_percentage}%)\n"
                                    max_reduce_amount = self.start_balance * (self.max_reduce_percentage / 100.0)
                                    msg += f"• Max reduce limit: ${max_reduce_amount:.2f}\n"
                                else:
                                    msg += f"• Mode: Fixed amount\n"
                                    msg += f"• Max reduce limit: ${self.max_reduce_balance:.2f}\n"
                                
                                if self.max_reduce_enabled:
                                    current_equity = self.get_current_equity()
                                    drawdown = self.start_balance - current_equity
                                    msg += f"• Emergency threshold: ${self.max_reduce_threshold_equity:.2f}\n"
                                    msg += f"• Current equity: ${current_equity:.2f}\n"
                                    msg += f"• Current drawdown: ${drawdown:.2f}\n"
                                    msg += f"• Peak drawdown: ${self.max_reduce_peak_drawdown:.2f}\n"
                                    msg += f"• Remaining buffer: ${current_equity - self.max_reduce_threshold_equity:.2f}\n"
                                    
                                    if self.max_reduce_triggered:
                                        msg += f"\n🚨 Emergency stop TRIGGERED"
                                        if self.max_reduce_trigger_time:
                                            msg += f" at {self.max_reduce_trigger_time.strftime('%H:%M:%S')}"
                                        msg += f"\n"
                                    elif current_equity <= self.max_reduce_threshold_equity:
                                        msg += f"\n⚠️ At emergency threshold!\n"
                                
                                msg += f"\n<b>Controls:</b>\n"
                                msg += f"• /maxreduce on/off - Toggle protection\n"
                                msg += f"• /maxreduce set AMOUNT - Set fixed amount limit\n"
                                msg += f"• /maxreduce percent XX - Set percentage limit\n"
                                msg += f"• /maxreduce mode fixed/percent - Switch mode\n"
                                msg += f"• /maxreduce reset - Reset after emergency stop"
                                
                                self.telegram_bot.send_message(msg, chat_id=chat_id, disable_notification=False)
                            
                            elif len(parts) >= 2:
                                command = parts[1].lower()
                                
                                if command in ["on", "off"]:
                                    # Toggle max reduce protection
                                    self.max_reduce_enabled = (command == "on")
                                    
                                    if self.max_reduce_enabled and not self.max_reduce_triggered:
                                        self.max_reduce_threshold_equity = self.start_balance - self.max_reduce_balance
                                    
                                    status_icon = "🛡️" if self.max_reduce_enabled else "🔘"
                                    status_text = "Enabled" if self.max_reduce_enabled else "Disabled"
                                    
                                    msg = f"🛡️ <b>Max Reduce Protection {status_text}</b>\n\n"
                                    if self.max_reduce_enabled:
                                        msg += f"• Max reduce limit: {self.max_reduce_balance:.2f}\n"
                                        msg += f"• Emergency threshold: {self.max_reduce_threshold_equity:.2f}"
                                    else:
                                        msg += f"• Protection disabled - no emergency stop"
                                    
                                    self.telegram_bot.send_message(msg, chat_id=chat_id, disable_notification=False)
                                
                                elif command == "set" and len(parts) >= 3:
                                    # Set max reduce amount
                                    try:
                                        new_amount = float(parts[2])
                                        if new_amount <= 0:
                                            raise ValueError("Amount must be positive")
                                        
                                        self.max_reduce_balance = new_amount
                                        self.max_reduce_use_percentage = False  # Switch to fixed mode
                                        if self.max_reduce_enabled and not self.max_reduce_triggered:
                                            self.max_reduce_threshold_equity = self.start_balance - self.max_reduce_balance
                                        
                                        msg = f"🛡️ <b>Max Reduce Limit Updated</b>\n\n"
                                        msg += f"• Mode: Fixed amount\n"
                                        msg += f"• New limit: ${self.max_reduce_balance:.2f}\n"
                                        if self.max_reduce_enabled:
                                            msg += f"• Emergency threshold: ${self.max_reduce_threshold_equity:.2f}"
                                        else:
                                            msg += f"• Protection currently disabled"
                                        
                                        self.telegram_bot.send_message(msg, chat_id=chat_id, disable_notification=False)
                                    except ValueError as ve:
                                        self.telegram_bot.send_message(f"❌ Invalid amount: {ve}", chat_id=chat_id, disable_notification=False)
                                
                                elif command == "percent" and len(parts) >= 3:
                                    # Set percentage-based limit
                                    try:
                                        new_percentage = float(parts[2])
                                        if new_percentage <= 0 or new_percentage >= 100:
                                            raise ValueError("Percentage must be between 0 and 100")
                                        
                                        self.max_reduce_percentage = new_percentage
                                        self.max_reduce_use_percentage = True  # Switch to percentage mode
                                        if self.max_reduce_enabled and not self.max_reduce_triggered:
                                            max_reduce_amount = self.start_balance * (self.max_reduce_percentage / 100.0)
                                            self.max_reduce_threshold_equity = self.start_balance - max_reduce_amount
                                        
                                        msg = f"🛡️ <b>Max Reduce Limit Updated</b>\n\n"
                                        msg += f"• Mode: Percentage-based\n"
                                        msg += f"• New percentage: {self.max_reduce_percentage}%\n"
                                        msg += f"• Calculated limit: ${max_reduce_amount:.2f}\n"
                                        if self.max_reduce_enabled:
                                            msg += f"• Emergency threshold: ${self.max_reduce_threshold_equity:.2f}"
                                        else:
                                            msg += f"• Protection currently disabled"
                                        
                                        self.telegram_bot.send_message(msg, chat_id=chat_id, disable_notification=False)
                                    except ValueError as ve:
                                        self.telegram_bot.send_message(f"❌ Invalid percentage: {ve}", chat_id=chat_id, disable_notification=False)
                                
                                elif command == "mode" and len(parts) >= 3:
                                    # Switch between fixed and percentage modes
                                    mode = parts[2].lower()
                                    if mode in ["fixed", "percent", "percentage"]:
                                        old_mode = "percentage" if self.max_reduce_use_percentage else "fixed"
                                        self.max_reduce_use_percentage = (mode in ["percent", "percentage"])
                                        
                                        # Recalculate threshold if enabled
                                        if self.max_reduce_enabled and not self.max_reduce_triggered:
                                            if self.max_reduce_use_percentage and self.max_reduce_percentage:
                                                max_reduce_amount = self.start_balance * (self.max_reduce_percentage / 100.0)
                                                self.max_reduce_threshold_equity = self.start_balance - max_reduce_amount
                                                limit_display = f"{self.max_reduce_percentage}% (${max_reduce_amount:.2f})"
                                            else:
                                                self.max_reduce_threshold_equity = self.start_balance - self.max_reduce_balance
                                                limit_display = f"${self.max_reduce_balance:.2f}"
                                        else:
                                            limit_display = "N/A (protection disabled or triggered)"
                                        
                                        new_mode = "percentage" if self.max_reduce_use_percentage else "fixed"
                                        msg = f"🛡️ <b>Max Reduce Mode Changed</b>\n\n"
                                        msg += f"• Changed from: {old_mode} → {new_mode}\n"
                                        msg += f"• Current limit: {limit_display}\n"
                                        if self.max_reduce_enabled and not self.max_reduce_triggered:
                                            msg += f"• Emergency threshold: ${self.max_reduce_threshold_equity:.2f}"
                                        
                                        self.telegram_bot.send_message(msg, chat_id=chat_id, disable_notification=False)
                                    else:
                                        self.telegram_bot.send_message("❌ Invalid mode. Use: fixed or percent", chat_id=chat_id, disable_notification=False)
                                
                                elif command == "reset":
                                    # Reset after emergency stop
                                    self.max_reduce_triggered = False
                                    self.max_reduce_warning_sent = False  # Reset warning flag
                                    self.max_reduce_peak_drawdown = 0  # Reset peak drawdown
                                    self.max_reduce_trigger_time = None  # Clear trigger time
                                    start_balance = self.get_current_balance()
                                    self.start_balance = start_balance
                                    
                                    if self.max_reduce_enabled:
                                        if self.max_reduce_use_percentage and self.max_reduce_percentage:
                                            max_reduce_amount = self.start_balance * (self.max_reduce_percentage / 100.0)
                                            self.max_reduce_threshold_equity = self.start_balance - max_reduce_amount
                                        else:
                                            self.max_reduce_threshold_equity = self.start_balance - self.max_reduce_balance
                                    
                                    msg = f"🛡️ <b>Max Reduce Protection Reset</b>\n\n"
                                    msg += f"• New start balance: {self.start_balance:.2f}\n"
                                    msg += f"• Max reduce limit: {self.max_reduce_balance:.2f}\n"
                                    if self.max_reduce_enabled:
                                        msg += f"• Emergency threshold: {self.max_reduce_threshold_equity:.2f}"
                                    
                                    self.telegram_bot.send_message(msg, chat_id=chat_id, disable_notification=False)
                                
                                else:
                                    msg = "❌ Unknown command. Available:\n"
                                    msg += "• /maxreduce - Show status\n"
                                    msg += "• /maxreduce on/off - Toggle protection\n"
                                    msg += "• /maxreduce set AMOUNT - Set limit\n"
                                    msg += "• /maxreduce reset - Reset protection"
                                    self.telegram_bot.send_message(msg, chat_id=chat_id, disable_notification=False)
                                    
                        except Exception as e:
                            self.logger.error(f"Error handling /maxreduce: {e}")
                            self.telegram_bot.send_message("❌ Failed to configure max reduce protection.", chat_id=chat_id, disable_notification=False)
                    # History
                    elif text.startswith('/history'):
                        try:
                            parts = text.split()
                            n = int(parts[1]) if len(parts) == 2 else 10
                            tz = timezone(timedelta(hours=7))
                            now = datetime.now(tz)
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
                                    ts = datetime.fromtimestamp(t, tz).strftime('%Y-%m-%d %H:%M') if isinstance(t, (int, float)) else str(t)
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
                            tz = timezone(timedelta(hours=7))
                            now = datetime.now(tz)
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
                    # Balance chart generation
                    elif text.startswith('/balance'):
                        try:
                            # Parse hours parameter (default 24)
                            parts = text.split()
                            hours = 24
                            if len(parts) > 1:
                                try:
                                    hours = int(parts[1])
                                    hours = max(1, min(hours, 168))  # Limit 1-168 hours (1 week)
                                except ValueError:
                                    self.telegram_bot.send_message("❌ Invalid hours format. Use: /balance [hours]\nExample: /balance 12", chat_id=chat_id, disable_notification=False)
                                    continue
                            # Generate chart
                            chart_buffer, stats_msg = self.generate_balance_chart(hours)
                            if chart_buffer:
                                # Send chart image
                                chart_buffer.name = f"balance_chart_{hours}h.png"
                                self.telegram_bot.send_photo(chat_id=chat_id, photo=chart_buffer, caption=stats_msg, parse_mode='HTML')
                                chart_buffer.close()
                            else:
                                self.telegram_bot.send_message(f"📊 {stats_msg}", chat_id=chat_id, disable_notification=False)
                        except Exception as e:
                            self.logger.error(f"Error handling /balance: {e}")
                            self.telegram_bot.send_message("❌ Failed to generate balance chart.", chat_id=chat_id, disable_notification=False)
                    # Balance log file information
                    elif text.strip().lower() == '/balancelog':
                        try:
                            if self.balance_log_file:
                                # Get file size and record count
                                file_size = 0
                                record_count = 0
                                try:
                                    if os.path.exists(self.balance_log_file):
                                        file_size = os.path.getsize(self.balance_log_file)
                                        with open(self.balance_log_file, 'r', encoding='utf-8') as f:
                                            record_count = sum(1 for _ in f) - 1  # Subtract header row
                                except Exception:
                                    pass
                                # Calculate logging duration
                                duration_minutes = record_count  # Since we log every minute
                                hours = duration_minutes // 60
                                minutes = duration_minutes % 60
                                msg = (
                                    "📊 <b>Balance Log Information</b>\n\n"
                                    f"• Log file: {os.path.basename(self.balance_log_file)}\n"
                                    f"• Directory: <code>data/balances/</code>\n"
                                    f"• Full path: <code>{self.balance_log_file}</code>\n"
                                    f"• File size: {file_size:,} bytes\n"
                                    f"• Records: {record_count:,} entries\n"
                                    f"• Duration: {hours}h {minutes}m\n"
                                    f"• Interval: Every 1 minute\n\n"
                                    f"<b>CSV Columns:</b>\n"
                                    f"• timestamp (UTC)\n"
                                    f"• datetime_gmt7\n"
                                    f"• balance, equity, free_margin\n"
                                    f"• drawdown, pnl_from_start\n"
                                    f"• session_runtime_minutes"
                                )
                            else:
                                msg = "📊 <b>Balance Log</b>\n\nNo log file created yet. The log will be initialized in <code>data/balances/</code> when the strategy starts running."
                            self.telegram_bot.send_message(msg, chat_id=chat_id, disable_notification=False)
                        except Exception as e:
                            self.logger.error(f"Error handling /balancelog: {e}")
                            self.telegram_bot.send_message("❌ Failed to get balance log info.", chat_id=chat_id, disable_notification=False)
        except Exception as e:
            # self.logger.error(f"Error in handle_telegram_command: {e}")
            pass
