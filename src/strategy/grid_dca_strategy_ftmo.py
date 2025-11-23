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
        self.bot_paused = True  # Start paused for FTMO - wait for /start command
        self.stop_requested = False
        self.next_trade_amount = None
        self.user_started = False  # Track if user has started the bot
        
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
        
        # Prop firm equity protection
        self.min_equity_threshold = None  # Minimum equity to maintain (prop firm protection)
        self.equity_emergency_triggered = False  # Track if emergency stop was triggered
        self.equity_threshold_required = True  # FTMO requires setting minimum equity before trading
        
        # Blackout window
        self.blackout_enabled = False
        self.blackout_start = 0
        self.blackout_end = 0
        
        # Trading halt (news/volatility protection)
        self.trading_halt_enabled = True  # Enabled by default for safety
        self.trading_halt_start = 4   # 4:00 AM
        self.trading_halt_end = 6     # 6:30 AM (we'll handle minutes separately)
        self.trading_halt_end_minutes = 30  # Additional 30 minutes for 6:30
        self.trading_halt_active = False  # Track if currently in halt period
        self.trading_halt_notified = False  # Prevent spam notifications
        
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
            # Blackout check (GMT+7)
            gmt_plus_7 = timezone(timedelta(hours=7))
            now_gmt7 = datetime.now(gmt_plus_7)
            current_hour = now_gmt7.hour
            in_blackout = (
                self.blackout_enabled and (
                    (self.blackout_start <= self.blackout_end and self.blackout_start <= current_hour <= self.blackout_end) or
                    (self.blackout_start > self.blackout_end and (current_hour >= self.blackout_start or current_hour <= self.blackout_end))
                )
            )
            if in_blackout:
                self.logger.info(f"⛔️ Blackout window active {self.blackout_start:02d}-{self.blackout_end:02d} GMT+7. Skipping grid build.")
                if self.telegram_bot:
                    self.telegram_bot.send_message(
                        f"⛔️ Blackout window active {self.blackout_start:02d}-{self.blackout_end:02d} GMT+7. Skipping grid build.",
                        chat_id=self.telegram_chat_id,
                    )
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
    
    def run(self):
        """
        Main strategy execution loop.
        Monitors filled orders, TP reached, handles Telegram commands, and manages trade cycles.
        For FTMO: Starts paused and waits for user /start command.
        """
        self.logger.info(f"=== Grid DCA Strategy for {self.trade_symbol} (FTMO Mode) ===")
        script_start_time = datetime.now()
        self.session_start_time = script_start_time
        
        try:
            symbol = self.trade_symbol
            trade_amount = self.trade_amount
            self.tp_expected = trade_amount * 1000
            
            self.logger.info(f"✅ Connected to MT5 Account (Symbol: {symbol}, Trade Amount: {trade_amount})")
            self.logger.info(f"🔶 FTMO Mode: Bot is PAUSED - Send /start to begin trading")
            
            if self.telegram_bot:
                initial_msg = (
                    f"✅ <b>Connected to MT5 Account</b>\n\n"
                    f"📊 <b>Trading Setup:</b>\n"
                    f"• Symbol: {symbol}\n"
                    f"• Trade Amount: {trade_amount}\n\n"
                    f"🔶 <b>FTMO Mode Active</b>\n"
                    f"• Bot is currently PAUSED\n"
                    f"• Send /start to begin trading\n"
                    f"• All risk management features active\n\n"
                    f"⚠️ <b>Ready to trade - awaiting your command!</b>"
                )
                self.telegram_bot.send_message(initial_msg, chat_id=self.telegram_chat_id)
            
            # Get start balance
            start_balance = self.get_current_balance()
            self.start_balance = start_balance
            
            # Initialize balance/equity logging
            self.initialize_balance_log()
            notified_tp = set()
            closed_pnl = 0
            
            idx = 0
            while True:
                # Handle Telegram commands
                if self.telegram_bot:
                    self.handle_telegram_command()
                
                # PROP FIRM EQUITY PROTECTION - Check first (highest priority)
                try:
                    if self.min_equity_threshold is not None and not self.equity_emergency_triggered:
                        current_equity = self.get_current_equity()
                        if current_equity <= self.min_equity_threshold:
                            self.equity_emergency_triggered = True
                            self.bot_paused = True
                            
                            emergency_msg = (
                                f"🚨🔴🚨 <b>PROP FIRM EMERGENCY STOP</b> 🚨🔴🚨\n\n"
                                f"┌─────────────────────────────┐\n"
                                f"│  🏛️ <b>EQUITY PROTECTION ALERT</b>  │\n"
                                f"└─────────────────────────────┘\n\n"
                                f"💰 <b>Account Status:</b>\n"
                                f"┣━ Current Equity: <code>${current_equity:.2f}</code>\n"
                                f"┣━ Minimum Threshold: <code>${self.min_equity_threshold:.2f}</code>\n"
                                f"┗━ ⚠️ Violation: <code>-${self.min_equity_threshold - current_equity:.2f}</code>\n\n"
                                f"🛑 <b>EMERGENCY ACTIONS EXECUTED:</b>\n"
                                f"┣━ ❌ Closing ALL positions immediately\n"
                                f"┣━ 🗑️ Cancelling ALL pending orders\n"
                                f"┗━ ⏹️ Strategy STOPPED permanently\n\n"
                                f"⚠️ <b>MANUAL INTERVENTION REQUIRED</b>\n"
                                f"🔒 Bot will remain STOPPED until manually restarted."
                            )
                            
                            self.logger.critical(f"PROP FIRM EMERGENCY: Equity ${current_equity:.2f} <= ${self.min_equity_threshold:.2f}")
                            
                            # Close all positions and cancel all orders immediately
                            try:
                                self.close_all_positions(symbol)
                                self.cancel_all_pending_orders(symbol)
                                self.logger.warning("Emergency closure: All positions closed and orders cancelled")
                            except Exception as close_error:
                                self.logger.error(f"Error during emergency closure: {close_error}")
                            
                            # Send emergency notification
                            if self.telegram_bot:
                                self.telegram_bot.send_message(emergency_msg, chat_id=self.telegram_chat_id, pin_msg=True, disable_notification=False)
                            
                            # Continue to pause state - bot will remain stopped
                except Exception as e:
                    self.logger.debug(f"Equity protection check error: {e}")
                
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

                # Automatic Trading Halt Check (4AM-6:30AM GMT+7 News Protection)
                try:
                    if self.trading_halt_enabled:
                        gmt_plus_7 = timezone(timedelta(hours=7))
                        now_gmt7 = datetime.now(gmt_plus_7)
                        current_hour = now_gmt7.hour
                        current_minute = now_gmt7.minute
                        
                        # Check if we're in trading halt period (4:00 AM to 6:30 AM)
                        in_halt_period = (
                            (current_hour == self.trading_halt_start) or  # 4:00-4:59 AM
                            (self.trading_halt_start < current_hour < self.trading_halt_end) or  # 5:00-5:59 AM  
                            (current_hour == self.trading_halt_end and current_minute < self.trading_halt_end_minutes)  # 6:00-6:29 AM
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
                                    f"┣━ 🚫 No new orders: <code>04:00-06:30 GMT+7</code>\n"
                                    f"┣━ ⏰ Current time: <code>{current_hour:02d}:{current_minute:02d} GMT+7</code>\n"
                                    f"┗━ 🔄 Auto-resume at: <code>06:30 GMT+7</code>\n\n"
                                    f"✅ <b>Active positions and orders remain untouched</b>\n"
                                    f"🛡️ <b>Risk management continues normally</b>"
                                )
                                self.logger.info(f"🛑 Trading halt ACTIVATED at {current_hour:02d}:{current_minute:02d} GMT+7")
                            else:
                                halt_msg = (
                                    f"✅ <b>Trading Halt DEACTIVATED</b>\n\n"
                                    f"┌─────────────────────────────┐\n"
                                    f"│  🟢 <b>Normal Trading Resumed</b>  │\n"
                                    f"└─────────────────────────────┘\n\n"
                                    f"🕐 <b>Status:</b>\n"
                                    f"┣━ ✅ New orders: <code>ENABLED</code>\n"
                                    f"┣━ ⏰ Current time: <code>{current_hour:02d}:{current_minute:02d} GMT+7</code>\n"
                                    f"┗━ 📈 Strategy: <code>ACTIVE</code>\n\n"
                                    f"🚀 <b>Bot ready to create new orders based on strategy</b>"
                                )
                                self.logger.info(f"✅ Trading halt DEACTIVATED at {current_hour:02d}:{current_minute:02d} GMT+7 - Normal trading resumed")
                            
                            if self.telegram_bot:
                                self.telegram_bot.send_message(halt_msg, chat_id=self.telegram_chat_id, disable_notification=False)
                except Exception as e:
                    self.logger.debug(f"Trading halt time check error: {e}")

                # Check if bot is paused
                if self.bot_paused:
                    if idx % 1000 == 0:
                        if self.equity_emergency_triggered:
                            self.logger.critical("PROP FIRM EMERGENCY STOP: Equity threshold breached - bot permanently stopped")
                        elif self.equity_threshold_required and self.min_equity_threshold is None:
                            self.logger.info("FTMO Mode: Equity protection setup required. Use /setminequity command...")
                        elif not self.user_started:
                            self.logger.info("FTMO Mode: Bot is paused. Send /start command to begin trading...")
                        else:
                            self.logger.info("Bot is paused. Send /start command to resume...")
                    time.sleep(1)
                    idx += 1
                    continue

                # Check various halt conditions before placing new orders
                skip_new_orders = False
                skip_reason = ""
                
                if self.trading_halt_active:
                    skip_new_orders = True
                    skip_reason = "trading halt (4AM-6:30AM news protection)"
                elif self.blackout_paused:
                    skip_new_orders = True
                    skip_reason = "blackout window"
                elif self.quiet_hours_enabled and self.is_quiet_hours():
                    # Note: quiet hours reduces trade amount but doesn't skip orders
                    pass
                
                # Skip order placement if any halt condition is active
                if skip_new_orders:
                    if idx % 100 == 0:  # Log every 100 iterations to avoid spam
                        self.logger.info(f"Skipping new orders due to {skip_reason}")
                    time.sleep(1)
                    idx += 1
                    continue
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
                            # Check if emergency stop was triggered
                            if self.equity_emergency_triggered:
                                emergency_msg = (
                                    f"🚨 <b>CANNOT RESTART - EQUITY EMERGENCY</b> 🚨\n\n"
                                    f"┌─────────────────────────────┐\n"
                                    f"│   ❌ <b>RESTART BLOCKED</b>      │\n"
                                    f"└─────────────────────────────┘\n\n"
                                    f"🚫 Bot cannot restart - equity threshold breached!\n\n"
                                    f"💰 <b>Account Status:</b>\n"
                                    f"┣━ ⚠️ Minimum Required: <code>${self.min_equity_threshold:.2f}</code>\n"
                                    f"┗━ 💎 Current Equity: <code>${self.get_current_equity():.2f}</code>\n\n"
                                    f"🔧 <b>Recovery Process:</b>\n"
                                    f"┣━ 1️⃣ Ensure equity > <code>${self.min_equity_threshold:.2f}</code>\n"
                                    f"┣━ 2️⃣ Use <code>/resetequity</code> to clear emergency\n"
                                    f"┗━ 3️⃣ Use <code>/start</code> to resume trading\n\n"
                                    f"⚠️ <i>Manual account review recommended!</i>"
                                )
                                self.telegram_bot.send_message(emergency_msg, chat_id=chat_id, disable_notification=False)
                                return
                            
                            # Check if minimum equity threshold is set (FTMO requirement)
                            if self.equity_threshold_required and self.min_equity_threshold is None:
                                required_msg = (
                                    f"🏛️ <b>PROP FIRM SETUP REQUIRED</b>\n\n"
                                    f"⚠️ <b>Cannot start trading without equity protection!</b>\n\n"
                                    f"🛡️ <b>Required Setup:</b>\n"
                                    f"You must set a minimum equity threshold before trading.\n\n"
                                    f"💰 <b>Current Account:</b>\n"
                                    f"• Current Equity: ${self.get_current_equity():.2f}\n\n"
                                    f"🔧 <b>Setup Steps:</b>\n"
                                    f"1. Use: /setminequity AMOUNT\n"
                                    f"2. Example: /setminequity 9600\n"
                                    f"3. Then use: /start to begin trading\n\n"
                                    f"ℹ️ <b>Recommended thresholds:</b>\n"
                                    f"• $10k account: /setminequity 9600\n"
                                    f"• $25k account: /setminequity 24000\n"
                                    f"• $50k account: /setminequity 48000\n"
                                    f"• $100k account: /setminequity 96000"
                                )
                                self.telegram_bot.send_message(required_msg, chat_id=chat_id, disable_notification=False)
                                return
                            
                            # Check if minimum equity threshold is set (FTMO requirement)
                            if self.equity_threshold_required and self.min_equity_threshold is None:
                                required_msg = (
                                    f"🏛️ <b>PROP FIRM SETUP REQUIRED</b>\n\n"
                                    f"⚠️ <b>Cannot start trading without equity protection!</b>\n\n"
                                    f"🛡️ <b>Required Setup:</b>\n"
                                    f"You must set a minimum equity threshold before trading.\n\n"
                                    f"💰 <b>Current Account:</b>\n"
                                    f"• Current Equity: ${self.get_current_equity():.2f}\n\n"
                                    f"🔧 <b>Setup Steps:</b>\n"
                                    f"1. Use: /setminequity AMOUNT\n"
                                    f"2. Example: /setminequity 9600\n"
                                    f"3. Then use: /start to begin trading\n\n"
                                    f"ℹ️ <b>Recommended thresholds:</b>\n"
                                    f"• $10k account: /setminequity 9600\n"
                                    f"• $25k account: /setminequity 24000\n"
                                    f"• $50k account: /setminequity 48000\n"
                                    f"• $100k account: /setminequity 96000"
                                )
                                self.telegram_bot.send_message(required_msg, chat_id=chat_id, disable_notification=False)
                                return
                            
                            self.bot_paused = False
                            self.stop_requested = False
                            
                            # Check if this is the first start (initial grid placement needed)
                            if not self.user_started:
                                self.user_started = True
                                current_equity = self.get_current_equity()
                                welcome_msg = (
                                    f"🏛️ <b>FTMO STRATEGY ACTIVATED</b> 🏛️\n\n"
                                    f"┌─────────────────────────────┐\n"
                                    f"│      🚀 <b>TRADING COMMENCED</b>      │\n"
                                    f"└─────────────────────────────┘\n\n"
                                    f"📊 <b>Account Overview:</b>\n"
                                    f"┣━ 🟢 Status: <b>Active & Ready</b>\n"
                                    f"┣━ 💰 Current Equity: <code>${current_equity:.2f}</code>\n"
                                    f"┗━ ⚙️ Magic Number: <code>{self.magic_number}</code>\n\n"
                                    f"🛡️ <b>Protection Status:</b>\n"
                                )
                                
                                if self.min_equity_threshold:
                                    welcome_msg += (
                                        f"┣━ ✅ Equity Guard: <code>${self.min_equity_threshold:.2f}</code>\n"
                                        f"┗━ 🟢 Emergency Stop: <b>Configured</b>\n\n"
                                    )
                                else:
                                    welcome_msg += (
                                        f"┣━ ⚠️ No equity protection set!\n"
                                        f"┣━ 📝 Recommend: <code>/setminequity 9600</code>\n"
                                        f"┗━ 🔴 <b>High risk for prop accounts!</b>\n\n"
                                    )
                                
                                welcome_msg += (
                                    f"🎯 <b>Next Actions:</b>\n"
                                    f"┣━ ⚡ Initial grid placement starting...\n"
                                    f"┗━ 📱 Use <code>/status</code> to monitor progress\n\n"
                                    f"<i>🤖 Bot is now actively monitoring the market</i>"
                                )
                                self.telegram_bot.send_message(welcome_msg, chat_id=chat_id, disable_notification=False)
                                self.logger.info(f"FTMO Bot started by user command from chat_id: {chat_id}")
                                
                                # Place initial grid now that user has started the bot
                                try:
                                    symbol = self.trade_symbol
                                    trade_amount = self.trade_amount
                                    self.run_at_index(symbol, trade_amount, index=self.current_idx, price=0)
                                    self.logger.info("Initial grid placement completed after user start command")
                                except Exception as e:
                                    self.logger.error(f"Error placing initial grid after start: {e}")
                                    if self.telegram_bot:
                                        self.telegram_bot.send_message(f"⚠️ Error placing initial grid: {e}", chat_id=chat_id)
                            else:
                                # Regular resume
                                resume_msg = f"▶️ <b>Bot Resumed!</b>\n\n"
                                resume_msg += f"• Account: {account_number}\n"
                                resume_msg += f"• Symbol: {self.trade_symbol}\n"
                                resume_msg += f"• Trade Amount: {self.trade_amount}\n"
                                resume_msg += f"• Status: Running ✅\n\n"
                                resume_msg += f"The bot will now resume trading operations."
                                self.telegram_bot.send_message(resume_msg, chat_id=chat_id, disable_notification=False)
                                self.logger.info(f"Bot resumed by user command from chat_id: {chat_id}")
                        else:
                            welcome_msg = f"👋 <b>Hello!</b>\n\n"
                            welcome_msg += f"• Account: {account_number}\n\n"
                            welcome_msg += f"Welcome to the Grid DCA Trading Bot for {self.trade_symbol}!\n\n"
                            welcome_msg += f"<b>Bot Status:</b>\n"
                            welcome_msg += f"• Strategy: Grid DCA (FTMO Mode)\n"
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

                            if self.bot_paused:
                                if self.equity_emergency_triggered:
                                    status_str = 'STOPPED (Equity Emergency) 🚨⏹️'
                                elif self.equity_threshold_required and self.min_equity_threshold is None:
                                    status_str = 'Paused (Equity Setup Required) 🏛️⏸️'
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

                            msg = f"🏛️ <b>FTMO STRATEGY DASHBOARD</b> 🏛️\n\n"
                            msg += f"┌─────────────────────────────┐\n"
                            msg += f"│        📊 <b>LIVE STATUS</b>        │\n"
                            msg += f"└─────────────────────────────┘\n\n"
                            msg += f"🎛️ <b>Bot Configuration:</b>\n"
                            msg += f"┣━ 🏛️ Account: <code>{login}</code>\n"
                            msg += f"┣━ 📈 Symbol: <code>{self.trade_symbol}</code>\n"
                            msg += f"┣━ ⚡ Status: <b>{status_str}</b>\n"
                            try:
                                if self.stop_at_datetime:
                                    msg += f"┣━ ⏰ Scheduled Stop: <code>{self.stop_at_datetime.strftime('%Y-%m-%d %H:%M')} GMT+7</code>\n"
                            except Exception:
                                pass
                            msg += f"┣━ 📊 Current Index: <code>{self.current_idx}</code>\n"
                            msg += f"┗━ 🎯 TP Threshold: <code>${self.tp_expected:.2f}</code>\n\n"
                            msg += f"⏱️ <b>Session Info:</b>\n"
                            msg += f"┗━ 🕐 Runtime: <code>{run_time_str}</code>\n\n"
                            msg += f"💰 <b>Account Summary:</b>\n"
                            msg += f"┣━ 💵 Balance: <code>${balance:.2f}</code>\n"
                            msg += f"┣━ 💎 Equity: <code>${equity:.2f}</code>\n"
                            msg += f"┗━ 💳 Free Margin: <code>${free_margin:.2f}</code>\n\n"
                            msg += f"📊 <b>Trading Activity:</b>\n"
                            msg += f"┣━ 📍 Open Positions: <code>{pos_count}</code>\n"
                            msg += f"┣━ ⏳ Pending Orders: <code>{order_count}</code>\n"
                            msg += f"┗━ 💹 Strategy P&L: <code>${open_pnl:.2f}</code>\n\n"
                            msg += f"🎯 <b>Trade Configuration:</b>\n"
                            msg += f"┣━ 📊 Base Amount: <code>{self.trade_amount}</code>\n"
                            msg += f"┗━ 🔄 Next Override: <code>{next_amount_str}</code>\n\n"
                            msg += f"🛡️ <b>Risk Management:</b>\n"
                            try:
                                qh_icon = "🟢" if self.quiet_hours_enabled else "⚪"
                                bl_icon = "🟢" if self.blackout_enabled else "⚪"
                                halt_icon = "🟢" if self.trading_halt_enabled else "⚪"
                                halt_status = "🛑 ACTIVE" if self.trading_halt_active else "⚪ Inactive"
                                msg += f"┣━ 🕰️ Quiet Hours: {qh_icon} ({self.quiet_hours_start:02d}-{self.quiet_hours_end:02d} x{self.quiet_hours_factor})\n"
                                msg += f"┣━ ⛔ Blackout: {bl_icon} ({self.blackout_start:02d}-{self.blackout_end:02d})\n"
                                msg += f"┣━ 🛑 Trading Halt: {halt_icon} (04:00-06:30) - {halt_status}\n"
                                msg += f"┣━ 🎛️ Limits: DD={self.max_dd_threshold} | Pos={self.max_positions} | Orders={self.max_orders} | Spread={self.max_spread}\n"
                                msg += f"┣━ 💼 Max Reduce: <code>${self.max_reduce_balance:.2f}</code>\n"
                                if self.min_equity_threshold:
                                    emergency_icon = "🚨" if self.equity_emergency_triggered else "🟢"
                                    msg += f"┗━ 🏛️ Prop Protection: <code>${self.min_equity_threshold:.2f}</code> ({emergency_icon})\n"
                                elif self.equity_threshold_required:
                                    msg += f"┗━ 🏛️ Prop Protection: 🔴 <b>NOT SET (Required!)</b>\n"
                                else:
                                    msg += f"┗━ 🏛️ Prop Protection: ⚪ Disabled\n"
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
                                "🏛️ <b>FTMO STRATEGY COMMANDS</b> 🏛️\n\n"
                                "┌─────────────────────────────┐\n"
                                "│       📖 <b>COMMAND GUIDE</b>       │\n"
                                "└─────────────────────────────┘\n\n"
                                "🎮 <b>Bot Control:</b>\n"
                                "┣━ 🚀 /start — Start trading or resume\n"
                                "┣━ ▶️ /resume — Alias of /start\n"
                                "┣━ ⏸️ /pause — Pause immediately\n"
                                "┣━ ⏹️ /stop — Finish cycle, then pause\n"
                                "┣━ ⏰ /stopat HH:MM — Schedule pause\n"
                                "┗━ 🆘 /panic — Emergency stop\n\n"
                                "⚙️ <b>Configuration:</b>\n"
                                "┣━ 💰 /setamount X.XX — Override trade amount\n"
                                "┣━ 🧹 /clearamount — Remove override\n"
                                "┣━ 🕰️ /quiethours — Set quiet window\n"
                                "┣━ 🛑 /tradinghalt — Control news protection\n"
                                "┣━ 📉 /setmaxdd X — Set max drawdown\n"
                                "┣━ 📊 /setmaxpos N — Cap positions\n"
                                "┣━ 📋 /setmaxorders N — Cap orders\n"
                                "┣━ 📏 /setspread X — Max spread\n"
                                "┣━ 💼 /setmaxreducebalance X — Equity limit\n"
                                "┗━ ⛔ /blackout — Set blackout window\n\n"
                                "🏛️ <b>Prop Firm Protection:</b>\n"
                                "┣━ 🛡️ /setminequity X — Set equity guard\n"
                                "┗━ 🔄 /resetequity — Reset emergency\n\n"
                                "📊 <b>Analytics & Status:</b>\n"
                                "┣━ 📱 /status — Full bot status\n"
                                "┣━ 📉 /drawdown — Drawdown report\n"
                                "┣━ 📚 /history N — Recent deals\n"
                                "┣━ 💹 /pnl — P&L summary\n"
                                "┣━ ✅ /filled — Filled orders\n"
                                "┣━ 🧩 /pattern — Pattern analysis\n"
                                "┣━ 📈 /balance — Equity chart\n"
                                "┗━ 📄 /balancelog — Log info\n\n"
                                "💡 <b>Quick Examples:</b>\n"
                                "┣━ 💰 <code>/setamount 0.05</code>\n"
                                "┣━ ⏰ <code>/stopat 21:00</code>\n"
                                "┣━ 🛑 <code>/tradinghalt on</code>\n"
                                "┣━ 📉 <code>/setmaxdd 300</code>\n"
                                "┣━ 📏 <code>/setspread 0.30</code>\n"
                                "┣━ 💼 <code>/setmaxreducebalance 5000</code>\n"
                                "┣━ 🛡️ <code>/setminequity 9600</code>\n"
                                "┣━ 📈 <code>/balance 12</code>\n"
                                "┗━ 🆘 <code>/panic confirm</code>\n\n"
                                "<i>🏛️ Professional trading for prop firm accounts</i>"
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
                    
                    # Handle /setminequity command (prop firm protection)
                    elif text.startswith('/setminequity'):
                        try:
                            parts = text.split()
                            if len(parts) == 2:
                                new_threshold = float(parts[1])
                                if new_threshold > 0:
                                    old_threshold = self.min_equity_threshold
                                    self.min_equity_threshold = new_threshold
                                    self.equity_threshold_required = False  # Mark as configured
                                    current_equity = self.get_current_equity()
                                    success_msg = (
                                        f"🏛️ <b>PROP FIRM PROTECTION SET</b> 🏛️\n\n"
                                        f"┌─────────────────────────────┐\n"
                                        f"│    ✅ <b>EQUITY GUARD ACTIVE</b>     │\n"
                                        f"└─────────────────────────────┘\n\n"
                                        f"📊 <b>Configuration Update:</b>\n"
                                        f"┣━ 📉 Previous: <code>{'${:.2f}'.format(old_threshold) if old_threshold else 'Not set'}</code>\n"
                                        f"┣━ 📈 New Threshold: <code>${new_threshold:.2f}</code>\n"
                                        f"┗━ 💰 Current Equity: <code>${current_equity:.2f}</code>\n\n"
                                        f"🚨 <b>Emergency Protection Details:</b>\n"
                                        f"┣━ ⚠️ Trigger Level: <code>${new_threshold:.2f}</code>\n"
                                        f"┣━ 🔒 Auto-close ALL positions\n"
                                        f"┣━ 🗑️ Cancel ALL pending orders\n"
                                        f"┗━ ⏹️ STOP strategy permanently\n\n"
                                        f"✅ <b>Status: Ready for Trading!</b>\n"
                                        f"🚀 Use <code>/start</code> if bot is paused\n\n"
                                        f"<i>🛡️ Your prop firm account is now protected!</i>"
                                    )
                                    self.telegram_bot.send_message(success_msg, chat_id=chat_id, disable_notification=False)
                                    self.logger.info(f"Prop firm equity protection set to ${new_threshold:.2f}")
                                    
                                    # Immediate check if current equity is already below threshold
                                    if current_equity <= new_threshold:
                                        warning_msg = (
                                            f"⚠️ <b>WARNING: Current equity already below threshold!</b>\n\n"
                                            f"• Current: ${current_equity:.2f}\n"
                                            f"• Threshold: ${new_threshold:.2f}\n\n"
                                            f"Emergency stop will trigger immediately when trading resumes."
                                        )
                                        self.telegram_bot.send_message(warning_msg, chat_id=chat_id, disable_notification=False)
                                else:
                                    error_msg = "❌ Threshold must be greater than 0.\nExample: /setminequity 9600"
                                    self.telegram_bot.send_message(error_msg, chat_id=chat_id, disable_notification=False)
                            else:
                                help_msg = (
                                    f"🏛️ <b>Set Minimum Equity Threshold</b>\n\n"
                                    f"Usage: /setminequity AMOUNT\n"
                                    f"Emergency stop when equity drops to this level.\n\n"
                                    f"Examples:\n"
                                    f"• /setminequity 9600 (stop at $9,600)\n"
                                    f"• /setminequity 4800 (stop at $4,800)\n\n"
                                    f"Current threshold: ${self.min_equity_threshold:.2f}" if self.min_equity_threshold else "Current threshold: Not set\n"
                                    f"Current equity: ${self.get_current_equity():.2f}"
                                )
                                self.telegram_bot.send_message(help_msg, chat_id=chat_id, disable_notification=False)
                        except ValueError:
                            error_msg = "❌ Invalid number format.\nUsage: /setminequity AMOUNT\nExample: /setminequity 9600"
                            self.telegram_bot.send_message(error_msg, chat_id=chat_id, disable_notification=False)
                        except Exception as e:
                            self.logger.error(f"Error in /setminequity command: {e}")
                    
                    # Handle /resetequity command (reset emergency state)
                    elif text == '/resetequity':
                        if self.equity_emergency_triggered:
                            current_equity = self.get_current_equity()
                            if self.min_equity_threshold and current_equity <= self.min_equity_threshold:
                                error_msg = (
                                    f"❌ <b>Cannot reset - equity still below threshold</b>\n\n"
                                    f"• Current Equity: ${current_equity:.2f}\n"
                                    f"• Minimum Threshold: ${self.min_equity_threshold:.2f}\n"
                                    f"• Need to add: ${self.min_equity_threshold - current_equity:.2f}\n\n"
                                    f"Please ensure equity is above ${self.min_equity_threshold:.2f} before resetting."
                                )
                                self.telegram_bot.send_message(error_msg, chat_id=chat_id, disable_notification=False)
                            else:
                                self.equity_emergency_triggered = False
                                reset_msg = (
                                    f"✅ <b>Equity Emergency State Reset</b>\n\n"
                                    f"• Emergency stop cleared\n"
                                    f"• Current Equity: ${current_equity:.2f}\n"
                                    f"• Minimum Threshold: ${self.min_equity_threshold:.2f}\n\n"
                                    f"🟢 You can now use /start to resume trading.\n\n"
                                    f"⚠️ Emergency protection remains active!"
                                )
                                self.telegram_bot.send_message(reset_msg, chat_id=chat_id, disable_notification=False)
                                self.logger.info("Equity emergency state reset by user")
                        else:
                            info_msg = "ℹ️ No equity emergency to reset. Bot is operating normally."
                            self.telegram_bot.send_message(info_msg, chat_id=chat_id, disable_notification=False)

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

                    # Trading halt window
                    elif text.startswith('/tradinghalt'):
                        try:
                            parts = text.split()
                            if len(parts) == 1:
                                status = 'ON' if self.trading_halt_enabled else 'OFF'
                                current_status = '🛑 ACTIVE' if self.trading_halt_active else '⚪ Inactive'
                                now_local = datetime.now()
                                info_msg = (
                                    f"🛑 <b>TRADING HALT CONFIGURATION</b>\n\n"
                                    f"📊 <b>Current Status:</b>\n"
                                    f"┣━ 🔧 Feature: <b>{status}</b>\n"
                                    f"┣━ 📅 Schedule: <code>04:00-06:30 GMT+7</code>\n"
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
                                    f"🛑 No new orders during 04:00-06:30 GMT+7\n"
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
                                    f"🛑 Prevents new orders 04:00-06:30", 
                                    chat_id=chat_id, disable_notification=False
                                )
                        except Exception as e:
                            self.logger.error(f"Error handling /tradinghalt: {e}")
                            self.telegram_bot.send_message("❌ Failed to configure trading halt.", chat_id=chat_id, disable_notification=False)

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
