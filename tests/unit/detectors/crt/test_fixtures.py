"""
Gold Standard Fixture Tests.

These tests verify the CRTDetector against the exact OHLC data
defined in CRT-TEST-FIXTURE-BULLISH-001 and CRT-TEST-FIXTURE-BEARISH-001
in docs/rulebooks/CRT_RULEBOOK.md Section 13.

Authority:
    CRT-TEST-FIXTURE-BULLISH-001
    CRT-TEST-FIXTURE-BEARISH-001
    CRT-STRAT-001 (no BUY/SELL in output)
"""

from decimal import Decimal


class TestBullishFixture:
    """CRT-TEST-FIXTURE-BULLISH-001: Gold standard bullish detection."""

    def test_bullish_detected(self, detector, bullish_sequence, default_atr):
        """The gold standard bullish fixture must detect as bullish_crt."""
        result = detector.detect(bullish_sequence, atr=default_atr)
        assert result.detected is True
        assert result.classification == "bullish_crt"

    def test_bullish_metadata_swept_level(self, detector, bullish_sequence, default_atr):
        """Bullish CRT sweeps the CRL."""
        result = detector.detect(bullish_sequence, atr=default_atr)
        assert result.metadata is not None
        assert result.metadata.swept_level == "CRL"

    def test_bullish_metadata_crt_levels(self, detector, bullish_sequence, default_atr):
        """CRH and CRL match parent high and low."""
        result = detector.detect(bullish_sequence, atr=default_atr)
        assert result.metadata.crt_high == Decimal("102.50")
        assert result.metadata.crt_low == Decimal("98.50")

    def test_bullish_metadata_midpoint(self, detector, bullish_sequence, default_atr):
        """Midpoint target = (102.50 + 98.50) / 2 = 100.50."""
        result = detector.detect(bullish_sequence, atr=default_atr)
        assert result.metadata.midpoint_target == Decimal("100.50")

    def test_bullish_metadata_sweep_distance(self, detector, bullish_sequence, default_atr):
        """sweep_distance_pips = |98.50 - 97.80| / 0.0001 = 7000."""
        result = detector.detect(bullish_sequence, atr=default_atr)
        assert result.metadata.sweep_distance_pips == 7000.0

    def test_bullish_metadata_close_ratio(self, detector, bullish_sequence, default_atr):
        """close_location_ratio = (100.80 - 98.50) / (102.50 - 98.50) = 0.575."""
        result = detector.detect(bullish_sequence, atr=default_atr)
        assert result.metadata.close_location_ratio == pytest.approx(0.575, abs=0.001)

    def test_bullish_no_buy_in_output(self, detector, bullish_sequence, default_atr):
        """CRT-STRAT-001: Output must never contain BUY."""
        result = detector.detect(bullish_sequence, atr=default_atr)
        assert "BUY" not in result.classification
        assert "BUY" not in result.reason

    def test_bullish_detector_name(self, detector, bullish_sequence, default_atr):
        """Detector field must identify the source detector."""
        result = detector.detect(bullish_sequence, atr=default_atr)
        assert result.detector == "CRTDetector"


class TestBearishFixture:
    """CRT-TEST-FIXTURE-BEARISH-001: Gold standard bearish detection."""

    def test_bearish_detected(self, detector, bearish_sequence, default_atr):
        """The gold standard bearish fixture must detect as bearish_crt."""
        result = detector.detect(bearish_sequence, atr=default_atr)
        assert result.detected is True
        assert result.classification == "bearish_crt"

    def test_bearish_metadata_swept_level(self, detector, bearish_sequence, default_atr):
        """Bearish CRT sweeps the CRH."""
        result = detector.detect(bearish_sequence, atr=default_atr)
        assert result.metadata is not None
        assert result.metadata.swept_level == "CRH"

    def test_bearish_metadata_crt_levels(self, detector, bearish_sequence, default_atr):
        """CRH and CRL match parent high and low."""
        result = detector.detect(bearish_sequence, atr=default_atr)
        assert result.metadata.crt_high == Decimal("152.80")
        assert result.metadata.crt_low == Decimal("149.20")

    def test_bearish_metadata_sweep_distance(self, detector, bearish_sequence, default_atr):
        """sweep_distance_pips = |153.50 - 152.80| / 0.0001 = 7000."""
        result = detector.detect(bearish_sequence, atr=default_atr)
        assert result.metadata.sweep_distance_pips == 7000.0

    def test_bearish_no_sell_in_output(self, detector, bearish_sequence, default_atr):
        """CRT-STRAT-001: Output must never contain SELL."""
        result = detector.detect(bearish_sequence, atr=default_atr)
        assert "SELL" not in result.classification
        assert "SELL" not in result.reason

    def test_bearish_invalidation_reason_is_none(self, detector, bearish_sequence, default_atr):
        """A successful detection has no invalidation reason."""
        result = detector.detect(bearish_sequence, atr=default_atr)
        assert result.invalidation_reason is None


# Need pytest for approx
import pytest
