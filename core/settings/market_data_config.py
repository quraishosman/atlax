"""
ATLAX Market Data Configuration Schema and Loader.

Defines MarketDataConfig loaded from config/atlax.yaml.
All watched symbols, timeframes, ATR periods, and poll intervals
must flow through this config — never hardcoded.

Authority: docs/13_CONFIGURATION.md, docs/04_SYSTEM_DESIGN.md
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml

from core.settings.timeframe_policy import is_retired_timeframe


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
        if is_retired_timeframe(self.timeframe):
            raise ValueError(
                f"timeframe {self.timeframe!r} is retired from active ATLAX scanning"
            )
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
class MarketScheduleConfig:
    """Validated calendar used to decide whether market scanning is allowed."""

    timezone: str = "UTC"
    trading_weekdays: tuple[int, ...] = (0, 1, 2, 3, 4)

    def __post_init__(self) -> None:
        try:
            ZoneInfo(self.timezone)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(
                f"market_data.schedule.timezone must be a valid IANA timezone, got {self.timezone!r}"
            ) from exc

        weekdays = tuple(self.trading_weekdays)
        if not weekdays:
            raise ValueError("market_data.schedule.trading_weekdays must not be empty")
        if any(day not in range(7) for day in weekdays):
            raise ValueError(
                "market_data.schedule.trading_weekdays values must be integers from 0 (Monday) to 6 (Sunday)"
            )
        if len(set(weekdays)) != len(weekdays):
            raise ValueError("market_data.schedule.trading_weekdays must not contain duplicates")

    def is_open(self, at: datetime | None = None) -> bool:
        """Return whether the configured market calendar permits scanning."""
        current = at or datetime.now(timezone.utc)
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        local_time = current.astimezone(ZoneInfo(self.timezone))
        return local_time.weekday() in self.trading_weekdays


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
    schedule: MarketScheduleConfig = MarketScheduleConfig()

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
    MT5 password is sourced from ATLAX_MT5_PASSWORD only.

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
    password = os.environ.get("ATLAX_MT5_PASSWORD", "").strip()

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

        schedule_raw = md_raw.get("schedule", {})
        if not isinstance(schedule_raw, dict):
            raise ValueError("market_data.schedule must be a mapping")
        schedule = MarketScheduleConfig(
            timezone=str(schedule_raw.get("timezone", "UTC")).strip(),
            trading_weekdays=tuple(
                int(day) for day in schedule_raw.get("trading_weekdays", (0, 1, 2, 3, 4))
            ),
        )

        config = MarketDataConfig(
            enabled=bool(md_raw.get("enabled", True)),
            poll_interval_seconds=int(md_raw.get("poll_interval_seconds", 60)),
            mt5=mt5_conn,
            instruments=tuple(instruments),
            schedule=schedule,
        )
    except (KeyError, TypeError, ValueError) as e:
        raise ValueError(
            f"Market data configuration validation failed: {e}"
        ) from e

    return config
