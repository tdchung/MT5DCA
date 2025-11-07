# Grid DCA Strategy Module

## Overview
This module provides a reusable Grid DCA (Dollar Cost Averaging) trading strategy that can be used across multiple MT5 accounts with different configurations.

## Architecture

### Single Strategy, Multiple Configurations
All `main_xxx.py` files now share the same strategy logic. The only difference between them is the config file path.

**Old approach:**
- Each `main_xxx.py` file contained ~1500 lines of duplicated code
- Bug fixes/improvements had to be manually applied to each file
- Inconsistent behavior across instances

**New approach:**
- Common logic in `src/strategy/grid_dca_strategy.py`
- Each `main_xxx.py` is ~80 lines: load config → create strategy → run
- Single source of truth for strategy logic
- Consistent behavior guaranteed

## Files

```
src/strategy/
├── __init__.py              # Package exports
├── grid_dca_strategy.py     # Main strategy class (~1474 lines)
├── example_main.py          # Template for account-specific main files
└── README.md               # This file
```

## Key Features

### Complete Strategy Implementation
- **Order Management**: Multi-layer grid placement, fill detection, position tracking
- **Risk Management**: Drawdown monitoring, capacity caps, spread limits, blackout windows
- **Pattern Detection**: Consecutive order analysis for trend detection
- **Telegram Integration**: Complete command handler with 20+ commands built-in
- **Fibonacci Scaling**: Dynamic position sizing based on grid level
- **Quiet Hours**: Automatic trade amount adjustment during specified hours

## Usage

### Creating a New Account Instance

1. **Create a config file** (if not exists):
   ```json
   {
     "mt5": {
       "login": 123456,
       "password": "yourpassword",
       "server": "ExnessDemo-MT5",
       "path": "C:\\Program Files\\MetaTrader 5\\terminal64.exe"
     },
     "telegram": {
       "api_token": "your_bot_token",
       "bot_name": "MyBot",
       "chat_id": "your_chat_id"
     },
     "trading": {
       "fibonacci_levels": [1, 1, 2, 2, 3, 3, 5, 5, 8, 8, 13, 13, 13, 13, 13],
       "trade_symbol": "XAUUSDc",
       "delta_enter_price": 0.8,
       "target_profit": 2.0,
       "trade_amount": 0.1,
       "percent_scale": 12,
       "max_reduce_balance": 5000,
       "min_free_margin": 100
     }
   }
   ```

2. **Create a minimal main file**:
   ```python
   import logging
   import sys
   import os
   
   src_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'src'))
   if src_path not in sys.path:
       sys.path.insert(0, src_path)
   
   from mt5_connector import MT5Connection
   from config_manager import ConfigManager
   from Libs.telegramBot import TelegramBot
   from strategy import GridDCAStrategy
   
   CONFIG_FILE = "config/mt5_config_YOUR_ACCOUNT.json"
   
   def main():
       logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
       logger = logging.getLogger(__name__)
       
       config = ConfigManager(CONFIG_FILE)
       telegram_config = config.config.get('telegram', {})
       telegram_bot = TelegramBot(telegram_config.get('api_token'), telegram_config.get('bot_name'))
       
       credentials = config.get_mt5_credentials()
       mt5 = MT5Connection(**credentials)
       if not mt5.connect():
           logger.error("❌ Failed to connect to MT5")
           return
       
       strategy = GridDCAStrategy(config=config, mt5_connection=mt5, telegram_bot=telegram_bot, logger=logger)
       strategy.run()
   
   if __name__ == "__main__":
       main()
   ```

3. **Run it**:
   ```powershell
   python src/main_YOUR_ACCOUNT.py
   ```

## Strategy Features

### Core Strategy
- **Multi-layer grid orders**: Places 3 buy stop + 3 sell stop orders at each index
- **Fibonacci position sizing**: Scales lot size based on index distance
- **Dynamic percentage scaling**: Adjusts entry/TP distances based on grid index
- **Take-profit cycling**: Resets grid after reaching profit target

### Risk Management
- **Equity protection**: Stops trading if equity drops below threshold
- **Free margin check**: Prevents new orders if margin is too low
- **Spread filter**: Skips grid build if spread exceeds max
- **Position/order capacity caps**: Limits concurrent positions and pending orders
- **Max drawdown auto-pause**: Pauses bot if drawdown threshold exceeded
- **Blackout window**: Disables trading during specified hours (GMT+7)

### Pattern Detection
- **Consecutive order tracking**: Detects consecutive BUY or SELL fills
- **Exposure reduction**: Skips first-layer orders during strong trends
  - Upward trend (2+ consecutive buys) → skip new SELL layer
  - Downward trend (2+ consecutive sells) → skip new BUY layer

