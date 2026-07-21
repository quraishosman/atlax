"""
Shared test fixtures for ATLAX CRT detector tests.

Contains factory functions that build the Gold Standard OHLC test
fixtures defined in docs/rulebooks/CRT_RULEBOOK.md Section 13.

Authority:
    CRT-TEST-FIXTURE-BULLISH-001
    CRT-TEST-FIXTURE-BEARISH-001
"""

import pytest
from decimal import Decimal
from datetime import datetime, timezone

from core.models.candle import Candle, CandleSequence
from core.settings.crt_config import load_crt_config, CRTConfig
from core.detectors.crt.detector import CRTDetector


# ---------------------------------------------------------------------------
# Helper: quick candle builder
# ---------------------------------------------------------------------------

def make_candle(
    o: str, h: str, l: str, c: str,
    hour: int = 0,
    symbol: str = "EURUSD",
    timeframe: str = "H4",
    is_closed: bool = True,
) -> Candle:
    """Build a Candle from string prices for concise test code."""
    return Candle(
        symbol=symbol,
        timeframe=timeframe,
        open_time=datetime(2026, 7, 20, hour, 0, tzinfo=timezone.utc),
        close_time=datetime(2026, 7, 20, hour + 4, 0, tzinfo=timezone.utc),
        open=Decimal(o),
        high=Decimal(h),
        low=Decimal(l),
        close=Decimal(c),
        is_closed=is_closed,
    )


def make_sequence(*candles: Candle) -> CandleSequence:
    """Wrap candles into a CandleSequence."""
    return CandleSequence(
        candles=tuple(candles),
        symbol=candles[0].symbol,
        timeframe=candles[0].timeframe,
    )


# ---------------------------------------------------------------------------
# Gold Standard Fixtures (from CRT_RULEBOOK.md Section 13)
# ---------------------------------------------------------------------------

@pytest.fixture
def bullish_parent() -> Candle:
    """CRT-TEST-FIXTURE-BULLISH-001: Parent candle."""
    return make_candle("100.00", "102.50", "98.50", "101.20", hour=0)


@pytest.fixture
def bullish_sweep() -> Candle:
    """CRT-TEST-FIXTURE-BULLISH-001: Sweep candle."""
    return make_candle("101.10", "101.80", "97.80", "100.80", hour=4)


@pytest.fixture
def bullish_confirmation() -> Candle:
    """CRT-TEST-FIXTURE-BULLISH-001: Confirmation candle."""
    return make_candle("100.90", "103.00", "100.40", "102.70", hour=8)


@pytest.fixture
def bullish_sequence(bullish_parent, bullish_sweep, bullish_confirmation):
    """Complete bullish CRT sequence."""
    return make_sequence(bullish_parent, bullish_sweep, bullish_confirmation)


@pytest.fixture
def bearish_parent() -> Candle:
    """CRT-TEST-FIXTURE-BEARISH-001: Parent candle."""
    return make_candle("150.00", "152.80", "149.20", "150.90", hour=0)


@pytest.fixture
def bearish_sweep() -> Candle:
    """CRT-TEST-FIXTURE-BEARISH-001: Sweep candle."""
    return make_candle("150.80", "153.50", "150.10", "151.20", hour=4)


@pytest.fixture
def bearish_confirmation() -> Candle:
    """CRT-TEST-FIXTURE-BEARISH-001: Confirmation candle."""
    return make_candle("151.10", "151.40", "148.70", "149.30", hour=8)


@pytest.fixture
def bearish_sequence(bearish_parent, bearish_sweep, bearish_confirmation):
    """Complete bearish CRT sequence."""
    return make_sequence(bearish_parent, bearish_sweep, bearish_confirmation)


# ---------------------------------------------------------------------------
# Config & Detector Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def crt_config() -> CRTConfig:
    """Load the standard CRT config from config/atlax.yaml."""
    return load_crt_config("config/atlax.yaml")


@pytest.fixture
def detector(crt_config) -> CRTDetector:
    """A CRTDetector with standard config."""
    return CRTDetector(crt_config)


@pytest.fixture
def default_atr() -> Decimal:
    """A reasonable ATR value that won't block the gold standard fixtures."""
    return Decimal("4.0")
