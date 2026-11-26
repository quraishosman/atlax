"""
Shared test fixtures for ATLAX CRT detector tests.

Contains factory functions that build the Gold Standard OHLC test
fixtures defined in docs/rulebooks/CRT_RULEBOOK.md Section 13.

Authority:
    CRT-TEST-FIXTURE-BULLISH-001
    CRT-TEST-FIXTURE-BEARISH-001
"""

import dataclasses
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
#
# Updated to satisfy CRT quality tightening thresholds (shadow_mode: false):
#   ATR = 4.0
#   min_parent_body_ratio = 0.38  → body >= 1.52
#   min_parent_range_atr_multiple = 0.75 → range >= 3.0
#   min_sweep_depth_atr_multiple = 0.22  → depth >= 0.88
#   max_sweep_retracement_pct = 0.55
# ---------------------------------------------------------------------------

@pytest.fixture
def bullish_parent() -> Candle:
    """CRT-TEST-FIXTURE-BULLISH-001: Parent candle.
    Range = 102.50 - 98.50 = 4.00 (ATR ratio = 1.00 >= 0.75 ✓)
    Body  = 101.70 - 100.00 = 1.70 (body ratio = 0.425 >= 0.38 ✓)
    """
    return make_candle("100.00", "102.50", "98.50", "101.70", hour=0)


@pytest.fixture
def bullish_sweep() -> Candle:
    """CRT-TEST-FIXTURE-BULLISH-001: Sweep candle.
    Depth = 98.50 - 97.50 = 1.00 (ATR ratio = 0.25 >= 0.22 ✓)
    Close = 100.20 >= parent.low 98.50 ✓
    """
    return make_candle("100.40", "100.60", "97.50", "100.20", hour=4)


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
    """CRT-TEST-FIXTURE-BEARISH-001: Parent candle.
    Range = 152.80 - 149.20 = 3.60 (ATR ratio = 0.90 >= 0.75 ✓)
    Body  = 151.90 - 150.30 = 1.60 (body ratio = 0.444 >= 0.38 ✓, bearish)
    """
    return make_candle("151.90", "152.80", "149.20", "150.30", hour=0)


@pytest.fixture
def bearish_sweep() -> Candle:
    """CRT-TEST-FIXTURE-BEARISH-001: Sweep candle.
    Depth = 153.75 - 152.80 = 0.95 (ATR ratio = 0.2375 >= 0.22 ✓)
    Close = 152.50 <= parent.high 152.80 ✓
    Retracement = (153.75 - 152.50) / 3.60 = 0.347 <= 0.55 ✓
    """
    return make_candle("152.50", "153.75", "150.85", "152.50", hour=4)



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
    """Use the approved three-candle fixture contract explicitly.

    The active scanner configuration intentionally keeps confirmation optional;
    these gold-standard fixtures exercise the separate confirmation branch.

    Quality tightening is set to shadow_mode=True in this fixture so that
    edge-case and invalidation tests can verify CRT detection rules without
    being pre-empted by the quality tightening enforcer.
    """
    base = load_crt_config("config/atlax.yaml")
    return dataclasses.replace(
        base,
        require_confirmation_candle=True,
        quality_tightening=dataclasses.replace(
            base.quality_tightening,
            shadow_mode=True,
        ),
    )


@pytest.fixture
def detector(crt_config) -> CRTDetector:
    """A CRTDetector with standard config."""
    return CRTDetector(crt_config)


@pytest.fixture
def default_atr() -> Decimal:
    """A reasonable ATR value that won't block the gold standard fixtures."""
    return Decimal("4.0")
