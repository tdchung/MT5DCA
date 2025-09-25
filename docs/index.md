# DCA1 Documentation

## Overview

The DCA1 project implements Dollar Cost Averaging strategies in Python.

## Getting Started

Please refer to the main [README.md](../README.md) for installation and usage instructions.

## API Documentation

### DCAStrategy Class

The main class for implementing DCA strategies.

#### Methods

- `__init__(investment_amount, frequency)`: Initialize strategy
- `add_trade(symbol, price, quantity, timestamp)`: Add a new trade
- `get_average_price(symbol)`: Get average price for a symbol
- `get_portfolio_summary()`: Get complete portfolio summary

## Examples

See the `src/main.py` file for usage examples.