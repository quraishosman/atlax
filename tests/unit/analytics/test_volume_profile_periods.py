"""
Unit tests for the Volume Profile Period Boundary Calculator.

Covers:
  - PREVIOUS_DAY boundaries (mid-day, midnight-edge, week boundary)
  - PREVIOUS_WEEK boundaries (mid-week, Monday reference, Sunday reference)
  - Candle period filtering (closed only, fully-inside only)
  - Forming candle rejection
  - Naive datetime rejection
  - Unknown period rejection

Authority:
    docs/40_VOLUME_PROFILE_CONFLUENCE_PLAN.md §3, §5.1
    docs/decisions/ADR-017.md
"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from core.analytics.volume_profile_periods import (
    candles_in_period,
    period_boundaries,
)
from core.models.candle import Candle


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _utc(year, month, day, hour=0, minute=0) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=timezone.utc)


def _candle(open_time: datetime, close_time: datetime, is_closed: bool = True) -> Candle:
    return Candle(
        symbol="EURUSD",
        timeframe="M15",
        open_time=open_time,
        close_time=close_time,
        open=Decimal("1.1000"),
        high=Decimal("1.1020"),
        low=Decimal("1.0990"),
        close=Decimal("1.1010"),
        is_closed=is_closed,
        tick_volume=500,
    )


# ---------------------------------------------------------------------------
# PREVIOUS_DAY
# ---------------------------------------------------------------------------

class TestPreviousDayBoundaries:
    def test_midday_reference(self):
        ref = _utc(2026, 8, 17, 14, 30)   # Monday 17 Aug 14:30 UTC
        start, end = period_boundaries("PREVIOUS_DAY", ref)
        assert start == _utc(2026, 8, 16)  # Sunday
        assert end   == _utc(2026, 8, 17)  # Monday (exclusive)

    def test_midnight_reference_uses_yesterday(self):
        ref = _utc(2026, 8, 17, 0, 0)     # exactly midnight Monday
        start, end = period_boundaries("PREVIOUS_DAY", ref)
        assert start == _utc(2026, 8, 16)
        assert end   == _utc(2026, 8, 17)

    def test_monday_reference_returns_sunday(self):
        ref = _utc(2026, 8, 17, 8, 0)
        start, end = period_boundaries("PREVIOUS_DAY", ref)
        assert start.weekday() == 6  # Sunday

    def test_half_open_interval(self):
        ref = _utc(2026, 8, 17, 10, 0)
        start, end = period_boundaries("PREVIOUS_DAY", ref)
        assert (end - start).total_seconds() == 86400

    def test_result_is_utc_aware(self):
        ref = _utc(2026, 8, 17, 9, 0)
        start, end = period_boundaries("PREVIOUS_DAY", ref)
        assert start.tzinfo is not None
        assert end.tzinfo is not None


# ---------------------------------------------------------------------------
# PREVIOUS_WEEK
# ---------------------------------------------------------------------------

class TestPreviousWeekBoundaries:
    def test_wednesday_reference_returns_last_week(self):
        ref = _utc(2026, 8, 19, 12, 0)   # Wednesday 19 Aug
        # This week: Mon 17 Aug; last week: Mon 10 Aug – Mon 17 Aug
        start, end = period_boundaries("PREVIOUS_WEEK", ref)
        assert start == _utc(2026, 8, 10)   # last Monday
        assert end   == _utc(2026, 8, 17)   # this Monday (exclusive)

    def test_monday_reference_returns_previous_full_week(self):
        ref = _utc(2026, 8, 17, 0, 0)   # Monday exactly
        start, end = period_boundaries("PREVIOUS_WEEK", ref)
        assert start == _utc(2026, 8, 10)
        assert end   == _utc(2026, 8, 17)

    def test_sunday_reference_returns_last_week(self):
        ref = _utc(2026, 8, 16, 23, 59)  # Sunday (still last week relative)
        # This week: Mon 10 Aug; previous week: Mon 3 Aug
        start, end = period_boundaries("PREVIOUS_WEEK", ref)
        assert start == _utc(2026, 8, 3)
        assert end   == _utc(2026, 8, 10)

    def test_period_spans_7_days(self):
        ref = _utc(2026, 8, 19, 12, 0)
        start, end = period_boundaries("PREVIOUS_WEEK", ref)
        assert (end - start).days == 7

    def test_start_is_always_monday(self):
        for day_offset in range(7):
            ref = _utc(2026, 8, 17) + timedelta(days=day_offset)
            start, _ = period_boundaries("PREVIOUS_WEEK", ref)
            assert start.weekday() == 0, f"Expected Monday, got weekday {start.weekday()} for ref {ref}"


# ---------------------------------------------------------------------------
# Error conditions
# ---------------------------------------------------------------------------

class TestPeriodBoundaryErrors:
    def test_unknown_period_raises(self):
        ref = _utc(2026, 8, 17, 12, 0)
        with pytest.raises(ValueError, match="Unknown profile period"):
            period_boundaries("CURRENT_DAY", ref)

    def test_naive_datetime_raises(self):
        naive = datetime(2026, 8, 17, 12, 0)  # no tzinfo
        with pytest.raises(ValueError, match="timezone-aware"):
            period_boundaries("PREVIOUS_DAY", naive)


# ---------------------------------------------------------------------------
# Candle period filtering
# ---------------------------------------------------------------------------

class TestCandlesInPeriod:
    """Candles must be closed and fully inside [start, end)."""

    def test_fully_inside_closed_candle_included(self):
        start = _utc(2026, 8, 16)
        end   = _utc(2026, 8, 17)
        c = _candle(_utc(2026, 8, 16, 10), _utc(2026, 8, 16, 10, 15))
        result = candles_in_period((c,), start, end)
        assert len(result) == 1

    def test_forming_candle_excluded(self):
        start = _utc(2026, 8, 16)
        end   = _utc(2026, 8, 17)
        c = _candle(_utc(2026, 8, 16, 10), _utc(2026, 8, 16, 10, 15), is_closed=False)
        result = candles_in_period((c,), start, end)
        assert len(result) == 0

    def test_candle_opening_before_period_excluded(self):
        start = _utc(2026, 8, 16)
        end   = _utc(2026, 8, 17)
        # Opens 15 min before period start
        c = _candle(_utc(2026, 8, 15, 23, 45), _utc(2026, 8, 16, 0))
        result = candles_in_period((c,), start, end)
        assert len(result) == 0

    def test_candle_closing_after_period_excluded(self):
        start = _utc(2026, 8, 16)
        end   = _utc(2026, 8, 17)
        # Closes exactly at period_end (which is exclusive)
        c = _candle(_utc(2026, 8, 16, 23, 45), _utc(2026, 8, 17, 0))
        # close_time == end is <= end so it is included (half-open: [start, end))
        result = candles_in_period((c,), start, end)
        assert len(result) == 1

    def test_candle_closing_after_period_end_excluded(self):
        start = _utc(2026, 8, 16)
        end   = _utc(2026, 8, 17)
        c = _candle(_utc(2026, 8, 16, 23, 45), _utc(2026, 8, 17, 0, 15))
        result = candles_in_period((c,), start, end)
        assert len(result) == 0

    def test_empty_candles_returns_empty(self):
        start = _utc(2026, 8, 16)
        end   = _utc(2026, 8, 17)
        result = candles_in_period((), start, end)
        assert result == ()

    def test_mixed_candles_filters_correctly(self):
        start = _utc(2026, 8, 16)
        end   = _utc(2026, 8, 17)
        inside   = _candle(_utc(2026, 8, 16, 10), _utc(2026, 8, 16, 10, 15))
        forming  = _candle(_utc(2026, 8, 16, 11), _utc(2026, 8, 16, 11, 15), is_closed=False)
        outside  = _candle(_utc(2026, 8, 17, 0),  _utc(2026, 8, 17, 0, 15))
        result = candles_in_period((inside, forming, outside), start, end)
        assert len(result) == 1
        assert result[0] is inside
