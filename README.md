# DCA1 - Dollar Cost Averaging Project

A Python application for implementing and analyzing Dollar Cost Averaging (DCA) trading strategies.

## Project Structure

```
DCA1/
├── src/                           # Source code
│   ├── __init__.py
│   ├── main.py                   # Main application entry point
│   ├── dca_strategy.py          # Basic DCA strategy implementation
│   ├── mt5_connector.py         # MetaTrader 5 connection handler
│   ├── mt5_dca_strategy.py      # MT5-integrated DCA strategy
│   └── config_manager.py        # Configuration management
├── tests/                        # Unit tests
│   ├── __init__.py
│   ├── test_dca_strategy.py     # Basic DCA tests
│   └── test_mt5_integration.py  # MT5 integration tests
├── config/                       # Configuration files
│   └── mt5_config.json          # MT5 and DCA settings
├── scripts/                      # Example and utility scripts
│   ├── analyze_portfolio.py     # Portfolio analysis utility
│   ├── mt5_connection_example.py # MT5 connection example
│   └── dca_trading_example.py   # Complete DCA+MT5 example
├── docs/                         # Documentation
├── data/                         # Data files
├── logs/                         # Log files
├── .venv/                        # Virtual environment
├── requirements.txt              # Project dependencies
└── README.md                    # This file
```

## Features

- **DCA Strategy Implementation**: Core functionality for dollar cost averaging
- **MetaTrader 5 Integration**: Live market data and automated trading via MT5
- **Portfolio Tracking**: Track investments and calculate average prices
- **Trade Management**: Add and manage individual trades
- **Performance Analysis**: Calculate portfolio summaries and metrics
- **Risk Management**: Configurable lot sizes and trade limits
- **Configuration Management**: JSON-based configuration system

## Getting Started

### Prerequisites

- Python 3.11 or higher
- Virtual environment (automatically created)
- MetaTrader 5 terminal (for live trading features)
- MT5 account with valid credentials (optional, can use demo account)

### Installation

1. Clone or download this project
2. The virtual environment is already configured
3. Install dependencies:
   ```powershell
   D:/WorkSpaces/MT5/DCA1/.venv/Scripts/python.exe -m pip install -r requirements.txt
   ```

### MT5 Configuration

1. Edit `config/mt5_config.json` with your MT5 credentials:
   ```json
   {
     "mt5": {
       "login": your_account_number,
       "password": "your_password",
       "server": "your_broker_server"
     }
   }
   ```

2. Ensure MetaTrader 5 terminal is installed and running
3. Enable "Allow DLL imports" and "Allow WebRequest" in MT5 settings

### Running the Application

```powershell
# Basic DCA application
D:/WorkSpaces/MT5/DCA1/.venv/Scripts/python.exe src/main.py

# MT5 connection example
D:/WorkSpaces/MT5/DCA1/.venv/Scripts/python.exe scripts/mt5_connection_example.py

# Complete DCA trading example (DEMO MODE)
D:/WorkSpaces/MT5/DCA1/.venv/Scripts/python.exe scripts/dca_trading_example.py
```

### Running Tests

```powershell
# Run all tests
D:/WorkSpaces/MT5/DCA1/.venv/Scripts/python.exe -m unittest discover tests

# Run specific test files
D:/WorkSpaces/MT5/DCA1/.venv/Scripts/python.exe -m unittest tests.test_dca_strategy
D:/WorkSpaces/MT5/DCA1/.venv/Scripts/python.exe -m unittest tests.test_mt5_integration
```

## Usage Examples

### Basic DCA Strategy
```python
from src.dca_strategy import DCAStrategy

# Create a DCA strategy
strategy = DCAStrategy(investment_amount=1000.0, frequency="weekly")

# Add trades
strategy.add_trade("AAPL", 150.0, 6.67)
strategy.add_trade("MSFT", 300.0, 3.33)

# Get portfolio summary
summary = strategy.get_portfolio_summary()
print(summary)
```

### MT5 Integrated DCA Trading
```python
from src.mt5_dca_strategy import MT5DCAStrategy
from src.config_manager import ConfigManager

# Load configuration
config = ConfigManager()
credentials = config.get_mt5_credentials()

# Create MT5 DCA strategy
strategy = MT5DCAStrategy(
    investment_amount=1000.0,
    frequency="weekly",
    mt5_login=credentials['login'],
    mt5_password=credentials['password'],
    mt5_server=credentials['server']
)

# Connect to MT5
if strategy.connect_mt5():
    # Get live price
    price = strategy.get_live_price("EURUSD")
    
    # Execute DCA purchase
    result = strategy.execute_dca_purchase("EURUSD")
    
    # Get portfolio summary
    summary = strategy.get_portfolio_summary()
```

## Development

### Code Style

- Follow PEP 8 guidelines
- Use type hints
- Write docstrings for all functions and classes
- Maintain test coverage

### Testing

All code should include unit tests. Run tests before committing changes.

## License

This project is open source and available under the [MIT License](LICENSE).

## Contributing

1. Fork the repository
2. Create a feature branch
3. Make changes and add tests
4. Run tests to ensure they pass
5. Submit a pull request

## Support

For questions or issues, please create an issue in the project repository.