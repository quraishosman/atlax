"""
ATLAX Candle Data Models.

Defines the immutable Candle and CandleSequence dataclasses used as
inputs to all ATLAX detectors.

Authority Documents:
    - docs/06_DATA_MODELS.md (field definitions)
    - docs/07_DETECTOR_SPECIFICATION.md (detector input contract)
    - docs/rulebooks/CRT_RULEBOOK.md, CRT-DATA-001 (required OHLC fields)
    - docs/rulebooks/CRT_RULEBOOK.md, CRT-DATA-002 (closed candle requirement)

Design Decisions:
    - Decimal for all price fields: avoids IEEE 754 floating-point precision
      errors in financial comparisons (e.g., 0.1 + 0.2 != 0.3 in float).
    - frozen=True: enforces immutability. Detectors must never mutate inputs.
    - tuple instead of list for CandleSequence.candles: immutable sequence.
    - volume is optional per docs/06_DATA_MODELS.md ("when available").
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Optional


@dataclass(frozen=True)
class Candle:
    """
    A single OHLC candlestick.

    Represents one completed (or forming) candle on a specific symbol
    and timeframe. All price fields use Decimal to guarantee exact
    financial arithmetic.

    Rule Authority:
        CRT-DATA-001: Requires open, high, low, close, open_time,
                      timeframe, symbol.
        CRT-DATA-002: Detection logic must reject candles where
                      is_closed is False.

    Attributes:
        symbol: The trading instrument identifier (e.g., "EURUSD").
        timeframe: The candle period (e.g., "H4", "M15", "D1").
        open_time: UTC timestamp when the candle period opened.
        close_time: UTC timestamp when the candle period closed.
                    From docs/06_DATA_MODELS.md.
        open: First price of the candle period.
        high: Highest price of the candle period.
        low: Lowest price of the candle period.
        close: Last price of the candle period.
        is_closed: True if this candle has completed its period.
                   CRT-DATA-002 requires all evaluated candles to be closed.
        volume: Trade volume during the candle period.
                Optional per docs/06_DATA_MODELS.md ("when available").
    """

    symbol: str
    timeframe: str
    open_time: datetime
    close_time: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    is_closed: bool
    volume: Optional[Decimal] = None

    def __post_init__(self) -> None:
        """Validate candle invariants on construction."""
        if self.high < self.low:
            raise ValueError(
                f"Candle invariant violated: high ({self.high}) "
                f"< low ({self.low}) for {self.symbol} {self.timeframe} "
                f"at {self.open_time}"
            )
        if self.high < self.open or self.high < self.close:
            raise ValueError(
                f"Candle invariant violated: high ({self.high}) "
                f"is not the highest price for {self.symbol} {self.timeframe} "
                f"at {self.open_time}"
            )
        if self.low > self.open or self.low > self.close:
            raise ValueError(
                f"Candle invariant violated: low ({self.low}) "
                f"is not the lowest price for {self.symbol} {self.timeframe} "
                f"at {self.open_time}"
            )

    @property
    def range(self) -> Decimal:
        """
        The full high-to-low range of this candle.

        Used by CRT-PARENT-001 to compute CRT_range = parent.high - parent.low
        and by CRT-PARENT-004 for minimum range threshold validation.
        """
        return self.high - self.low

    @property
    def body_size(self) -> Decimal:
        """
        The absolute size of the candle body (|close - open|).

        Relevant for sweep validation: the body (not wick) determines
        whether the candle closed inside or outside the parent range.
        """
        return abs(self.close - self.open)

    @property
    def is_bullish(self) -> bool:
        """True if the candle closed above its open."""
        return self.close > self.open

    @property
    def is_bearish(self) -> bool:
        """True if the candle closed below its open."""
        return self.close < self.open

    @property
    def midpoint(self) -> Decimal:
        """
        The 50% midpoint of the candle range.

        Referenced as "Mean Threshold" in the CRT glossary.
        Used as a take-profit target or precision entry level.
        """
        return (self.high + self.low) / 2

    def __str__(self) -> str:
        return (
            f"Candle({self.symbol} {self.timeframe} "
            f"{self.open_time.strftime('%Y-%m-%d %H:%M')} "
            f"O={self.open} H={self.high} L={self.low} C={self.close} "
            f"{'closed' if self.is_closed else 'forming'})"
        )

    def __repr__(self) -> str:
        return (
            f"Candle(symbol={self.symbol!r}, timeframe={self.timeframe!r}, "
            f"open_time={self.open_time!r}, close_time={self.close_time!r}, "
            f"open={self.open!r}, high={self.high!r}, low={self.low!r}, "
            f"close={self.close!r}, is_closed={self.is_closed!r}, "
            f"volume={self.volume!r})"
        )


@dataclass(frozen=True)
class CandleSequence:
    """
    An ordered sequence of candles for a single symbol and timeframe.

    This is the primary input to all ATLAX detectors.
    Candles are ordered oldest-first (index 0 is the earliest candle).

    Authority:
        docs/07_DETECTOR_SPECIFICATION.md defines that each detector
        receives a CandleSequence and returns a DetectorOutput.

    Attributes:
        candles: Immutable tuple of Candle objects, oldest first.
        symbol: The trading instrument (must match all candles).
        timeframe: The candle period (must match all candles).
    """

    candles: tuple[Candle, ...]
    symbol: str
    timeframe: str

    def __post_init__(self) -> None:
        """Validate sequence consistency on construction."""
        for i, candle in enumerate(self.candles):
            if candle.symbol != self.symbol:
                raise ValueError(
                    f"Candle at index {i} has symbol {candle.symbol!r}, "
                    f"expected {self.symbol!r}"
                )
            if candle.timeframe != self.timeframe:
                raise ValueError(
                    f"Candle at index {i} has timeframe {candle.timeframe!r}, "
                    f"expected {self.timeframe!r}"
                )

    def __len__(self) -> int:
        return len(self.candles)

    def __getitem__(self, index: int) -> Candle:
        return self.candles[index]

    def __str__(self) -> str:
        return (
            f"CandleSequence({self.symbol} {self.timeframe}, "
            f"{len(self.candles)} candles)"
        )

    def __repr__(self) -> str:
        return (
            f"CandleSequence(candles={self.candles!r}, "
            f"symbol={self.symbol!r}, timeframe={self.timeframe!r})"
        )
