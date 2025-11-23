# Grid DCA Strategy - Example Execution Logs
# Shows normal operation, blackout auto-pause/resume, order fills, and TP cycle

================================================================================
2025-11-23 08:30:00,123 - INFO - === Grid DCA Strategy for XAUUSDc ===
2025-11-23 08:30:00,125 - INFO - ✅ Connected to MT5 Account (Symbol: XAUUSDc, Trade Amount: 0.1)
2025-11-23 08:30:00,130 - INFO - Starting balance: $5000.00
2025-11-23 08:30:00,135 - INFO - run_at_index: Current price for XAUUSDc: 2650.45
2025-11-23 08:30:00,145 - INFO - ✅ :: buy_0 :: BUY STOP order placed: 0.1 lots at 2651.25, TP: 2653.25
2025-11-23 08:30:00,147 - INFO - ✅ :: sell_0 :: SELL STOP order placed: 0.1 lots at 2649.65, TP: 2647.65
2025-11-23 08:30:00,149 - INFO - ✅ :: buy_1 :: BUY STOP order placed: 0.1 lots at 2653.35, TP: 2655.35
2025-11-23 08:30:00,151 - INFO - ✅ :: sell_-1 :: SELL STOP order placed: 0.1 lots at 2647.55, TP: 2645.55
2025-11-23 08:30:00,153 - INFO - ✅ :: buy_2 :: BUY STOP order placed: 0.2 lots at 2655.50, TP: 2657.70
2025-11-23 08:30:00,155 - INFO - ✅ :: sell_-2 :: SELL STOP order placed: 0.2 lots at 2645.40, TP: 2643.20
2025-11-23 08:30:00,160 - INFO - Grid orders placed for index 0: buy/sell stops at 2651.25, 2653.35, 2655.50, 2649.65, 2647.55, 2645.40

# Normal monitoring phase
2025-11-23 08:30:10,200 - INFO - Current open positions P&L: $0.00
2025-11-23 08:30:10,201 - INFO - Closed positions (TP filled) P&L: $0.00
2025-11-23 08:30:10,202 - INFO - All P&L: $0.00
2025-11-23 08:30:10,203 - INFO - current_idx: 0

# Order fill detected
2025-11-23 08:32:45,567 - INFO - 🔥 :: buy_0 :: Pending order filled: ID 123456 | BUY | 2651.25
2025-11-23 08:32:45,570 - INFO - Filled order IDs: {123456}
2025-11-23 08:32:45,575 - INFO - run_at_index: Current price for XAUUSDc: 2652.10
2025-11-23 08:32:45,580 - INFO - ✅ :: buy_0 :: BUY STOP order placed: 0.1 lots at 2652.90, TP: 2654.90
2025-11-23 08:32:45,582 - INFO - ✅ :: sell_0 :: SELL STOP order placed: 0.1 lots at 2651.30, TP: 2649.30

# Pattern detection
2025-11-23 08:35:20,123 - WARNING - ⚠️ Strong upward trend detected - consider reducing BUY exposure

# Another order fill
2025-11-23 08:37:15,234 - INFO - 🔥 :: sell_0 :: Pending order filled: ID 123457 | SELL | 2649.65
2025-11-23 08:37:15,240 - INFO - Position closed with TP: ID 123457, PnL: $1.85

# Blackout window approaching (configured 22:00-06:00 GMT+7)
2025-11-23 21:59:58,500 - INFO - Current open positions P&L: $12.45
2025-11-23 21:59:58,501 - INFO - Bot is running normally. Next check in 0.2s...

# BLACKOUT AUTO-PAUSE
2025-11-23 22:00:01,123 - INFO - ⛔️ Blackout window started (22:00-06:00 GMT+7). Bot paused automatically.
2025-11-23 22:00:01,125 - INFO - Bot is paused (blackout). Waiting...
2025-11-23 22:01:01,456 - INFO - Bot is paused (blackout). Waiting...
2025-11-23 22:17:01,789 - INFO - Bot is paused (blackout). Waiting...

# During blackout - no trading activity, just monitoring
2025-11-23 23:15:01,234 - INFO - Bot is paused (blackout). Waiting...
2025-11-24 01:30:01,567 - INFO - Bot is paused (blackout). Waiting...
2025-11-24 04:45:01,890 - INFO - Bot is paused (blackout). Waiting...

# BLACKOUT AUTO-RESUME
2025-11-24 06:00:01,123 - INFO - ⛔️ Blackout window ended. Bot auto-resuming trading operations.
2025-11-24 06:00:01,130 - INFO - run_at_index: Current price for XAUUSDc: 2648.75
2025-11-24 06:00:01,135 - INFO - ✅ :: buy_0 :: BUY STOP order placed: 0.1 lots at 2649.55, TP: 2651.55
2025-11-24 06:00:01,137 - INFO - ✅ :: sell_0 :: SELL STOP order placed: 0.1 lots at 2647.95, TP: 2645.95
2025-11-24 06:00:01,140 - INFO - Grid orders placed for index 0: immediate resume after blackout

