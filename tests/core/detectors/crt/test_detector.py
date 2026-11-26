"""
Unit tests for the Native MT5 CRT Detector.
"""

import dataclasses
from datetime import datetime, timezone
from decimal import Decimal
import pytest

from core.detectors.crt.detector import CRTDetector
from core.models.candle import Candle, CandleSequence
from core.settings.crt_config import CRTConfig


@pytest.fixture
def crt_config():
    return CRTConfig(
        enabled=True,
        min_parent_range_atr_multiple=0.5,
        min_parent_body_ratio=0.2,
        max_sweep_retracement_pct=0.55,
        require_confirmation_candle=False,
        max_gap_tolerance_pips=Decimal("2.0"),
        pip_size=Decimal("0.0001"),
        price_comparison_tolerance=Decimal("0.00001"),
        allowed_timeframes=["M5", "M15", "H1", "H4", "D1"],
        timeframe_pairings={"H1": "M5"},
        kill_zones=[]
    )


@pytest.fixture
def detector(crt_config):
    return CRTDetector(crt_config)


def create_candle(open_p, high_p, low_p, close_p, hour):
    return Candle(
        symbol="EURUSD",
        timeframe="H1",
        open=Decimal(str(open_p)),
        high=Decimal(str(high_p)),
        low=Decimal(str(low_p)),
        close=Decimal(str(close_p)),
        open_time=datetime(2026, 7, 14, hour, 0, tzinfo=timezone.utc),
        close_time=datetime(2026, 7, 14, hour, 59, tzinfo=timezone.utc),
        is_closed=True,
    )


def test_bullish_crt_valid_sweep_and_close_back(detector):
    """Test CRT-BULL-001: Sweep below CRL and body closes back inside."""
    parent = create_candle(1.1020, 1.1050, 1.1000, 1.1010, 10)  # CRL = 1.1000, CRH = 1.1050
    sweep = create_candle(1.1010, 1.1025, 1.0980, 1.1005, 11)   # Low < 1.1000, Close > 1.1000, retracement within limit

    seq = CandleSequence(symbol="EURUSD", timeframe="H1", candles=(parent, sweep))
    
    # ATR=0.0020, threshold=0.5 * 0.0020 = 0.0010. Parent range=0.0050 (meets threshold)
    output = detector.detect(seq, atr=Decimal("0.0020"))
    
    assert output.detected is True
    assert output.classification == "bullish_crt"
    assert output.metadata.swept_level == "CRL"
    assert output.metadata.crt_high == Decimal("1.1050")
    assert output.metadata.crt_low == Decimal("1.1000")


def test_bearish_crt_valid_sweep_and_close_back(detector):
    """Test CRT-BEAR-001: Sweep above CRH and body closes back inside."""
    parent = create_candle(1.1020, 1.1050, 1.1000, 1.1010, 10)  # CRL = 1.1000, CRH = 1.1050
    sweep = create_candle(1.1045, 1.1055, 1.1023, 1.1040, 11)   # High > 1.1050, Close < 1.1050, retracement within limit

    seq = CandleSequence(symbol="EURUSD", timeframe="H1", candles=(parent, sweep))
    
    output = detector.detect(seq, atr=Decimal("0.0020"))
    
    assert output.detected is True
    assert output.classification == "bearish_crt"
    assert output.metadata.swept_level == "CRH"


def test_invalid_sweep_body_outside(detector):
    """Test CRT-INVALID-001: Sweep body closes outside the parent range (Breakout)."""
    parent = create_candle(1.1020, 1.1050, 1.1000, 1.1010, 10)  # CRL = 1.1000
    sweep = create_candle(1.1010, 1.1025, 1.0980, 1.0990, 11)   # Low < 1.1000, Close < 1.1000 (Outside) while staying within retracement limit

    seq = CandleSequence(symbol="EURUSD", timeframe="H1", candles=(parent, sweep))
    
    output = detector.detect(seq, atr=Decimal("0.0020"))
    
    assert output.detected is False
    assert "breakout" in output.invalidation_reason.lower()


def test_both_sides_sweep_invalid(detector):
    """Test CRT-INVALID-004: One candle sweeps both CRH and CRL."""
    parent = create_candle(1.1020, 1.1050, 1.1000, 1.1010, 10)
    sweep = create_candle(1.1010, 1.1060, 1.0980, 1.1020, 11)   # High > 1.1050 AND Low < 1.1000

    seq = CandleSequence(symbol="EURUSD", timeframe="H1", candles=(parent, sweep))
    
    output = detector.detect(seq, atr=Decimal("0.0020"))
    
    assert output.detected is False
    assert "Both-sides sweep" in output.invalidation_reason


def test_parent_range_below_threshold(detector):
    """Test CRT-PARENT-004: Parent candle range is too small."""
    parent = create_candle(1.1020, 1.1025, 1.1015, 1.1020, 10)  # Range = 0.0010
    sweep = create_candle(1.1020, 1.1040, 1.1010, 1.1022, 11)

    seq = CandleSequence(symbol="EURUSD", timeframe="H1", candles=(parent, sweep))
    
    # ATR=0.0040, threshold=0.5 * 0.0040 = 0.0020. Parent range=0.0010 (below threshold)
    output = detector.detect(seq, atr=Decimal("0.0040"))
    
    assert output.detected is False
    assert "Parent candle range below minimum" in output.invalidation_reason


def test_parent_body_ratio_at_threshold_is_allowed(crt_config):
    """CRT-PARENT-005 boundary: exact threshold body ratio is accepted."""
    config = dataclasses.replace(crt_config, min_parent_body_ratio=0.25)
    detector = CRTDetector(config)

    parent = create_candle(1.1020, 1.1050, 1.1010, 1.1010, 10)  # Range=0.0040, body=0.0010 -> 25%
    sweep = create_candle(1.1012, 1.1032, 1.0985, 1.1020, 11)   # Sweep below CRL, closes back inside at retracement boundary

    seq = CandleSequence(symbol="EURUSD", timeframe="H1", candles=(parent, sweep))
    output = detector.detect(seq, atr=Decimal("0.0020"))

    assert output.detected is True
    assert output.classification == "bullish_crt"


def test_sweep_retracement_at_threshold_is_allowed(crt_config):
    """CRT-SWEEP-004 boundary: exact threshold retracement is still valid."""
    detector = CRTDetector(crt_config)

    parent = create_candle(1.1020, 1.1050, 1.1010, 1.1010, 10)  # Range=0.0040
    sweep = create_candle(1.1013, 1.1032, 1.0995, 1.1020, 11)   # High exactly at parent.low + 0.55*range

    seq = CandleSequence(symbol="EURUSD", timeframe="H1", candles=(parent, sweep))
    output = detector.detect(seq, atr=Decimal("0.0020"))

    assert output.detected is True
    assert output.classification == "bullish_crt"
