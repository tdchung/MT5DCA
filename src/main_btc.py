import logging

def setup_logging():
    """Setup basic logging configuration."""
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )


def main():
    """Main application entry point: Run CustomDcaBuy strategy for BTCUSD."""
    setup_logging()
    logger = logging.getLogger(__name__)
    logger.info("Starting CustomDcaBuy Strategy for BTCUSD")

    # Import the new grid DCA strategy using importlib
    import importlib.util
    import os
    script_path = os.path.join(os.path.dirname(__file__), '..', 'scripts', 'grid_dca_btc.py')
    spec = importlib.util.spec_from_file_location("grid_dca_btc", script_path)
    new_grid_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(new_grid_module)
    new_grid_module.main()


if __name__ == "__main__":
    main()