# Back to normal trading
2025-11-24 06:00:10,200 - INFO - Current open positions P&L: $8.20
2025-11-24 06:00:10,201 - INFO - Closed positions (TP filled) P&L: $45.60
2025-11-24 06:00:10,202 - INFO - All P&L: $53.80

# Manual command during trading
2025-11-24 06:15:30,123 - INFO - Received Telegram command: /status from chat_id: 123456789
# Status shows: Running ✅ (not Paused (Blackout))

# Target Profit reached
2025-11-24 07:22:45,678 - INFO - ✅✅✅✅✅ Target profit reached.
2025-11-24 07:22:45,679 - INFO - Start balance: 5000.00
2025-11-24 07:22:45,680 - INFO - Current balance: 5105.25
2025-11-24 07:22:45,681 - INFO - Total PnL: 105.25
2025-11-24 07:22:45,682 - INFO - Session PnL: 102.50
2025-11-24 07:22:45,683 - INFO - Run time: 22:52:45

# Cycle reset and restart
2025-11-24 07:22:45,690 - INFO - Strategy positions closed: 3 out of 3 total positions for XAUUSDc
2025-11-24 07:22:45,695 - INFO - Strategy orders cancelled: 12 out of 12 total orders for XAUUSDc
2025-11-24 07:22:45,700 - INFO - Starting balance: $5105.25
2025-11-24 07:22:45,705 - INFO - run_at_index: Current price for XAUUSDc: 2655.20
2025-11-24 07:22:45,710 - INFO - ✅ New cycle started with updated balance

# Risk management example
2025-11-24 08:45:20,123 - WARNING - 🛑 Max drawdown reached: 150.00 ≥ 100.00. Bot paused.
2025-11-24 08:45:20,125 - INFO - Bot is paused (manual/scheduled pause). Waiting...

# Manual resume after risk event
2025-11-24 08:47:15,456 - INFO - Received Telegram command: /start from chat_id: 123456789
2025-11-24 08:47:15,460 - INFO - Bot resumed by user command from chat_id: 123456789

# Spread protection example
2025-11-24 09:15:30,789 - INFO - ⛔️ Spread 0.85 > max 0.30. Skipping grid build.

# Manual blackout override example
2025-11-24 22:30:00,123 - INFO - Bot is paused (blackout). Waiting...
2025-11-24 22:30:15,456 - INFO - Received Telegram command: /start from chat_id: 123456789
2025-11-24 22:30:15,460 - INFO - Bot resumed by user command from chat_id: 123456789
# (Manual start overrides blackout pause)

# Error handling example
2025-11-24 10:20:15,678 - ERROR - Error placing grid after blackout resume: Connection timeout
2025-11-24 10:20:15,680 - INFO - Retrying grid placement...

================================================================================
TELEGRAM NOTIFICATIONS EXAMPLES:
================================================================================

📱 Bot Startup:
"✅ Connected to MT5 Account (Symbol: XAUUSDc, Trade Amount: 0.1)"

📱 Order Fill Notification:
"🔥 Pending order filled - buy_0
ID 123456 | BUY | 2651.25

📋 Orders Status:
• buy_1: placed (pending)
• sell_0: placed (pending)
• buy_2: placed (pending)

📊 Drawdown Report:
Current Drawdown: $8.50
Max Drawdown: $12.30
Percentage Drawdown: 0.25%

⚠️ Pattern Detected
• Consecutive BUY pairs: 2
• Total filled: 5"

📱 Blackout Auto-Pause:
"⛔️ Blackout window started (22:00-06:00 GMT+7). Bot paused automatically."

📱 Blackout Auto-Resume:
"⛔️ Blackout window ended. Bot auto-resuming trading operations."

📱 Status During Blackout:
"🤖 Bot Status

• Account: 123456
• Symbol: XAUUSDc
• Status: Paused (Blackout) ⛔️⏸️
• Current Index: 0
• Target Profit Threshold: $100.00

Session
• Run time: 2:15:30

Account
• Balance: $5,055.20
• Equity: $5,043.85
• Free Margin: $4,890.15"

📱 TP Achievement:
"✅✅✅✅✅ Target profit reached.
Start balance: 5000.00
Current balance: 5105.25
Total PnL: 105.25
Session PnL: 102.50
Run time: 22:52:45"

================================================================================
KEY FEATURES DEMONSTRATED:
================================================================================

✅ Automatic blackout pause at 22:00 GMT+7
✅ Automatic blackout resume at 06:00 GMT+7
✅ Immediate grid placement after resume
✅ Manual override capability during blackout
✅ Status tracking shows specific pause reason
✅ Normal trading continues after blackout
✅ All existing functionality preserved
✅ Risk management still active during blackout
✅ Clean state transitions
✅ Comprehensive logging and notifications

The logs show seamless operation with the new blackout auto-pause/resume feature!