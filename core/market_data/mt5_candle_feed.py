"""
ATLAX MT5 Candle Feed.

Fetches OHLC candle data and computes ATR from the MetaTrader5
Python library. Converts raw MT5 rate data to ATLAX Candle models
using Decimal precision throughout.

Authority: docs/04_SYSTEM_DESIGN.md (Market Data Layer)
           docs/07_DETECTOR_SPECIFICATION.md (candle data contract)
           docs/40_VOLUME_PROFILE_CONFLUENCE_PLAN.md §4 (volume field contract)
           docs/decisions/ADR-017.md (volume source approval)

Official MT5 Python API references:
    https://www.mql5.com/en/docs/python_metatrader5/mt5copyratesfrom_py
    https://www.mql5.com/en/docs/python_metatrader5/mt5symbolinfo_py

Architectural Boundaries:
    - Market Data Layer ONLY. Returns Candle objects, never DetectorOutput.
    - Never detects patterns, produces signals, or executes trades.
    - All price values converted to Decimal on ingestion.
    - tick_volume and real_volume are preserved separately so the Volume
      Profile engine can select the approved source at evaluation time.
      Broker tick volume must never be presented as consolidated global
      Forex volume.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Mapping
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Optional

from core.models.candle import Candle, CandleSequence
from core.market_data.mt5_symbol import resolve_mt5_symbol
from core.settings.market_data_config import WatchedInstrument
from core.settings.timeframe_policy import is_retired_timeframe

logger = logging.getLogger("atlax.market_data.feed")

# MT5 timeframe constant mapping
# Maps ATLAX timeframe strings to MetaTrader5 TIMEFRAME_* constants
_TIMEFRAME_MAP: dict[str, int] = {
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
    if is_retired_timeframe(timeframe_str):
        raise ValueError(f"Timeframe {timeframe_str!r} is retired from active ATLAX use")
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


def _rate_field(rate: Any, field: str, default: Any = None) -> Any:
    """Read a field from either an MT5 structured row or a mapping fixture.

    ``MetaTrader5.copy_rates_from_pos`` returns rows from a NumPy structured
    array in production.  Unit tests and alternate feed adapters commonly use
    dictionaries, so the ingestion boundary must support both representations.
    """
    if isinstance(rate, Mapping):
        return rate.get(field, default)

    dtype = getattr(rate, "dtype", None)
    names = getattr(dtype, "names", None)
    if names and field in names:
        return rate[field]

    try:
        return rate[field]
    except (IndexError, KeyError, TypeError, ValueError):
        return default


def _broker_clock_offset_seconds(
    broker_now: float,
    *,
    utc_now: Optional[float] = None,
) -> int:
    """Return the MT5 broker-clock offset relative to the local UTC clock.

    Some MT5 terminals expose broker-local bar epochs as if they were UTC.
    Measuring the offset from the current tick lets the feed normalize both
    bar timestamps and closure checks without assuming a fixed broker zone.
    """
    reference = time.time() if utc_now is None else utc_now
    raw_offset = float(broker_now) - float(reference)
    # Tick clocks can lead or lag the workstation by a few seconds. Keep the
    # broker timezone component while preserving minute-aligned bar epochs.
    return int(round(raw_offset / 3600.0) * 3600)


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
        if is_retired_timeframe(instrument.timeframe):
            raise ValueError(
                f"Timeframe {instrument.timeframe!r} is retired from active ATLAX use"
            )
        self._instrument = instrument

    def get_candles(self, count: Optional[int] = None, include_live: bool = False) -> CandleSequence:
        """
        Fetch the most recent candles for this instrument.

        By default, fetches count+1 candles and drops the last (in-progress) candle
        so only closed candles are returned. If include_live is True, the forming
        candle is included at the end of the sequence (with is_closed=False).

        Args:
            count: Number of closed candles to return. Defaults to
                   instrument.candle_count from config.
            include_live: If True, include the current forming candle.

        Returns:
            CandleSequence of candles, newest last.

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
        broker_symbol = resolve_mt5_symbol(mt5, inst.symbol)
        if broker_symbol is None:
            raise RuntimeError(
                f"MT5 symbol is unavailable for canonical symbol {inst.symbol}"
            )

        rates = mt5.copy_rates_from_pos(broker_symbol, tf, 0, n)

        if rates is None or len(rates) == 0:
            error = mt5.last_error()
            raise RuntimeError(
                f"MT5 returned no data for {broker_symbol} {inst.timeframe}. "
                f"Error: code={error[0]}, msg={error[1]}"
            )

        tick = mt5.symbol_info_tick(broker_symbol)
        if not tick:
            raise RuntimeError(f"Could not fetch tick for {broker_symbol} to determine broker time.")
            
        utc_now = time.time()
        broker_now = float(tick.time)
        broker_offset_seconds = _broker_clock_offset_seconds(
            broker_now,
            utc_now=utc_now,
        )

        logger.debug(
            '{"event":"mt5_clock_normalized","symbol":"%s",'
            '"broker_timezone_offset_seconds":%d}',
            broker_symbol,
            broker_offset_seconds,
        )

        candles = []
        valid_volume_bars = 0
        for rate in rates:
            raw_open_time = int(rate["time"])
            # Compute close time from open + timeframe duration
            tf_seconds = self._timeframe_seconds(inst.timeframe)
            open_time = raw_open_time - broker_offset_seconds
            close_time = raw_open_time + tf_seconds - broker_offset_seconds
            open_dt = datetime.fromtimestamp(open_time, tz=timezone.utc)
            close_dt = datetime.fromtimestamp(close_time, tz=timezone.utc)
            
            is_closed = utc_now >= close_time

            if not include_live and not is_closed:
                continue

            # Extract volume fields from the MT5 rate row.
            # tick_volume is a broker-local activity proxy (always present when
            # the terminal provides it). real_volume is non-zero only when the
            # broker exposes consolidated volume. Both default to None so that
            # existing OHLC-only code paths and CRT fixtures are unaffected.
            # Authority: docs/40_VOLUME_PROFILE_CONFLUENCE_PLAN.md §4, ADR-017.
            raw_tick = _rate_field(rate, "tick_volume")
            raw_real = _rate_field(rate, "real_volume")
            tick_vol: Optional[int] = int(raw_tick) if raw_tick else None
            real_vol: Optional[int] = int(raw_real) if raw_real else None

            if tick_vol is not None and tick_vol > 0:
                valid_volume_bars += 1

            candles.append(Candle(
                symbol=inst.symbol,
                timeframe=inst.timeframe,
                open_time=open_dt,
                close_time=close_dt,
                open=_to_decimal(rate["open"], inst.pip_size),
                high=_to_decimal(rate["high"], inst.pip_size),
                low=_to_decimal(rate["low"], inst.pip_size),
                close=_to_decimal(rate["close"], inst.pip_size),
                is_closed=is_closed,
                tick_volume=tick_vol,
                real_volume=real_vol,
            ))

        logger.debug(
            '{"event":"candles_fetched","symbol":"%s","timeframe":"%s",'
            '"count":%d,"valid_volume_bars":%d}',
            inst.symbol, inst.timeframe, len(candles), valid_volume_bars,
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
        return self.get_atr_from_sequence(seq, p)

    def get_atr_from_sequence(
        self,
        sequence: CandleSequence,
        period: Optional[int] = None,
    ) -> Decimal:
        """Compute ATR from an already-fetched closed-candle sequence.

        The scanner already retrieves the closed candles used by detection.
        Reusing that immutable sequence avoids a second MT5 IPC request, which
        can transiently return no history or zero ATR even when the detector
        has valid market data. This keeps detector and ATR evidence on the
        same broker snapshot.
        """
        p = period or self._instrument.atr_period
        candles = tuple(candle for candle in sequence.candles if candle.is_closed)

        if len(candles) < p + 1:
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
            '"period":%d,"atr":"%s","source":"closed_sequence"}',
            self._instrument.symbol, self._instrument.timeframe, p, str(atr),
        )
        return atr

    @staticmethod
    def _timeframe_seconds(timeframe: str) -> int:
        """Return the number of seconds in one candle for the given timeframe."""
        if is_retired_timeframe(timeframe):
            raise ValueError(f"Timeframe {timeframe!r} is retired from active ATLAX use")
        mapping = {
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
