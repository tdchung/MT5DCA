"""
Grid DCA Strategy for Account 183585926
Refactored to use GridDCAStrategy module
"""

import logging
import sys
import os
import time
from datetime import datetime, timedelta, timezone

from mt5_connector import MT5Connection
from config_manager import ConfigManager
from Libs.telegramBot import TelegramBot
from strategy.grid_dca_strategy import GridDCAStrategy

src_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'src'))
if src_path not in sys.path:
    sys.path.insert(0, src_path)


################################################################################################
# Configuration
################################################################################################
CONFIG_FILE = f"config/mt5_config_183585926.json"

config = ConfigManager(CONFIG_FILE)

# Load Telegram configuration
telegram_config = config.config.get('telegram', {})
TELEGRAM_API_TOKEN = telegram_config.get('api_token')
TELEGRAM_BOT_NAME = telegram_config.get('bot_name')
TELEGRAM_CHAT_ID = telegram_config.get('chat_id')

# Load trading configuration
trading_config = config.config.get('trading', {})
TRADE_SYMBOL = trading_config.get('trade_symbol', "XAUUSDc")
TRADE_AMOUNT = trading_config.get('trade_amount', 0.1)

telegramBot = TelegramBot(TELEGRAM_API_TOKEN, TELEGRAM_BOT_NAME) if TELEGRAM_API_TOKEN else None


