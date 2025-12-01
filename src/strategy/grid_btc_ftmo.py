"""
Grid BTC Strategy - Buy-Only Grid Strategy for BTC
Simple grid strategy with buy orders only, fixed TP, and telegram notifications.
"""

import logging
import time
import json
import os
from datetime import datetime, timedelta, timezone


class GridBTCStrategy:
    """
    BTC Grid Strategy with:
    - Buy orders only
    - Fixed 100 pip TP
    - No SL
    - 75 pip grid spacing
    - 0.01 lot volume
    - Duplicate order prevention
    - Telegram notifications
    """
    
    DEFAULT_MAGIC_NUMBER = 234003
    GRID_SPACING = 75.0  # 75 pips
    VOLUME = 0.01  # Fixed volume
    TP_DISTANCE = 100.0  # 100 pips TP
    MAX_ORDERS = 6  # 3 above + 3 below current price
    
    def __init__(self, config_file_path, mt5_connection, telegram_bot=None, logger=None):
        """
        Initialize BTC grid strategy.
        
        Args:
            config_file_path: Path to BTC configuration file
            mt5_connection: MT5Connection instance
            telegram_bot: TelegramBot instance (optional)
            logger: logging.Logger instance (optional)
        """
        self.mt5 = mt5_connection
        self.mt5_api = mt5_connection.mt5  # Direct access to MT5 API
        self.telegram_bot = telegram_bot
        self.logger = logger or logging.getLogger(__name__)
        
        # Load configuration
        self.config = self._load_config(config_file_path)
        
        # Strategy state
        self.is_running = False
        self.is_paused = False
        self.symbol = self.config.get('trading', {}).get('trade_symbol', 'BTCUSD')
        self.magic_number = self.DEFAULT_MAGIC_NUMBER
        self.telegram_chat_id = self.config.get('telegram', {}).get('chat_id')
        
        # Order tracking
        self.placed_order_prices = set()  # Track prices to prevent duplicates
        self.active_orders = {}  # Track active orders
        
        self.logger.info(f"🟨 GridBTCStrategy initialized for {self.symbol}")
    
    def _load_config(self, config_file_path):
        """Load configuration from JSON file."""
        try:
            with open(config_file_path, 'r') as f:
                return json.load(f)
        except Exception as e:
            self.logger.error(f"❌ Failed to load config from {config_file_path}: {e}")
            return {}
    
    def start(self):
        """Start the BTC grid strategy."""
        self.is_running = True
        self.is_paused = False
        self.logger.info("🚀 BTC Grid Strategy started")
        
        if self.telegram_bot:
            self.telegram_bot.send_message(
                f"🚀 <b>BTC Grid Strategy Started</b>\n\n"
                f"• Symbol: <code>{self.symbol}</code>\n"
                f"• Grid Spacing: <code>{self.GRID_SPACING} pips</code>\n"
                f"• Volume: <code>{self.VOLUME}</code>\n"
                f"• TP Distance: <code>{self.TP_DISTANCE} pips</code>\n"
                f"• Strategy: <b>Buy Only</b>",
                chat_id=self.telegram_chat_id
            )
        
        self._place_initial_grid()
    
    def stop(self):
        """Stop the strategy and close all orders."""
        self.is_running = False
        self.logger.info("⛔ BTC Grid Strategy stopped")
        
        # Close all pending orders
        self._close_all_orders()
        
        if self.telegram_bot:
            self.telegram_bot.send_message(
                "⛔ <b>BTC Grid Strategy Stopped</b>\n\n"
                "All pending orders have been closed.",
                chat_id=self.telegram_chat_id
            )
    
    def pause(self):
        """Pause the strategy."""
        self.is_paused = True
        self.logger.info("⏸️ BTC Grid Strategy paused")
        
        if self.telegram_bot:
            self.telegram_bot.send_message(
                "⏸️ <b>BTC Grid Strategy Paused</b>",
                chat_id=self.telegram_chat_id
            )
    
    def resume(self):
        """Resume the strategy."""
        self.is_paused = False
        self.logger.info("▶️ BTC Grid Strategy resumed")
        
        if self.telegram_bot:
            self.telegram_bot.send_message(
                "▶️ <b>BTC Grid Strategy Resumed</b>",
                chat_id=self.telegram_chat_id
            )
        
        self._place_initial_grid()
    
    def run(self):
        """Main strategy loop."""
        iteration_count = 0
        
        while self.is_running:
            try:
                if self.is_paused:
                    time.sleep(1)
                    continue
                
                # Check for filled orders and maintain grid
                self._check_filled_orders()
                self._maintain_grid()
                
                time.sleep(5)  # 5-second loop
                iteration_count += 1
                
                # Log status every minute
                if iteration_count % 12 == 0:  # 12 * 5 seconds = 60 seconds
                    self._log_status()
                
            except Exception as e:
                self.logger.error(f"❌ Error in strategy loop: {e}")
                time.sleep(10)
    
    def _place_initial_grid(self):
        """Place initial grid of buy orders."""
        try:
            # Get current price
            tick = self.mt5_api.symbol_info_tick(self.symbol)
            if not tick:
                self.logger.error(f"❌ Failed to get tick for {self.symbol}")
                return
            
            current_price = tick.bid
            point_value = self._get_point_value()
            
            self.logger.info(f"📊 Current BTC price: {current_price:.2f}, Point value: {point_value}")
            
            # Calculate grid levels (3 above, 3 below current price)
            grid_levels = []
            for i in range(-3, 4):  # -3, -2, -1, 0, 1, 2, 3
                if i == 0:
                    continue  # Skip current price level
                
                price_level = current_price + (i * self.GRID_SPACING * point_value)
                grid_levels.append({
                    'price': price_level,
                    'level': i,
                    'type': 'buy_limit' if i < 0 else 'buy_stop'  # Below = limit, Above = stop
                })
            
            self.logger.info(f"📊 Calculated {len(grid_levels)} grid levels:")
            for level in grid_levels:
                self.logger.info(f"   Level {level['level']:2d}: {level['price']:8.2f} ({level['type']})")
            
            # Place buy orders (both limit and stop)
            placed_count = 0
            for level_info in grid_levels:
                if self._place_buy_order(level_info['price'], level_info['type']):
                    placed_count += 1
            
            self.logger.info(f"📊 Initial grid placed: {placed_count}/{len(grid_levels)} orders successful")
            
        except Exception as e:
            self.logger.error(f"❌ Error placing initial grid: {e}")
    
    def _place_buy_order(self, price, order_type='buy_limit'):
        """Place a buy order at specified price."""
        try:
            # Check if order already exists at this price
            if self._order_exists_at_price(price):
                return False
            
            # Get current market price for validation
            tick = self.mt5_api.symbol_info_tick(self.symbol)
            if not tick:
                self.logger.error("Failed to get current market price")
                return False
            
            current_ask = tick.ask
            current_bid = tick.bid
            
            # Determine MT5 order type and validate price
            if order_type == 'buy_limit':
                # Buy limit must be below current ask
                if price >= current_ask:
                    self.logger.warning(f"⚠️ Buy limit price {price:.2f} must be below current ask {current_ask:.2f}")
                    return False
                mt5_order_type = self.mt5_api.ORDER_TYPE_BUY_LIMIT
                order_name = "Buy Limit"
            else:  # buy_stop
                # Buy stop must be above current ask
                if price <= current_ask:
                    self.logger.warning(f"⚠️ Buy stop price {price:.2f} must be above current ask {current_ask:.2f}")
                    return False
                mt5_order_type = self.mt5_api.ORDER_TYPE_BUY_STOP
                order_name = "Buy Stop"
            
            # Calculate TP price
            tp_price = price + (self.TP_DISTANCE * self._get_point_value())
            
            self.logger.info(f"📋 Placing {order_name}: Price={price:.2f}, Ask={current_ask:.2f}, TP={tp_price:.2f}")
            
            # Place buy order
            request = {
                "action": self.mt5_api.TRADE_ACTION_PENDING,
                "symbol": self.symbol,
                "volume": self.VOLUME,
                "type": mt5_order_type,
                "price": price,
                "tp": tp_price,
                "sl": 0.0,  # No SL
                "magic": self.magic_number,
                "comment": f"BTC Grid {order_name}",
                "type_time": self.mt5_api.ORDER_TIME_GTC,
            }
            
            result = self.mt5_api.order_send(request)
            if result and result.retcode == self.mt5_api.TRADE_RETCODE_DONE:
                self.placed_order_prices.add(round(price, 2))  # Round to 2 decimals for BTC
                self.active_orders[result.order] = {
                    'price': price,
                    'tp': tp_price,
                    'type': order_type,
                    'timestamp': time.time()
                }
                
                self.logger.info(f"✅ {order_name} placed at {price:.5f}, TP: {tp_price:.5f}")
                
                # Send telegram notification for new order
                if self.telegram_bot:
                    self.telegram_bot.send_message(
                        f"📋 <b>New {order_name} Placed</b>\n\n"
                        f"• Symbol: <code>{self.symbol}</code>\n"
                        f"• Entry Price: <code>{price:.2f}</code>\n"
                        f"• TP Target: <code>{tp_price:.2f}</code>\n"
                        f"• Volume: <code>{self.VOLUME}</code>\n"
                        f"• Type: <b>{order_name}</b>",
                        chat_id=self.telegram_chat_id
                    )
                
                return True
            else:
                error_msg = result.comment if result else "Unknown error"
                self.logger.error(f"❌ Failed to place {order_name} at {price:.5f}: {error_msg}")
                return False
                
        except Exception as e:
            self.logger.error(f"❌ Error placing buy order: {e}")
            return False
    
    def _check_filled_orders(self):
        """Check for filled orders and send notifications."""
        try:
            # Get current positions
            positions = self.mt5_api.positions_get(symbol=self.symbol)
            
            # Get current orders
            current_orders = self.mt5_api.orders_get(symbol=self.symbol)
            current_order_ids = {order.ticket for order in current_orders} if current_orders else set()
            
            # Check for filled orders (orders that are no longer pending)
            filled_orders = []
            for order_id, order_info in list(self.active_orders.items()):
                if order_id not in current_order_ids:
                    # Order was filled or cancelled
                    filled_orders.append((order_id, order_info))
                    
                    # Remove from tracking
                    del self.active_orders[order_id]
                    if 'price' in order_info:
                        self.placed_order_prices.discard(round(order_info['price'], 2))  # Round to 2 decimals
            
            # Send notifications for filled orders
            for order_id, order_info in filled_orders:
                self._notify_order_filled(order_id, order_info)
            
            # Check for TP hits
            self._check_tp_filled(positions)
            
        except Exception as e:
            self.logger.error(f"❌ Error checking filled orders: {e}")
    
    def _check_tp_filled(self, positions):
        """Check for TP fills and send notifications."""
        try:
            if not positions:
                return
            
            # Get deals to check for TP fills
            from_date = datetime.now() - timedelta(minutes=5)
            deals = self.mt5_api.history_deals_get(symbol=self.symbol, date_from=from_date)
            
            if deals:
                for deal in deals:
                    if deal.type == self.mt5_api.DEAL_TYPE_SELL and deal.reason == self.mt5_api.DEAL_REASON_TP:
                        # TP was hit
                        profit = deal.profit
                        price = deal.price
                        
                        self.logger.info(f"🎯 TP Hit! Price: {price:.5f}, Profit: ${profit:.2f}")
                        
                        if self.telegram_bot:
                            self.telegram_bot.send_message(
                                f"🎯 <b>Take Profit Hit!</b>\n\n"
                                f"• Symbol: <code>{self.symbol}</code>\n"
                                f"• TP Price: <code>{price:.5f}</code>\n"
                                f"• Profit: <code>${profit:.2f}</code>\n"
                                f"• Volume: <code>{deal.volume}</code>",
                                chat_id=self.telegram_chat_id
                            )
                        
                        # Grid will be maintained by _maintain_grid() method
                        self.logger.info("🔄 TP hit - grid will be maintained on next cycle")
            
        except Exception as e:
            self.logger.error(f"❌ Error checking TP fills: {e}")
    
    def _notify_order_filled(self, order_id, order_info):
        """Send notification for filled order."""
        try:
            price = order_info.get('price', 0)
            tp = order_info.get('tp', 0)
            
            self.logger.info(f"📈 Order filled: {order_id} at {price:.5f}")
            
            if self.telegram_bot:
                self.telegram_bot.send_message(
                    f"📈 <b>Buy Order Filled!</b>\n\n"
                    f"• Symbol: <code>{self.symbol}</code>\n"
                    f"• Entry Price: <code>{price:.5f}</code>\n"
                    f"• TP Target: <code>{tp:.5f}</code>\n"
                    f"• Volume: <code>{self.VOLUME}</code>",
                    chat_id=self.telegram_chat_id
                )
        
        except Exception as e:
            self.logger.error(f"❌ Error sending order notification: {e}")
    
    def _maintain_grid(self):
        """Maintain grid with dynamic expansion when orders fill."""
        try:
            # Get current price
            tick = self.mt5_api.symbol_info_tick(self.symbol)
            if not tick:
                return
            
            current_price = tick.ask
            point_value = self._get_point_value()
            
            # Get current orders and positions
            current_orders = self.mt5_api.orders_get(symbol=self.symbol)
            current_positions = self.mt5_api.positions_get(symbol=self.symbol)
            
            # Track occupied grid levels
            occupied_levels = set()
            
            # Check existing orders
            if current_orders:
                for order in current_orders:
                    if order.magic == self.magic_number:
                        order_price = round(order.price_open, 2)
                        # Determine grid level based on distance from current price
                        distance = round((order_price - current_price) / (self.GRID_SPACING * point_value))
                        occupied_levels.add(distance)
            
            # Check existing positions  
            active_position_level = None
            if current_positions:
                for position in current_positions:
                    if position.magic == self.magic_number:
                        position_price = round(position.price_open, 2)
                        # Determine grid level
                        distance = round((position_price - current_price) / (self.GRID_SPACING * point_value))
                        occupied_levels.add(distance)
                        active_position_level = distance
                        self.logger.info(f"🎯 Active position at level {distance}: ${position_price:.2f}")
            
            # Define required grid levels based on active positions
            required_levels = [-3, -2, -1, 1, 2, 3]  # Base 6 levels
            
            # Dynamic expansion logic
            if active_position_level == 1:  # Position +1 filled
                required_levels.extend([0, 4])  # Add level 0 (limit) and level 4 (stop)
                self.logger.info("📈 Position +1 active - expanding grid to levels 0 and +4")
                
            elif active_position_level == -1:  # Position -1 filled  
                required_levels.extend([0, -4])  # Add level 0 (stop) and level -4 (limit)
                self.logger.info("📉 Position -1 active - expanding grid to levels 0 and -4")
                
            elif active_position_level == 2:  # Position +2 filled
                required_levels.extend([0, 1, 4, 5])  # Fill gaps and expand
                self.logger.info("📈 Position +2 active - expanding grid")
                
            elif active_position_level == -2:  # Position -2 filled
                required_levels.extend([0, -1, -4, -5])  # Fill gaps and expand  
                self.logger.info("📉 Position -2 active - expanding grid")
                
            elif active_position_level == 3:  # Position +3 filled (strong upward breakout)
                required_levels.extend([0, 1, 4, 5, 6])  # Fill gaps and extend range
                self.logger.info("🚀 Position +3 active - strong breakout, expanding grid to +6")
                
            elif active_position_level == -3:  # Position -3 filled (strong downward breakout)
                required_levels.extend([0, -1, -4, -5, -6])  # Fill gaps and extend range
                self.logger.info("📉 Position -3 active - strong breakdown, expanding grid to -6")
            
            # Remove duplicates
            required_levels = list(set(required_levels))
            
            # Place missing orders
            placed = 0
            for level in required_levels:
                if level in occupied_levels:
                    continue
                    
                if level == 0:
                    # Skip level 0 as it's too close to current price
                    continue
                
                target_price = current_price + (level * self.GRID_SPACING * point_value)
                
                # Skip if too close to current price
                if abs(target_price - current_price) < (self.GRID_SPACING * point_value * 0.3):
                    continue
                
                # Determine order type
                order_type = 'buy_limit' if level < 0 else 'buy_stop'
                
                # Place the missing order
                if self._place_buy_order(target_price, order_type):
                    placed += 1
                    self.logger.info(f"🔄 Placed {order_type} at level {level}: ${target_price:.2f}")
                    time.sleep(1)  # Small delay between orders
            
            if placed > 0:
                self.logger.info(f"🔄 Grid maintenance: placed {placed} new orders")
        
        except Exception as e:
            self.logger.error(f"❌ Error maintaining grid: {e}")
        
        except Exception as e:
            self.logger.error(f"❌ Error maintaining grid: {e}")
    
    def _order_exists_at_price(self, price, tolerance=1.0):
        """Check if an order already exists at the given price."""
        # Use $1 tolerance for BTC (since 1 pip = $1)
        rounded_price = round(price, 2)  # Round to 2 decimals for BTC
        
        # Check if any existing price is within tolerance
        for existing_price in self.placed_order_prices:
            if abs(existing_price - rounded_price) < tolerance:
                return True
        
        return False
    
    def _get_point_value(self):
        """Get point value for the symbol."""
        try:
            symbol_info = self.mt5_api.symbol_info(self.symbol)
            if symbol_info:
                # For BTC, 1 pip = 1.00 (not the point value)
                # Point value is usually 0.01, but we want pip value
                if 'BTC' in self.symbol.upper():
                    return 1.0  # 1 pip = 1 dollar for BTCUSD
                return symbol_info.point
            else:
                # Default for BTC
                return 1.0
        except:
            return 1.0
    
    def _close_all_orders(self):
        """Close all pending orders."""
        try:
            orders = self.mt5_api.orders_get(symbol=self.symbol)
            if not orders:
                return
            
            closed_count = 0
            for order in orders:
                request = {
                    "action": self.mt5_api.TRADE_ACTION_REMOVE,
                    "order": order.ticket,
                }
                
                result = self.mt5_api.order_send(request)
                if result and result.retcode == self.mt5_api.TRADE_RETCODE_DONE:
                    closed_count += 1
            
            # Clear tracking
            self.placed_order_prices.clear()
            self.active_orders.clear()
            
            self.logger.info(f"🗑️ Closed {closed_count} pending orders")
            
        except Exception as e:
            self.logger.error(f"❌ Error closing orders: {e}")
    
    def _log_status(self):
        """Log current strategy status."""
        try:
            orders = self.mt5_api.orders_get(symbol=self.symbol)
            positions = self.mt5_api.positions_get(symbol=self.symbol)
            
            order_count = len(orders) if orders else 0
            position_count = len(positions) if positions else 0
            
            tick = self.mt5_api.symbol_info_tick(self.symbol)
            current_price = tick.bid if tick else 0.0
            
            self.logger.info(
                f"📊 BTC Grid Status: "
                f"Price: {current_price:.5f}, "
                f"Orders: {order_count}, "
                f"Positions: {position_count}"
            )
            
        except Exception as e:
            self.logger.error(f"❌ Error logging status: {e}")
    
    def get_status(self):
        """Get current strategy status for telegram commands."""
        try:
            orders = self.mt5_api.orders_get(symbol=self.symbol)
            positions = self.mt5_api.positions_get(symbol=self.symbol)
            
            order_count = len(orders) if orders else 0
            position_count = len(positions) if positions else 0
            
            tick = self.mt5_api.symbol_info_tick(self.symbol)
            current_price = tick.bid if tick else 0.0
            
            return {
                'running': self.is_running,
                'paused': self.is_paused,
                'symbol': self.symbol,
                'current_price': current_price,
                'pending_orders': order_count,
                'open_positions': position_count,
                'grid_spacing': self.GRID_SPACING,
                'volume': self.VOLUME,
                'tp_distance': self.TP_DISTANCE
            }
            
        except Exception as e:
            self.logger.error(f"❌ Error getting status: {e}")
            return {'error': str(e)}