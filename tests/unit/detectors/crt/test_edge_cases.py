"""
Invalidation and Edge Case Tests.

Tests every invalidation rule and edge case defined in the
CRT_RULEBOOK.md to ensure the detector correctly rejects
invalid patterns.

Authority:
    CRT-INVALID-001, CRT-INVALID-004
    CRT-SWEEP-INVALID-001, CRT-SWEEP-INVALID-002, CRT-SWEEP-INVALID-003
    CRT-PARENT-004, CRT-DATA-002, CRT-CLOSE-001
"""

from decimal import Decimal

from tests.unit.detectors.crt.conftest import make_candle, make_sequence


class TestInvalidation:
    """Tests for CRT invalidation rules."""

    def test_bullish_breakout_rejected(self, detector, bullish_parent, default_atr):
        """
        CRT-INVALID-001: sweep.close < parent.low = breakout, not CRT.
        """
        sweep = make_candle("101.10", "101.80", "96.50", "97.00", hour=4)
        conf = make_candle("97.00", "98.00", "96.00", "97.50", hour=8)
        seq = make_sequence(bullish_parent, sweep, conf)
        result = detector.detect(seq, atr=default_atr)
        assert result.detected is False
        assert "CRT-INVALID-001" in result.reason

    def test_bearish_breakout_rejected(self, detector, bearish_parent, default_atr):
        """
        CRT-INVALID-001: sweep.close > parent.high = breakout, not CRT.
        """
        sweep = make_candle("150.80", "153.50", "150.10", "153.00", hour=4)
        conf = make_candle("153.00", "154.00", "152.00", "152.50", hour=8)
        seq = make_sequence(bearish_parent, sweep, conf)
        result = detector.detect(seq, atr=default_atr)
        assert result.detected is False
        assert "CRT-INVALID-001" in result.reason

    def test_bullish_no_confirmation_rejected(self, detector, bullish_parent, default_atr):
        """
        CRT-BULL-001: confirmation.close <= sweep.high = no MSS.
        """
        sweep = make_candle("101.10", "101.80", "97.80", "100.80", hour=4)
        # Confirmation closes BELOW sweep.high (101.80)
        conf = make_candle("100.80", "101.50", "100.00", "101.00", hour=8)
        seq = make_sequence(bullish_parent, sweep, conf)
        result = detector.detect(seq, atr=default_atr)
        assert result.detected is False
        assert "CRT-BULL-001" in result.reason

    def test_bearish_no_confirmation_rejected(self, detector, bearish_parent, default_atr):
        """
        CRT-BEAR-001: confirmation.close >= sweep.low = no MSS.
        """
        sweep = make_candle("150.80", "153.50", "150.10", "151.20", hour=4)
        # Confirmation closes ABOVE sweep.low (150.10)
        conf = make_candle("151.00", "152.00", "150.50", "151.50", hour=8)
        seq = make_sequence(bearish_parent, sweep, conf)
        result = detector.detect(seq, atr=default_atr)
        assert result.detected is False
        assert "CRT-BEAR-001" in result.reason


