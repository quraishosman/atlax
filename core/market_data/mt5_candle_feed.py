"""
ATLAX MT5 Candle Feed.

Fetches OHLC candle data and computes ATR from the MetaTrader5
Python library. Converts raw MT5 rate data to ATLAX Candle models
using Decimal precision throughout.

Authority: docs/04_SYSTEM_DESIGN.md (Market Data Layer)
           docs/07_DETECTOR_SPECIFICATION.md (candle data contract)

Official MT5 Python API references:
    https://www.mql5.com/en/docs/python_metatrader5/mt5copyratesfrom_py
    https://www.mql5.com/en/docs/python_metatrader5/mt5symbolinfo_py

Architectural Boundaries:
    - Market Data Layer ONLY. Returns Candle objects, never DetectorOutput.
    - Never detects patterns, produces signals, or executes trades.
    - All price values converted to Decimal on ingestion.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional

from core.models.candle import Candle, CandleSequence
from core.settings.market_data_config import WatchedInstrument

logger = logging.getLogger("atlax.market_data.feed")

# MT5 timeframe constant mapping
# Maps ATLAX timeframe strings to MetaTrader5 TIMEFRAME_* constants
_TIMEFRAME_MAP: dict[str, int] = {
    "M1":  1,
    "M5":  5,
    "M15": 15,
    "M30": 30,
    "H1":  16385,
    "H4":  16388,
    "D1":  16408,
    "W1":  32769,
    "MN":  49153,
}


def _get_mt5_timeframe(timeframe_str: str) -> int:
    """
    Resolve an ATLAX timeframe string to an MT5 TIMEFRAME_* constant.

    Raises ValueError for unrecognised timeframes.
    """
    try:
        import MetaTrader5 as mt5
    except ImportError:
        # Fallback: use the integer codes directly (tested without MT5)
        code = _TIMEFRAME_MAP.get(timeframe_str)
        if code is None:
            raise ValueError(
                f"Unknown timeframe: {timeframe_str!r}. "
                f"Allowed: {list(_TIMEFRAME_MAP.keys())}"
            )
        return code

    mapping = {
        "M1":  mt5.TIMEFRAME_M1,
        "M5":  mt5.TIMEFRAME_M5,
        "M15": mt5.TIMEFRAME_M15,
        "M30": mt5.TIMEFRAME_M30,
        "H1":  mt5.TIMEFRAME_H1,
        "H4":  mt5.TIMEFRAME_H4,
        "D1":  mt5.TIMEFRAME_D1,
        "W1":  mt5.TIMEFRAME_W1,
        "MN":  mt5.TIMEFRAME_MN1,
    }
    tf = mapping.get(timeframe_str)
    if tf is None:
        raise ValueError(
            f"Unknown timeframe: {timeframe_str!r}. "
            f"Allowed: {list(mapping.keys())}"
        )
    return tf


def _to_decimal(value: float, pip_size: float) -> Decimal:
    """
    Convert a float price to a Decimal with appropriate precision.

    The number of decimal places is inferred from pip_size.
    e.g. pip_size=0.0001 → 5 decimal places, pip_size=0.01 → 3 places.
    """
    if pip_size >= 0.01:
        places = Decimal("0.001")
    elif pip_size >= 0.001:
        places = Decimal("0.0001")
    elif pip_size >= 0.0001:
        places = Decimal("0.00001")
    else:
        places = Decimal("0.000001")

    return Decimal(str(value)).quantize(places, rounding=ROUND_HALF_UP)


class MT5CandleFeed:
    """
    Fetches candle (rate) data from MetaTrader5 and converts it
    to ATLAX Candle / CandleSequence models.

    Requires an active MT5 connection (MT5Connection.initialize() called).
    Uses lazy import so the class can be tested without MT5 installed.

    Args:
        instrument: The WatchedInstrument config for this feed instance.
    """

    def __init__(self, instrument: WatchedInstrument) -> None:
        self._instrument = instrument

    def get_candles(self, count: Optional[int] = None) -> CandleSequence:
        """
        Fetch the most recent closed candles for this instrument.

        Fetches count+1 candles and drops the last (in-progress) candle
        so only closed candles are returned. This matches the CRT detector's
        expectation that all candles in a sequence are closed.

        Args:
            count: Number of closed candles to return. Defaults to
                   instrument.candle_count from config.

        Returns:
            CandleSequence of closed candles, newest last.

        Raises:
            RuntimeError: If MT5 returns no data or an error.
        """
        try:
            import MetaTrader5 as mt5
        except ImportError as exc:
            raise RuntimeError(
                "MetaTrader5 package not installed. "
                "Run: pip install MetaTrader5"
            ) from exc

        inst = self._instrument
        n = (count or inst.candle_count) + 1  # +1 to discard the live candle
        tf = _get_mt5_timeframe(inst.timeframe)

        rates = mt5.copy_rates_from_pos(inst.symbol, tf, 0, n)

        if rates is None or len(rates) == 0:
            error = mt5.last_error()
            raise RuntimeError(
                f"MT5 returned no data for {inst.symbol} {inst.timeframe}. "
                f"Error: code={error[0]}, msg={error[1]}"
            )

        # Discard the last (in-progress) candle
        closed_rates = rates[:-1]

        candles = []
        for rate in closed_rates:
            open_dt = datetime.fromtimestamp(rate["time"], tz=timezone.utc)
            # Compute close time from open + timeframe duration
            tf_seconds = self._timeframe_seconds(inst.timeframe)
            close_dt = datetime.fromtimestamp(
                rate["time"] + tf_seconds, tz=timezone.utc
            )
            candles.append(Candle(
                symbol=inst.symbol,
                timeframe=inst.timeframe,
                open_time=open_dt,
                close_time=close_dt,
                open=_to_decimal(rate["open"], inst.pip_size),
                high=_to_decimal(rate["high"], inst.pip_size),
                low=_to_decimal(rate["low"], inst.pip_size),
                close=_to_decimal(rate["close"], inst.pip_size),
                is_closed=True,
            ))

        logger.debug(
            '{"event":"candles_fetched","symbol":"%s","timeframe":"%s","count":%d}',
            inst.symbol, inst.timeframe, len(candles),
        )

        return CandleSequence(tuple(candles), inst.symbol, inst.timeframe)

    def get_atr(self, period: Optional[int] = None) -> Decimal:
        """
        Compute the Average True Range (ATR) for the instrument.

        ATR is the simple average of True Range over `period` candles.
        Used by CRTDetector for the min_parent_range_atr_multiple check.

        True Range = max(high-low, |high-prev_close|, |low-prev_close|)

        Args:
            period: ATR period. Defaults to instrument.atr_period.

        Returns:
            ATR as a Decimal. Returns Decimal("0") if insufficient data.
        """
        p = period or self._instrument.atr_period
        # Fetch p+1 candles to compute p true ranges
        seq = self.get_candles(count=p + 1)
        candles = seq.candles

        if len(candles) < 2:
            logger.warning(
                '{"event":"atr_insufficient_data","symbol":"%s","need":%d,"got":%d}',
                self._instrument.symbol, p + 1, len(candles),
            )
            return Decimal("0")

        true_ranges = []
        for i in range(1, len(candles)):
            c = candles[i]
            prev = candles[i - 1]
            tr = max(
                c.high - c.low,
                abs(c.high - prev.close),
                abs(c.low - prev.close),
            )
            true_ranges.append(tr)

        # Use only the last `p` true ranges
        atr_values = true_ranges[-p:]
        atr = sum(atr_values) / len(atr_values)

        logger.debug(
            '{"event":"atr_computed","symbol":"%s","timeframe":"%s",'
            '"period":%d,"atr":"%s"}',
            self._instrument.symbol, self._instrument.timeframe, p, str(atr),
        )
        return atr

    @staticmethod
    def _timeframe_seconds(timeframe: str) -> int:
        """Return the number of seconds in one candle for the given timeframe."""
        mapping = {
            "M1":  60,
            "M5":  300,
            "M15": 900,
            "M30": 1800,
            "H1":  3600,
            "H4":  14400,
            "D1":  86400,
            "W1":  604800,
            "MN":  2592000,
        }
        return mapping.get(timeframe, 3600)
