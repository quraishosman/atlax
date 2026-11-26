"""
ATLAX Scanner Entry Point.

Start the live market scanner with:
    python run_scanner.py

The scanner will:
  1. Load all configuration from config/atlax.yaml
  2. Connect to the MetaTrader5 terminal
  3. Continuously poll all configured instruments
  4. Run the full pipeline: Detect -> Strategy -> Confidence -> Alert
  5. Shut down cleanly on Ctrl+C or SIGTERM

Prerequisites:
  - MetaTrader5 terminal must be running and logged in
  - pip install MetaTrader5 PyYAML
  - Set ATLAX_MT5_PASSWORD env var if not using terminal session

Authority: docs/04_SYSTEM_DESIGN.md
"""

import logging
import os
import sys
from pathlib import Path

def _load_project_env() -> None:
    """Load the local .env when python-dotenv is unavailable."""
    try:
        from dotenv import load_dotenv
    except ImportError:
        env_path = Path(".env")
        if not env_path.is_file():
            return
        for raw_line in env_path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip()
            if not key or not key.replace("_", "").isalnum():
                continue
            if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
                value = value[1:-1]
            os.environ.setdefault(key, value)
        return
    load_dotenv()


_load_project_env()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(name)s | %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)

logger = logging.getLogger("atlax.main")

CONFIG_PATH = "config/atlax.yaml"
_INSTANCE_LOCK_HANDLE = None


def _acquire_instance_lock():
    """Prevent duplicate scanner processes from competing for Telegram updates."""
    global _INSTANCE_LOCK_HANDLE

    lock_path = Path("data") / "scanner.instance.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = lock_path.open("a+", encoding="utf-8")
    handle.seek(0)
    handle.write("0")
    handle.flush()
    handle.seek(0)

    try:
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (BlockingIOError, OSError) as exc:
        handle.close()
        logger.error(
            '{"event":"scanner_instance_lock_failed","lock":"%s",'
            '"reason":"another scanner is already running"}',
            str(lock_path),
        )
        raise SystemExit(1) from exc

    handle.seek(0)
    handle.truncate()
    handle.write(str(os.getpid()))
    handle.flush()
    _INSTANCE_LOCK_HANDLE = handle
    return handle


def _release_instance_lock() -> None:
    global _INSTANCE_LOCK_HANDLE
    if _INSTANCE_LOCK_HANDLE is None:
        return
    try:
        _INSTANCE_LOCK_HANDLE.close()
    finally:
        _INSTANCE_LOCK_HANDLE = None


def main() -> None:
    _acquire_instance_lock()
    from core.market_data.scanner import MarketScanner
    from core.settings.alert_config import load_alert_config
    from core.settings.account_mode_config import load_account_mode_config
    from core.settings.analytics_config import load_analytics_config
    from core.settings.atr_quality_config import load_atr_quality_config
    from core.settings.approval_config import load_approval_config
    from core.settings.confidence_config import load_confidence_config
    from core.settings.circuit_breaker_config import load_circuit_breaker_config
    from core.settings.htf_momentum_config import load_htf_momentum_config
    from core.settings.crt_config import load_crt_config
    from core.settings.execution_config import load_execution_config
    from core.settings.fvg_config import load_fvg_config
    from core.settings.market_data_config import load_market_data_config
    from core.settings.key_level_config import load_key_level_config
    from core.settings.nested_crt_config import load_nested_crt_routing_config
    from core.settings.payment_config import load_payment_config
    from core.settings.profit_protection_config import load_profit_protection_config
    from core.settings.prop_firm_profiles import load_prop_firm_profiles
    from core.settings.risk_config import load_risk_config
    from core.settings.strategy_config import load_strategy_config
    from core.settings.volume_profile_config import load_volume_profile_config
    from core.settings.watchdog_config import load_watchdog_config
    from core.storage.postgres import initialize_storage

    logger.info('{"event":"atlax_start","config":"%s"}', CONFIG_PATH)

    try:
        crt_cfg = load_crt_config(CONFIG_PATH)
        strategy_cfg = load_strategy_config(CONFIG_PATH)
        conf_cfg = load_confidence_config(CONFIG_PATH)
        circuit_breaker_cfg = load_circuit_breaker_config(CONFIG_PATH)
        htf_momentum_cfg = load_htf_momentum_config(CONFIG_PATH)
        alert_cfg = load_alert_config(CONFIG_PATH)
        market_cfg = load_market_data_config(CONFIG_PATH)
        risk_cfg = load_risk_config(CONFIG_PATH)
        approval_cfg = load_approval_config(CONFIG_PATH)
        execution_cfg = load_execution_config(CONFIG_PATH)
        fvg_cfg = load_fvg_config(CONFIG_PATH)
        analytics_cfg = load_analytics_config(CONFIG_PATH)
        payment_cfg = load_payment_config(CONFIG_PATH)
        atr_quality_cfg = load_atr_quality_config(CONFIG_PATH)
        key_level_cfg = load_key_level_config(CONFIG_PATH)
        nested_crt_cfg = load_nested_crt_routing_config(CONFIG_PATH)
        volume_profile_cfg = load_volume_profile_config(CONFIG_PATH)
        watchdog_cfg = load_watchdog_config(CONFIG_PATH)
        profit_protection_cfg = load_profit_protection_config(CONFIG_PATH)
        account_mode_cfg = load_account_mode_config(CONFIG_PATH)
        prop_firm_profiles = load_prop_firm_profiles()
        initialize_storage()
    except (FileNotFoundError, ValueError) as exc:
        logger.error('{"event":"config_load_failed","reason":"%s"}', exc)
        sys.exit(1)
    except RuntimeError as exc:
        logger.error('{"event":"storage_startup_failed","reason":"%s"}', exc)
        sys.exit(1)

    logger.info(
        '{"event":"config_loaded","instruments":%d,"profiles":%d}',
        len(market_cfg.instruments),
        len(strategy_cfg.active_profiles),
    )

    scanner = MarketScanner(
        market_cfg=market_cfg,
        crt_cfg=crt_cfg,
        strategy_cfg=strategy_cfg,
        conf_cfg=conf_cfg,
        htf_momentum_cfg=htf_momentum_cfg,
        alert_cfg=alert_cfg,
        risk_cfg=risk_cfg,
        approval_cfg=approval_cfg,
        execution_cfg=execution_cfg,
        analytics_cfg=analytics_cfg,
        payment_cfg=payment_cfg,
        atr_quality_cfg=atr_quality_cfg,
        key_level_cfg=key_level_cfg,
        nested_crt_cfg=nested_crt_cfg,
        fvg_cfg=fvg_cfg,
        volume_profile_cfg=volume_profile_cfg,
        profit_protection_cfg=profit_protection_cfg,
        account_mode_cfg=account_mode_cfg,
        circuit_breaker_cfg=circuit_breaker_cfg,
        prop_firm_profiles=prop_firm_profiles,
        watchdog_cfg=watchdog_cfg,
    )

    try:
        scanner.run()
    finally:
        _release_instance_lock()
        logger.info('{"event":"atlax_exit"}')


if __name__ == "__main__":
    main()
