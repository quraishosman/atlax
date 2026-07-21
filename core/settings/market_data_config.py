"""
ATLAX Market Data Configuration Schema and Loader.

Defines MarketDataConfig loaded from config/atlax.yaml.
All watched symbols, timeframes, ATR periods, and poll intervals
must flow through this config — never hardcoded.

Authority: docs/13_CONFIGURATION.md, docs/04_SYSTEM_DESIGN.md
"""

from __future__ import annotations

from dataclasses import dataclass

import yaml


@dataclass(frozen=True)
class WatchedInstrument:
    """
    A single symbol+timeframe pair to monitor.

    The scanner runs a detection cycle for every WatchedInstrument
    on every poll. Candles are fetched from MT5 for each.

    Attributes:
        symbol: Broker symbol string (e.g. "EURUSD", "XAUUSD").
        timeframe: Timeframe string matching CRT allowed timeframes
                   (e.g. "M15", "H1", "H4").
        candle_count: How many candles to fetch per cycle.
                      Must be at least 3 (parent + sweep + confirmation).
        atr_period: Number of candles used to compute ATR.
                    ATR is used by CRTDetector for min_parent_range check.
        pip_size: Pip size for this instrument.
                  Overrides the global crt.pip_size for this symbol.
    """
    symbol: str
    timeframe: str
    candle_count: int
    atr_period: int
    pip_size: float

    def __post_init__(self) -> None:
        if self.candle_count < 3:
            raise ValueError(
                f"candle_count must be >= 3 for {self.symbol} {self.timeframe}, "
                f"got {self.candle_count}"
            )
        if self.atr_period < 1:
            raise ValueError(
                f"atr_period must be >= 1, got {self.atr_period}"
            )
        if self.pip_size <= 0:
            raise ValueError(
                f"pip_size must be > 0, got {self.pip_size}"
            )


@dataclass(frozen=True)
class MT5ConnectionConfig:
    """
    MT5 terminal connection settings.

    Attributes:
        account: MT5 account number. 0 = use the currently logged-in account.
        server: Broker server name. Empty string = use terminal default.
        password: Account password. Empty = use terminal session.
                  Should be set via ATLAX_MT5_PASSWORD env var (docs/16_SECURITY.md).
        timeout_ms: Connection timeout in milliseconds.
        max_retries: How many times to retry initialisation on failure.
    """
    account: int
    server: str
    password: str
    timeout_ms: int
    max_retries: int

    def __post_init__(self) -> None:
        if self.timeout_ms < 100:
            raise ValueError(f"timeout_ms must be >= 100, got {self.timeout_ms}")
        if self.max_retries < 0:
            raise ValueError(f"max_retries must be >= 0, got {self.max_retries}")


@dataclass(frozen=True)
class MarketDataConfig:
    """
    Full configuration for the MT5 Market Data Feed and Scanner.

    Authority: docs/13_CONFIGURATION.md

    Attributes:
        enabled: Master toggle for the market data feed.
        poll_interval_seconds: Seconds to wait between scanner cycles.
        mt5: MT5 connection settings.
        instruments: Tuple of WatchedInstrument instances to scan.
    """
    enabled: bool
    poll_interval_seconds: int
    mt5: MT5ConnectionConfig
    instruments: tuple[WatchedInstrument, ...]

    def __post_init__(self) -> None:
        if self.poll_interval_seconds < 1:
            raise ValueError(
                f"poll_interval_seconds must be >= 1, "
                f"got {self.poll_interval_seconds}"
            )
        if not self.instruments:
            raise ValueError(
                "At least one instrument must be configured under "
                "market_data.instruments."
            )


def load_market_data_config(config_path: str) -> MarketDataConfig:
    """
    Load and validate MarketDataConfig from a YAML file.

    Fail-closed: raises ValueError for any invalid configuration.
    MT5 password can be overridden via ATLAX_MT5_PASSWORD env var.

    Authority: docs/13_CONFIGURATION.md

    Args:
        config_path: Path to the YAML config file.

    Returns:
        A validated, immutable MarketDataConfig.
    """
    import os

    with open(config_path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    md_raw = raw.get("market_data", {})
    if not md_raw:
        raise ValueError(
            f"Market data configuration ('market_data:' section) is missing "
            f"from {config_path}."
        )

    mt5_raw = md_raw.get("mt5", {})
    password = os.environ.get(
        "ATLAX_MT5_PASSWORD", str(mt5_raw.get("password", ""))
    )

    try:
        mt5_conn = MT5ConnectionConfig(
            account=int(mt5_raw.get("account", 0)),
            server=str(mt5_raw.get("server", "")),
            password=password,
            timeout_ms=int(mt5_raw.get("timeout_ms", 60000)),
            max_retries=int(mt5_raw.get("max_retries", 3)),
        )

        instruments_raw = md_raw.get("instruments", [])
        if not instruments_raw:
            raise ValueError("market_data.instruments must have at least one entry.")

        instruments = []
        for inst in instruments_raw:
            instruments.append(WatchedInstrument(
                symbol=str(inst["symbol"]),
                timeframe=str(inst["timeframe"]),
                candle_count=int(inst.get("candle_count", 50)),
                atr_period=int(inst.get("atr_period", 14)),
                pip_size=float(inst.get("pip_size", 0.0001)),
            ))

        config = MarketDataConfig(
            enabled=bool(md_raw.get("enabled", True)),
            poll_interval_seconds=int(md_raw.get("poll_interval_seconds", 60)),
            mt5=mt5_conn,
            instruments=tuple(instruments),
        )
    except (KeyError, TypeError, ValueError) as e:
        raise ValueError(
            f"Market data configuration validation failed: {e}"
        ) from e

    return config