### Telegram Control

**Note**: All Telegram command handling is built into the strategy module. The main files only need to call `strategy.handle_telegram_command()` in the main loop. Commands are automatically processed by the strategy instance.

#### Available Commands:
- **/start**, **/resume** — Resume bot
- **/pause** — Pause immediately
- **/stop** — Pause after next TP
- **/stopat HH:MM** — Schedule pause at specific time
- **/panic confirm** — Emergency close all + pause
- **/status** — Bot status with account info, PnL, guards
- **/setamount X.XX** — Override trade amount (persistent)
- **/clearamount** — Remove trade amount override
- **/quiethours** — Configure reduced-risk hours
- **/setmaxdd X**, **/setmaxpos N**, **/setmaxorders N**, **/setspread X** — Set risk guards
- **/blackout HH-HH** — Set full trading blackout window
- **/history N** — Show last N deals
- **/pnl today|week|month** — Aggregated PnL
- **/filled** — Show filled orders summary
- **/pattern** — Show consecutive pattern detection
- **/drawdown** — Show drawdown report
- **/help** — Command reference

### Time-based Adjustments
- **Quiet hours**: Reduce trade amount during specified hours (e.g., 50% from 19:00-23:00 GMT+7)
- **Override persistence**: Manual amount changes remain until cleared

## Migrating Existing main_xxx.py Files

### Option 1: Quick Migration (Recommended)
Replace your existing `main_xxx.py` with a minimal version:

1. Backup your current file
2. Copy `src/strategy/example_main.py` to `src/main_xxx.py`
3. Update the `CONFIG_FILE` path in the new file
4. Test it

### Option 2: Gradual Migration
Keep your current file but import strategy helpers:

```python
from strategy import GridDCAStrategy

# ... your existing config loading code ...

# Replace your while loop with:
strategy = GridDCAStrategy(config, mt5, telegram_bot, logger)
strategy.run()
```

## Configuration Reference

### Required Config Keys

**`mt5`** (dict):
- `login` (int): MT5 account number
- `password` (str): Account password
- `server` (str): Broker server name
- `path` (str): Path to terminal64.exe

**`telegram`** (dict, optional):
- `api_token` (str): Telegram bot API token
- `bot_name` (str): Bot display name
- `chat_id` (str): Chat ID for notifications

**`trading`** (dict):
- `fibonacci_levels` (list[int]): Position size multipliers by index
- `trade_symbol` (str): Trading symbol (e.g., "XAUUSDc")
- `delta_enter_price` (float): Distance from price to place pending order
- `target_profit` (float): Profit distance for TP
- `trade_amount` (float): Base lot size
- `percent_scale` (int): Percentage scaling factor for grid spacing
- `max_reduce_balance` (float): Max allowed equity drop before stopping
- `min_free_margin` (float): Minimum free margin required

## Advanced: Customizing the Strategy

If you need to customize the strategy logic:

1. **Extend the class**:
   ```python
   from strategy import GridDCAStrategy
   
   class MyCustomStrategy(GridDCAStrategy):
       def run_at_index(self, symbol, amount, index, price=0):
           # Your custom grid placement logic
           super().run_at_index(symbol, amount, index, price)
   ```

2. **Override specific methods**:
   - `check_consecutive_orders_pattern()` — Custom pattern detection
   - `run_at_index()` — Custom grid placement
   - `handle_telegram_command()` — Add custom commands

## Troubleshooting

**Import errors**:
- Ensure `src/` is in Python path
- Check that `mt5_connector.py`, `config_manager.py` exist

**Strategy not running**:
- Verify config file path is correct
- Check MT5 credentials and connection
- Review logs for specific errors

**Telegram not working**:
- Verify bot token and chat ID
- Check that telegram_bot is passed to strategy
- Test bot separately with `/start` command

## Benefits

✅ **Single source of truth** — Fix once, fix everywhere
✅ **Easy to maintain** — Strategy logic centralized
✅ **Consistent behavior** — All accounts run identical code
✅ **Quick setup** — New accounts need only config file
✅ **Testable** — Strategy class can be unit tested
✅ **Flexible** — Override methods for custom behavior

## Example: Running Multiple Accounts

```powershell
# Terminal 1
python src/main_159623800.py

# Terminal 2
python src/main_183585926.py

# Terminal 3
python src/main_263120967.py
```

Each runs the same strategy with different config!

## Next Steps

1. Review `example_main.py` template
2. Create/verify your config file
3. Create minimal main file for your account
4. Test with one account first
5. Migrate other accounts once verified
6. Enjoy unified codebase! 🎉
