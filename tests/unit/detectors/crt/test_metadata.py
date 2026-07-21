"""
Tests for CRT quality metadata computation.

Authority: CRT-QUALITY-001, CRT-SESSION-001
"""

import pytest
from decimal import Decimal
from datetime import datetime, timezone, time

from core.detectors.crt.metadata import (
    compute_sweep_distance_pips,
    compute_close_location_ratio,
    compute_sweep_wick_ratio,
    compute_session_alignment,
)
from core.settings.crt_config import KillZone
from tests.unit.detectors.crt.conftest import make_candle


class TestSweepDistancePips:
    """CRT-QUALITY-001: sweep_distance_pips."""

    def test_bullish_sweep_distance(self, bullish_parent, bullish_sweep):
        """Distance = |98.50 - 97.80| / 0.0001 = 7000 pips."""
        result = compute_sweep_distance_pips(
            bullish_parent, bullish_sweep,
            Decimal("0.0001"), "bullish"
        )
        assert result == 7000.0

    def test_bearish_sweep_distance(self, bearish_parent, bearish_sweep):
        """Distance = |153.50 - 152.80| / 0.0001 = 7000 pips."""
        result = compute_sweep_distance_pips(
            bearish_parent, bearish_sweep,
            Decimal("0.0001"), "bearish"
        )
        assert result == 7000.0

    def test_zero_pip_size_returns_none(self, bullish_parent, bullish_sweep):
        """Zero pip_size should safely return None."""
        result = compute_sweep_distance_pips(
            bullish_parent, bullish_sweep,
            Decimal("0"), "bullish"
        )
        assert result is None


class TestCloseLocationRatio:
    """CRT-QUALITY-001: close_location_ratio."""

    def test_bullish_close_ratio(self, bullish_parent, bullish_sweep):
        """Ratio = (100.80 - 98.50) / (102.50 - 98.50) = 0.575."""
        result = compute_close_location_ratio(bullish_parent, bullish_sweep)
        assert result == pytest.approx(0.575, abs=0.001)

    def test_close_at_crl_is_zero(self):
        """A sweep closing exactly at CRL → ratio = 0.0."""
        parent = make_candle("100", "110", "90", "105", hour=0)
        sweep = make_candle("100", "105", "85", "90", hour=4)
        result = compute_close_location_ratio(parent, sweep)
        assert result == pytest.approx(0.0, abs=0.001)

    def test_close_at_crh_is_one(self):
        """A sweep closing exactly at CRH → ratio = 1.0."""
        parent = make_candle("100", "110", "90", "105", hour=0)
        sweep = make_candle("100", "115", "100", "110", hour=4)
        result = compute_close_location_ratio(parent, sweep)
        assert result == pytest.approx(1.0, abs=0.001)

    def test_zero_range_returns_none(self):
        """A doji parent with zero range → None."""
        parent = make_candle("100", "100", "100", "100", hour=0)
        sweep = make_candle("100", "101", "99", "100", hour=4)
        result = compute_close_location_ratio(parent, sweep)
        assert result is None


class TestSweepWickRatio:
    """CRT-QUALITY-001: sweep_wick_ratio."""

    def test_bullish_wick_ratio(self, bullish_parent, bullish_sweep):
        """Wick = |98.50 - 97.80| / (102.50 - 98.50) = 0.70 / 4.00 = 0.175."""
        result = compute_sweep_wick_ratio(
            bullish_parent, bullish_sweep, "bullish"
        )
        assert result == pytest.approx(0.175, abs=0.001)

    def test_bearish_wick_ratio(self, bearish_parent, bearish_sweep):
        """Wick = |153.50 - 152.80| / (152.80 - 149.20) = 0.70 / 3.60."""
        result = compute_sweep_wick_ratio(
            bearish_parent, bearish_sweep, "bearish"
        )
        assert result == pytest.approx(0.1944, abs=0.001)


class TestSessionAlignment:
    """CRT-SESSION-001: session_alignment."""

    def test_inside_london_kill_zone(self):
        """03:30 UTC is inside London Open (02:00 - 05:00)."""
        kz = (KillZone("London Open", time(2, 0), time(5, 0)),)
        dt = datetime(2026, 7, 20, 3, 30, tzinfo=timezone.utc)
        assert compute_session_alignment(dt, kz) is True

    def test_outside_all_kill_zones(self):
        """10:00 UTC is outside both London and NY."""
        kz = (
            KillZone("London Open", time(2, 0), time(5, 0)),
            KillZone("New York AM", time(12, 0), time(15, 0)),
        )
        dt = datetime(2026, 7, 20, 10, 0, tzinfo=timezone.utc)
        assert compute_session_alignment(dt, kz) is False

    def test_inside_new_york_kill_zone(self):
        """13:00 UTC is inside New York AM (12:00 - 15:00)."""
        kz = (KillZone("New York AM", time(12, 0), time(15, 0)),)
        dt = datetime(2026, 7, 20, 13, 0, tzinfo=timezone.utc)
        assert compute_session_alignment(dt, kz) is True

    def test_boundary_start_is_inside(self):
        """Exactly at session start is inside."""
        kz = (KillZone("London Open", time(2, 0), time(5, 0)),)
        dt = datetime(2026, 7, 20, 2, 0, tzinfo=timezone.utc)
        assert compute_session_alignment(dt, kz) is True

    def test_empty_kill_zones(self):
        """No kill zones configured → always False."""
        dt = datetime(2026, 7, 20, 3, 0, tzinfo=timezone.utc)
        assert compute_session_alignment(dt, ()) is False