################################################################################################
# Note: Telegram command handler moved to GridDCAStrategy.handle_telegram_command()
################################################################################################
def handle_telegram_command_legacy(bot, strategy, mt5_api=None, logger=None):
    """
    Handle incoming Telegram commands and update strategy state
    """
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
                    if strategy.bot_paused:
                        strategy.bot_paused = False
                        strategy.stop_requested = False
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
                    if not strategy.stop_requested:
                        strategy.stop_requested = True
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
                                strategy.next_trade_amount = new_amount
                                amount_msg = f"💰 <b>Trade Amount Updated</b>\n\n"
                                amount_msg += f"• Configured amount: {TRADE_AMOUNT}\n"
                                amount_msg += f"• Override amount (persistent): {strategy.next_trade_amount}\n\n"
                                amount_msg += (
                                    "The override will be applied after the next target profit is reached "
                                    "and will persist for all subsequent runs until you change it again."
                                )
                                
                                bot.send_message(amount_msg, chat_id=chat_id, disable_notification=False)
                                
                                if logger:
                                    logger.info(f"Trade amount set to {strategy.next_trade_amount} for next run")
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
                            if getattr(p, 'magic', None) == strategy.magic_number:
                                pos_count += 1
                                open_pnl += float(getattr(p, 'profit', 0.0))

                        pending_orders = mt5_api.orders_get(symbol=TRADE_SYMBOL) if mt5_api else []
                        order_count = 0
                        for o in pending_orders or []:
                            if getattr(o, 'magic', None) == strategy.magic_number:
                                order_count += 1

                        status_str = 'Paused ⏸️' if strategy.bot_paused else ('Stopping after TP ⏳' if strategy.stop_requested else 'Running ✅')
                        next_amount_str = f"{strategy.next_trade_amount}" if strategy.next_trade_amount else '-'
                        
                        # Run time
                        run_time_str = '-'
                        try:
                            if strategy.session_start_time:
                                run_time = datetime.now() - strategy.session_start_time
                                run_time_str = str(run_time).split('.')[0]
                        except Exception:
                            pass

                        msg = f"🤖 <b>Bot Status</b>\n\n"
                        msg += f"• Account: {login}\n"
                        msg += f"• Symbol: {TRADE_SYMBOL}\n"
                        msg += f"• Status: {status_str}\n"
                        # Scheduled stop info
                        try:
                            if strategy.stop_at_datetime:
                                msg += f"• Stop at: {strategy.stop_at_datetime.strftime('%Y-%m-%d %H:%M')} GMT+7\n"
                        except Exception:
                            pass
                        msg += f"• Current Index: {strategy.current_idx}\n"
                        msg += f"• Target Profit Threshold: ${strategy.tp_expected:.2f}\n\n"
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
                            qh_state = 'on' if strategy.quiet_hours_enabled else 'off'
                            msg += f"• Quiet hours: {qh_state} ({strategy.quiet_hours_start:02d}-{strategy.quiet_hours_end:02d} x{strategy.quiet_hours_factor})\n"
                            bl_state = 'on' if strategy.blackout_enabled else 'off'
                            msg += f"• Blackout: {bl_state} ({strategy.blackout_start:02d}-{strategy.blackout_end:02d})\n"
                            msg += f"• Caps: maxDD={strategy.max_dd_threshold}, maxPos={strategy.max_positions}, maxOrders={strategy.max_orders}, maxSpread={strategy.max_spread}\n"
                        except Exception:
                            pass

                        bot.send_message(msg, chat_id=chat_id, disable_notification=False)
                    except Exception as e:
                        if logger:
                            logger.error(f"Error building /status: {e}")
                        bot.send_message("❌ Failed to get status.", chat_id=chat_id, disable_notification=False)

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
                        if not strategy.bot_paused:
                            strategy.bot_paused = True
                            strategy.stop_requested = False
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
                            # Close and cancel immediately, then pause
                            strategy.close_all_positions(TRADE_SYMBOL)
                            strategy.cancel_all_pending_orders(TRADE_SYMBOL)
                            strategy.bot_paused = True
                            strategy.stop_requested = False
                            # Clear in-memory state
                            strategy.detail_orders.clear()
                            strategy.notified_filled.clear()
                            
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
                        account_number = "N/A"
                        if mt5_api:
                            try:
                                acc_info = mt5_api.account_info()
                                if acc_info and hasattr(acc_info, 'login'):
                                    account_number = acc_info.login
                            except Exception as e:
                                if logger:
                                    logger.debug(f"Could not get account info: {e}")
                        if strategy.bot_paused:
                            strategy.bot_paused = False
                            strategy.stop_requested = False
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
                        bot.send_message(strategy.drawdown_report(), chat_id=chat_id, disable_notification=False)
                    except Exception as e:
                        if logger:
                            logger.error(f"Error handling /drawdown: {e}")

                # Handle /clearamount command
                elif text.strip().lower() == '/clearamount':
                    try:
                        if strategy.next_trade_amount is not None:
                            cleared = strategy.next_trade_amount
                            strategy.next_trade_amount = None
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

                # Handle /stopat HH:MM (GMT+7) or /stopat off
                elif text.startswith('/stopat'):
                    try:
                        parts = text.split()
                        if len(parts) == 2 and parts[1].lower() == 'off':
                            strategy.stop_at_datetime = None
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
                            strategy.stop_at_datetime = sched
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

                # Handle risk caps
                elif text.startswith('/setmaxdd'):
                    try:
                        parts = text.split()
                        if len(parts) == 2:
                            strategy.max_dd_threshold = float(parts[1])
                            bot.send_message(f"🛡️ Max drawdown set to {strategy.max_dd_threshold}", chat_id=chat_id, disable_notification=False)
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
                            strategy.max_positions = int(parts[1])
                            bot.send_message(f"🛡️ Max positions set to {strategy.max_positions}", chat_id=chat_id, disable_notification=False)
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
                            strategy.max_orders = int(parts[1])
                            bot.send_message(f"🛡️ Max pending orders set to {strategy.max_orders}", chat_id=chat_id, disable_notification=False)
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
                            strategy.max_spread = float(parts[1])
                            bot.send_message(f"🛡️ Max spread set to {strategy.max_spread}", chat_id=chat_id, disable_notification=False)
                        else:
                            bot.send_message("Usage: /setspread X", chat_id=chat_id, disable_notification=False)
                    except Exception as e:
                        if logger:
                            logger.error(f"Error handling /setspread: {e}")
                        bot.send_message("❌ Failed to set max spread.", chat_id=chat_id, disable_notification=False)

                # Blackout window
                elif text.startswith('/blackout'):
                    try:
                        parts = text.split()
                        if len(parts) == 1:
                            state = 'on' if strategy.blackout_enabled else 'off'
                            bot.send_message(
                                f"⛔️ Blackout {state}. Window: {strategy.blackout_start:02d}-{strategy.blackout_end:02d} GMT+7",
                                chat_id=chat_id,
                                disable_notification=False,
                            )
                        elif len(parts) == 2 and parts[1].lower() == 'off':
                            strategy.blackout_enabled = False
                            bot.send_message("⛔️ Blackout disabled.", chat_id=chat_id, disable_notification=False)
                        elif len(parts) == 2 and '-' in parts[1]:
                            start_s, end_s = parts[1].split('-', 1)
                            start, end = int(start_s), int(end_s)
                            if not (0 <= start <= 23 and 0 <= end <= 23):
                                raise ValueError('Hours must be 0-23')
                            strategy.blackout_start, strategy.blackout_end = start, end
                            strategy.blackout_enabled = True
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

                # Quiet hours
                elif text.startswith('/quiethours'):
                    try:
                        parts = text.split()
                        if len(parts) == 1:
                            state = 'on' if strategy.quiet_hours_enabled else 'off'
                            bot.send_message(
                                (
                                    f"🕰️ <b>Quiet Hours</b> {state}\n"
                                    f"Window: {strategy.quiet_hours_start:02d}-{strategy.quiet_hours_end:02d} GMT+7\n"
                                    f"Factor: x{strategy.quiet_hours_factor}\n\n"
                                    "Usage:\n"
                                    "/quiethours on|off\n"
                                    "/quiethours HH-HH [factor]\n"
                                    "Example: /quiethours 19-23 0.5"
                                ),
                                chat_id=chat_id,
                                disable_notification=False,
                            )
                        elif len(parts) == 2 and parts[1].lower() in ('on', 'off'):
                            strategy.quiet_hours_enabled = (parts[1].lower() == 'on')
                            bot.send_message(f"🕰️ Quiet hours {'enabled' if strategy.quiet_hours_enabled else 'disabled'}.", chat_id=chat_id, disable_notification=False)
                        elif len(parts) >= 2 and '-' in parts[1]:
                            start_s, end_s = parts[1].split('-', 1)
                            start, end = int(start_s), int(end_s)
                            if not (0 <= start <= 23 and 0 <= end <= 23):
                                raise ValueError('Hours must be 0-23')
                            strategy.quiet_hours_start, strategy.quiet_hours_end = start, end
                            if len(parts) == 3:
                                strategy.quiet_hours_factor = float(parts[2])
                            strategy.quiet_hours_enabled = True
                            bot.send_message(
                                f"🕰️ Quiet hours set: {start:02d}-{end:02d} x{strategy.quiet_hours_factor} (enabled)",
                                chat_id=chat_id,
                                disable_notification=False,
                            )
                        else:
                            bot.send_message("Usage: /quiethours [on|off] or /quiethours HH-HH [factor]", chat_id=chat_id, disable_notification=False)
                    except Exception as e:
                        if logger:
                            logger.error(f"Error handling /quiethours: {e}")
                        bot.send_message("❌ Failed to configure quiet hours.", chat_id=chat_id, disable_notification=False)

                # History
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
                                if getattr(d, 'magic', None) != strategy.magic_number:
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
                            bot.send_message("Usage: /pnl today|week|month", chat_id=chat_id, disable_notification=False)
                            start = None
                        if start is not None:
                            deals = mt5_api.history_deals_get(start, now) if mt5_api else []
                            total = 0.0
                            count = 0
                            for d in deals or []:
                                if getattr(d, 'symbol', '') != TRADE_SYMBOL:
                                    continue
                                if getattr(d, 'magic', None) != strategy.magic_number:
                                    continue
                                total += float(getattr(d, 'profit', 0.0))
                                count += 1
                            bot.send_message(f"📈 PnL {scope}: {total:+.2f} ({count} deals)", chat_id=chat_id, disable_notification=False)
                    except Exception as e:
                        if logger:
                            logger.error(f"Error handling /pnl: {e}")
                        bot.send_message("❌ Failed to compute PnL.", chat_id=chat_id, disable_notification=False)

                # Filled orders summary
                elif text.strip().lower() == '/filled':
                    try:
                        bot.send_message(strategy.get_filled_orders_summary(), chat_id=chat_id, disable_notification=False)
                    except Exception as e:
                        if logger:
                            logger.error(f"Error handling /filled: {e}")
                        bot.send_message("❌ Failed to show filled orders.", chat_id=chat_id, disable_notification=False)

                # Pattern detection
                elif text.strip().lower() == '/pattern':
                    try:
                        pd = strategy.check_consecutive_orders_pattern()
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

    except Exception as e:
        if logger:
            logger.error(f"Error in handle_telegram_command: {e}")


################################################################################################
# Main
################################################################################################
def main():
    """
    Main entry point - uses GridDCAStrategy module
    """
    # Set up logging
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[
            logging.FileHandler(f"logs/dca_183585926_{datetime.now().strftime('%Y%m%d')}.log"),
            logging.StreamHandler()
        ]
    )
    logger = logging.getLogger(__name__)
    
    try:
        # Connect to MT5
        mt5 = MT5Connection(config, logger=logger)
        if not mt5.connect():
            logger.error("Failed to connect to MT5")
            return
        
        # Create strategy instance
        strategy = GridDCAStrategy(
            config=config,
            mt5_connection=mt5,
            telegram_bot=telegramBot,
            logger=logger
        )
        
        logger.info(f"=== Grid DCA Strategy for {TRADE_SYMBOL} (Account: 183585926) ===")
        
        # Run strategy (contains full main loop)
        strategy.run()
    
    except KeyboardInterrupt:
        logger.info("Interrupted by user. Disconnecting...")
    except Exception as e:
        logger.error(f"Error in main: {e}")
    finally:
        if 'mt5' in locals():
            mt5.disconnect()


if __name__ == "__main__":
    main()