class TestEdgeCases:
    """Tests for all 6 approved edge cases."""

    def test_equal_low_not_sweep(self, detector, bullish_parent, default_atr):
        """
        CRT-SWEEP-INVALID-002: sweep.low == parent.low is NOT a sweep.
        Strict inequality required.
        """
        # sweep.low = 98.50 == parent.low = 98.50
        sweep = make_candle("100.00", "101.00", "98.50", "100.00", hour=4)
        conf = make_candle("100.00", "103.00", "99.50", "102.00", hour=8)
        seq = make_sequence(bullish_parent, sweep, conf)
        result = detector.detect(seq, atr=default_atr)
        assert result.detected is False

    def test_equal_high_not_sweep(self, detector, bearish_parent, default_atr):
        """
        CRT-SWEEP-INVALID-002: sweep.high == parent.high is NOT a sweep.
        """
        # sweep.high = 152.80 == parent.high = 152.80
        sweep = make_candle("150.50", "152.80", "150.00", "151.00", hour=4)
        conf = make_candle("151.00", "151.50", "148.00", "148.50", hour=8)
        seq = make_sequence(bearish_parent, sweep, conf)
        result = detector.detect(seq, atr=default_atr)
        assert result.detected is False

    def test_both_sides_sweep_rejected(self, detector, bullish_parent, default_atr):
        """
        CRT-INVALID-004: sweep breaches BOTH CRH and CRL = chaotic.
        """
        sweep = make_candle("100.00", "103.00", "97.00", "100.00", hour=4)
        conf = make_candle("100.00", "104.00", "99.00", "103.00", hour=8)
        seq = make_sequence(bullish_parent, sweep, conf)
        result = detector.detect(seq, atr=default_atr)
        assert result.detected is False
        assert "CRT-INVALID-004" in result.reason

    def test_gap_candle_bullish_rejected(self, detector, bullish_parent, default_atr):
        """
        CRT-SWEEP-INVALID-003: sweep.open far below parent.low = gap.
        """
        # sweep opens at 95.00, well below parent.low=98.50
        sweep = make_candle("95.00", "101.00", "94.00", "100.00", hour=4)
        conf = make_candle("100.00", "103.00", "99.50", "102.50", hour=8)
        seq = make_sequence(bullish_parent, sweep, conf)
        result = detector.detect(seq, atr=default_atr)
        assert result.detected is False
        assert "CRT-SWEEP-INVALID-003" in result.reason

    def test_gap_candle_bearish_rejected(self, detector, bearish_parent, default_atr):
        """
        CRT-SWEEP-INVALID-003: sweep.open far above parent.high = gap.
        """
        # sweep opens at 156.00, well above parent.high=152.80
        sweep = make_candle("156.00", "157.00", "150.00", "151.00", hour=4)
        conf = make_candle("151.00", "151.50", "148.00", "148.50", hour=8)
        seq = make_sequence(bearish_parent, sweep, conf)
        result = detector.detect(seq, atr=default_atr)
        assert result.detected is False
        assert "CRT-SWEEP-INVALID-003" in result.reason

    def test_doji_parent_rejected(self, detector, default_atr):
        """
        CRT-PARENT-004: parent with near-zero range (doji) is rejected.
        """
        parent = make_candle("100.00", "100.01", "99.99", "100.00", hour=0)
        sweep = make_candle("100.00", "100.01", "99.90", "100.00", hour=4)
        conf = make_candle("100.00", "100.10", "99.95", "100.05", hour=8)
        seq = make_sequence(parent, sweep, conf)
        # ATR=4.0, multiple=0.5, threshold=2.0. Parent range=0.02 < 2.0
        result = detector.detect(seq, atr=default_atr)
        assert result.detected is False
        assert "CRT-PARENT-004" in result.reason

    def test_boundary_close_bullish_valid(self, detector, default_atr):
        """
        CRT-CLOSE-001: sweep.close == parent.low EXACTLY is valid.
        Boundary close is accepted.
        """
        parent = make_candle("100.00", "102.50", "98.50", "101.20", hour=0)
        # sweep.close = 98.50 == parent.low = 98.50 (exactly on boundary)
        sweep = make_candle("101.00", "101.50", "97.80", "98.50", hour=4)
        conf = make_candle("98.50", "102.00", "98.00", "101.60", hour=8)
        seq = make_sequence(parent, sweep, conf)
        result = detector.detect(seq, atr=default_atr)
        assert result.detected is True
        assert result.classification == "bullish_crt"

    def test_boundary_close_bearish_valid(self, detector, default_atr):
        """
        CRT-CLOSE-001: sweep.close == parent.high EXACTLY is valid.
        """
        parent = make_candle("150.00", "152.80", "149.20", "150.90", hour=0)
        # sweep.close = 152.80 == parent.high = 152.80 (exactly on boundary)
        sweep = make_candle("151.00", "153.50", "150.50", "152.80", hour=4)
        conf = make_candle("152.80", "153.00", "149.00", "149.50", hour=8)
        seq = make_sequence(parent, sweep, conf)
        result = detector.detect(seq, atr=default_atr)
        assert result.detected is True
        assert result.classification == "bearish_crt"

    def test_unclosed_candle_returns_unknown(self, detector, bullish_parent, default_atr):
        """
        CRT-DATA-002: Forming candles must return UNKNOWN.
        """
        sweep = make_candle("101.10", "101.80", "97.80", "100.80", hour=4,
                            is_closed=False)
        conf = make_candle("100.90", "103.00", "100.40", "102.70", hour=8)
        seq = make_sequence(bullish_parent, sweep, conf)
        result = detector.detect(seq, atr=default_atr)
        assert result.classification == "UNKNOWN"
        assert "CRT-DATA-002" in result.reason

    def test_insufficient_candles_returns_unknown(self, detector, bullish_parent):
        """
        CRT-DATA-002: Less than required candles → UNKNOWN.
        """
        seq = make_sequence(bullish_parent)
        result = detector.detect(seq)
        assert result.classification == "UNKNOWN"
        assert "Insufficient" in result.reason
