# BTC Grid Strategy

A specialized buy-only grid trading strategy for Bitcoin (BTC) with telegram notifications and automated order management.

## Strategy Overview

### Features
- **Buy-Only Trading**: Places only buy limit orders
- **Fixed Parameters**: 
  - Grid spacing: 75 pips
  - Take Profit: 100 pips
  - Volume: 0.01 lots
  - No Stop Loss
- **Grid Management**: Maintains 2 orders above and 2 orders below current price
- **Duplicate Prevention**: Tracks order prices to prevent duplicate placements
- **Auto-Cleanup**: Removes filled orders and maintains grid structure
- **Telegram Integration**: Real-time notifications and command control

## How It Works

1. **Initial Grid**: Places 4 buy limit orders around current BTC price
2. **Order Fill**: When price drops and fills a buy order, position opens with 100-pip TP
3. **TP Hit**: When TP is reached, position closes and profit is realized
4. **Grid Maintenance**: Continuously maintains 4 pending orders around market price
5. **Notifications**: Sends telegram alerts for order fills and TP hits

## Files Structure

```
src/
├── strategy/
│   └── grid_btc_ftmo.py      # Core BTC grid strategy implementation
├── main_btc_grid.py          # Main application runner
└── run_btc_grid.py           # Simple runner script

config/
└── mt5_config_btc.json       # BTC strategy configuration

docs/
└── btc_grid_README.md        # This documentation
```

## Configuration

The strategy reads settings from `config/mt5_config_btc.json`:

```json
{
    "trading": {
        "trade_symbol": "BTCUSD",
        "grid_spacing": 75.0,
        "volume": 0.01,
        "tp_distance": 100.0,
        "max_orders": 4,
        "strategy_type": "buy_only"
    },
    "btc_grid": {
        "grid_spacing_pips": 75,
        "take_profit_pips": 100,
        "volume_per_trade": 0.01,
        "max_pending_orders": 4,
        "side": "buy_only",
        "stop_loss": null
    }
}
```

## Usage

### Quick Start

1. **Run the strategy**:
   ```bash
   python run_btc_grid.py
   ```

2. **Control via Telegram**:
   - `/start_btc` - Start the strategy
   - `/stop_btc` - Stop the strategy
   - `/pause_btc` - Pause trading
   - `/resume_btc` - Resume trading
   - `/status_btc` - Check current status
   - `/help_btc` - Show available commands

### Manual Run

```bash
python src/main_btc_grid.py
```

## Telegram Commands

| Command | Description |
|---------|-------------|
| `/start_btc` | Start the BTC grid strategy |
| `/stop_btc` | Stop strategy and close all orders |
| `/pause_btc` | Pause order placement |
| `/resume_btc` | Resume strategy and place grid |
| `/status_btc` | Show strategy status and statistics |
| `/help_btc` | Display command help |

## Notifications

The strategy sends telegram notifications for:

### Order Filled
```
📈 Buy Order Filled!

• Symbol: BTCUSD
• Entry Price: 45,250.00
• TP Target: 45,350.00
• Volume: 0.01
```

### Take Profit Hit
```
🎯 Take Profit Hit!

• Symbol: BTCUSD
• TP Price: 45,350.00
• Profit: $10.00
• Volume: 0.01
```

### Strategy Status
```
📊 BTC Grid Strategy Status

• Status: Running
• Paused: No
• Symbol: BTCUSD
• Current Price: 45,275.50
• Pending Orders: 4
• Open Positions: 2
• Grid Spacing: 75 pips
• Volume: 0.01
• TP Distance: 100 pips
```

## Risk Management

### Built-in Protections
- **Fixed Volume**: Limits exposure per trade to 0.01 lots
- **No Stop Loss**: Relies on BTC's long-term upward trend
- **Order Limits**: Maximum 4 pending orders at any time
- **Duplicate Prevention**: Prevents multiple orders at same price level

### Risk Considerations
- **Drawdown Risk**: No stop loss means potential for significant drawdowns
- **Trend Dependency**: Works best in ranging or upward trending markets
- **Capital Requirements**: Need sufficient margin for multiple positions

## Monitoring

### Log Files
- Strategy logs: `logs/btc_grid_strategy.log`
- Application logs include order placements, fills, and errors

### Status Checks
- Use `/status_btc` command for real-time statistics
- Monitor pending orders and open positions
- Track profit/loss from TP hits

## Troubleshooting

### Common Issues

1. **No Orders Placed**:
   - Check MT5 connection
   - Verify symbol availability (BTCUSD)
   - Check account margin requirements

2. **Orders Not Filling**:
   - Verify price levels are reasonable
   - Check market hours and liquidity
   - Review grid spacing settings

3. **Telegram Not Working**:
   - Check telegram bot token and chat ID
   - Verify network connectivity
   - Review telegram configuration in config file

### Debug Mode
Enable detailed logging by setting log level to DEBUG in configuration:

```json
{
    "logging": {
        "level": "DEBUG",
        "file": "logs/btc_grid_strategy.log"
    }
}
```

## Performance Notes

- **Loop Frequency**: 5-second main loop
- **Status Updates**: Every minute
- **Order Tracking**: Real-time monitoring
- **Memory Usage**: Minimal state tracking

## Customization

To modify strategy parameters, edit `config/mt5_config_btc.json`:

- `grid_spacing`: Distance between orders (pips)
- `volume`: Trade size per order
- `tp_distance`: Take profit distance (pips)
- `max_orders`: Maximum pending orders

## Safety Features

1. **Graceful Shutdown**: Handles SIGINT and SIGTERM signals
2. **Error Recovery**: Continues operation despite temporary errors
3. **Connection Monitoring**: Automatically attempts MT5 reconnection
4. **State Persistence**: Tracks order placements across restarts

## Future Enhancements

Potential improvements:
- Dynamic grid spacing based on volatility
- Time-based trading windows
- Multiple TP levels
- Risk-based position sizing
- Performance analytics and reporting