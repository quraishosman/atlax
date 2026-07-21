"""
ATLAX Scanner Entry Point.

Start the live market scanner with:
    python run_scanner.py

The scanner will:
  1. Load all configuration from config/atlax.yaml
  2. Connect to the MetaTrader5 terminal
  3. Continuously poll all configured instruments
  4. Run the full pipeline: Detect → Strategy → Confidence → Alert
  5. Shut down cleanly on Ctrl+C or SIGTERM

Prerequisites:
  - MetaTrader5 terminal must be running and logged in
  - pip install MetaTrader5 PyYAML
  - Set ATLAX_MT5_PASSWORD env var if not using terminal session

Authority: docs/04_SYSTEM_DESIGN.md
"""

import logging
import sys

# Configure structured logging before importing anything else
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(name)s | %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
    ],
)

logger = logging.getLogger("atlax.main")

CONFIG_PATH = "config/atlax.yaml"


def main() -> None:
    from core.settings.crt_config import load_crt_config
    from core.settings.strategy_config import load_strategy_config
    from core.settings.confidence_config import load_confidence_config
    from core.settings.alert_config import load_alert_config
    from core.settings.market_data_config import load_market_data_config
    from core.market_data.scanner import MarketScanner

    logger.info('{"event":"atlax_start","config":"%s"}', CONFIG_PATH)

    try:
        crt_cfg      = load_crt_config(CONFIG_PATH)
        strategy_cfg = load_strategy_config(CONFIG_PATH)
        conf_cfg     = load_confidence_config(CONFIG_PATH)
        alert_cfg    = load_alert_config(CONFIG_PATH)
        market_cfg   = load_market_data_config(CONFIG_PATH)
    except (FileNotFoundError, ValueError) as exc:
        logger.error('{"event":"config_load_failed","reason":"%s"}', exc)
        sys.exit(1)

    logger.info(
        '{"event":"config_loaded","instruments":%d,"profiles":%d}',
        len(market_cfg.instruments),
        len(strategy_cfg.active_profiles),
    )

    scanner = MarketScanner(
        market_cfg   = market_cfg,
        crt_cfg      = crt_cfg,
        strategy_cfg = strategy_cfg,
        conf_cfg     = conf_cfg,
        alert_cfg    = alert_cfg,
    )

    scanner.run()
    logger.info('{"event":"atlax_exit"}')


if __name__ == "__main__":
    main()
