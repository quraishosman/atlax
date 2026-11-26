"""
Tests for Volume Profile ↔ Key-Level Evaluator integration (Phase 3).

Covers:
  - Bridge: VolumeProfileResult → KeyLevel conversion
  - Bridge: UNKNOWN profile → empty list
  - Evaluator: VP levels matched using same tolerance as period/swing levels
  - Evaluator: VP levels with no structural match → parent_at_htf_zone stays False
  - Evaluator: Mixed VP + structural matches → all appear in matched_levels
  - Evaluator: No VP levels (backward compat) → existing behavior unchanged
  - Evaluator: VP level exactly at tolerance boundary → included
  - Evaluator: VP level beyond tolerance → excluded

Authority:
    docs/40_VOLUME_PROFILE_CONFLUENCE_PLAN.md §6 (Confluence Matching)
    docs/27_KEY_LEVEL_EVALUATOR_PLAN.md
    docs/decisions/ADR-011.md
    docs/decisions/ADR-017.md
"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from core.market_data.key_level_evaluator import KeyLevelEvaluator
from core.market_data.volume_profile_bridge import volume_profile_levels
from core.models.candle import Candle
from core.models.key_level import KeyLevel, KeyLevelMatch
from core.models.volume_profile_result import (
    VolumeProfileLevel,
    VolumeProfileLevels,
    VolumeProfileResult,
    VolumeProfileStatus,
)
from core.settings.key_level_config import KeyLevelConfig

UTC = timezone.utc


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _candle(
    symbol: str,
    timeframe: str,
    opened: datetime,
    high: str,
    low: str,
    close: str | None = None,
    *,
    duration_hours: int,
    is_closed: bool = True,
) -> Candle:
    close_price = close if close is not None else high
    return Candle(
        symbol=symbol,
        timeframe=timeframe,
        open_time=opened,
        close_time=opened + timedelta(hours=duration_hours),
        open=Decimal(low),
        high=Decimal(high),
        low=Decimal(low),
        close=Decimal(close_price),
        is_closed=is_closed,
    )


def _config(**overrides) -> KeyLevelConfig:
    values = {
        "enabled": True,
        "shadow_mode": True,
        "version": "0.1.0",
        "tolerance_atr_multiplier": Decimal("0.10"),
        "minimum_tolerance_pips": Decimal("5"),
        "swing_left_bars": 2,
        "swing_right_bars": 2,
        "maximum_swing_levels": 5,
        "source_timeframes": ("D1", "W1", "H4"),
    }
    values.update(overrides)
    return KeyLevelConfig(**values)


def _make_vp_result(
    *,
    status=VolumeProfileStatus.VALID,
    poc_price="1.1000",
    vah_price="1.1050",
    val_price="1.0950",
    period_label="PREVIOUS_DAY",
) -> VolumeProfileResult:
    if status == VolumeProfileStatus.UNKNOWN:
        return VolumeProfileResult(
            status=VolumeProfileStatus.UNKNOWN,
            symbol="EURUSD",
            period_label=period_label,
            period_start_utc=datetime(2026, 8, 15, tzinfo=UTC),
            period_end_utc=datetime(2026, 8, 16, tzinfo=UTC),
            source_timeframe="M15",
            volume_source="MT5_TICK_VOLUME",
            volume_quality="UNKNOWN",
            allocation_method="RANGE_UNIFORM",
            value_area_percent=0.70,
            configuration_snapshot_id="test123",
            levels=None,
            unknown_reason="test_unknown",
        )
    return VolumeProfileResult(
        status=VolumeProfileStatus.VALID,
        symbol="EURUSD",
        period_label=period_label,
        period_start_utc=datetime(2026, 8, 15, tzinfo=UTC),
        period_end_utc=datetime(2026, 8, 16, tzinfo=UTC),
        source_timeframe="M15",
        volume_source="MT5_TICK_VOLUME",
        volume_quality="BROKER_TICK_PROXY",
        allocation_method="RANGE_UNIFORM",
        value_area_percent=0.70,
        configuration_snapshot_id="test123",
        levels=VolumeProfileLevels(
            poc=VolumeProfileLevel(
                level_type=f"{period_label}_POC",
                price=Decimal(poc_price),
                row_volume=Decimal("5000"),
            ),
            vah=VolumeProfileLevel(
                level_type=f"{period_label}_VAH",
                price=Decimal(vah_price),
                row_volume=Decimal("2000"),
            ),
            val=VolumeProfileLevel(
                level_type=f"{period_label}_VAL",
                price=Decimal(val_price),
                row_volume=Decimal("2000"),
            ),
            total_volume=Decimal("10000"),
            bin_width=Decimal("0.00010"),
            source_bars=96,
            valid_volume_bars=90,
        ),
    )


def _base_candles():
    """Minimal structural candles to satisfy the evaluator's history requirement."""
    daily = [_candle("EURUSD", "D1", datetime(2026, 8, 9, tzinfo=UTC), "1.3000", "1.2800", duration_hours=24)]
    weekly = [_candle("EURUSD", "W1", datetime(2026, 7, 27, tzinfo=UTC), "1.4000", "1.3800", duration_hours=168)]
    h4 = [_candle("EURUSD", "H4", datetime(2026, 8, 8, tzinfo=UTC), "1.2500", "1.2400", duration_hours=4)]
    return daily, weekly, h4


def _evaluate(parent, *, vp_levels=None, daily=None, weekly=None, h4=None):
    d, w, h = _base_candles()
    evaluator = KeyLevelEvaluator(_config())
    return evaluator.evaluate(
        parent=parent,
        parent_atr=Decimal("0.0020"),
        pip_size=Decimal("0.0001"),
        daily_candles=daily or d,
        weekly_candles=weekly or w,
        h4_candles=h4 or h,
        d1_candles=daily or d,
        volume_profile_levels=vp_levels,
    )


# ---------------------------------------------------------------------------
# Bridge tests: VolumeProfileResult → KeyLevel
# ---------------------------------------------------------------------------


class TestVolumeProfileBridge:
    def test_valid_result_produces_three_key_levels(self):
        result = _make_vp_result()
        levels = volume_profile_levels(result)
        assert len(levels) == 3
        types = {kl.level_type for kl in levels}
        assert types == {"PREVIOUS_DAY_POC", "PREVIOUS_DAY_VAH", "PREVIOUS_DAY_VAL"}

    def test_unknown_result_produces_empty_list(self):
        result = _make_vp_result(status=VolumeProfileStatus.UNKNOWN)
        levels = volume_profile_levels(result)
        assert levels == []

    def test_previous_week_labels(self):
        result = _make_vp_result(period_label="PREVIOUS_WEEK")
        levels = volume_profile_levels(result)
        types = {kl.level_type for kl in levels}
        assert types == {"PREVIOUS_WEEK_POC", "PREVIOUS_WEEK_VAH", "PREVIOUS_WEEK_VAL"}

    def test_source_timeframe_preserved(self):
        result = _make_vp_result()
        levels = volume_profile_levels(result)
        assert all(kl.source_timeframe == "M15" for kl in levels)

    def test_reference_open_time_is_period_start(self):
        result = _make_vp_result()
        levels = volume_profile_levels(result)
        assert all(kl.reference_open_time == datetime(2026, 8, 15, tzinfo=UTC) for kl in levels)

    def test_prices_match_profile_levels(self):
        result = _make_vp_result(poc_price="1.08500", vah_price="1.09000", val_price="1.08000")
        levels = volume_profile_levels(result)
        prices = {kl.level_type: kl.price for kl in levels}
        assert prices["PREVIOUS_DAY_POC"] == Decimal("1.08500")
        assert prices["PREVIOUS_DAY_VAH"] == Decimal("1.09000")
        assert prices["PREVIOUS_DAY_VAL"] == Decimal("1.08000")


# ---------------------------------------------------------------------------
# Evaluator integration: VP levels with matching
# ---------------------------------------------------------------------------


class TestEvaluatorVPIntegration:
    def test_vp_level_matched_within_tolerance(self):
        """VP POC at 1.1012 is within tolerance of parent [1.0990, 1.1010]."""
        # parent_atr=0.0020, tolerance_atr=0.10 → tol = 0.0002
        # pip_size=0.0001, min_pips=5 → pip_tol = 0.0005
        # tolerance = max(0.0002, 0.0005) = 0.0005
        # parent.high + tolerance = 1.1010 + 0.0005 = 1.1015
        # POC at 1.1012 → inside → match
        parent = _candle("EURUSD", "H1", datetime(2026, 8, 10, 10, tzinfo=UTC),
                         "1.1010", "1.0990", "1.1000", duration_hours=1)
        vp = _make_vp_result(poc_price="1.1012", vah_price="1.2000", val_price="1.0000")
        vp_levels = volume_profile_levels(vp)
        result = _evaluate(parent, vp_levels=vp_levels)

        assert result.status == "VALID"
        assert result.parent_at_htf_zone is True
        poc_matches = [m for m in result.matched_levels if m.level_type == "PREVIOUS_DAY_POC"]
        assert len(poc_matches) == 1
        assert poc_matches[0].price == Decimal("1.1012")

    def test_vp_level_beyond_tolerance_not_matched(self):
        """VP POC at 1.1020 is beyond tolerance of parent [1.0990, 1.1010]."""
        # tolerance = 0.0005
        # parent.high + tolerance = 1.1015
        # POC at 1.1020 > 1.1015 → no match
        parent = _candle("EURUSD", "H1", datetime(2026, 8, 10, 10, tzinfo=UTC),
                         "1.1010", "1.0990", "1.1000", duration_hours=1)
        vp = _make_vp_result(poc_price="1.1020", vah_price="1.2000", val_price="1.0000")
        vp_levels = volume_profile_levels(vp)
        result = _evaluate(parent, vp_levels=vp_levels)

        poc_matches = [m for m in result.matched_levels if m.level_type == "PREVIOUS_DAY_POC"]
        assert len(poc_matches) == 0

    def test_vp_level_at_exact_tolerance_boundary_matched(self):
        """VP level exactly at parent.high + tolerance is included."""
        # tolerance = 0.0005, parent.high = 1.1010
        # boundary = 1.1015
        parent = _candle("EURUSD", "H1", datetime(2026, 8, 10, 10, tzinfo=UTC),
                         "1.1010", "1.0990", "1.1000", duration_hours=1)
        vp = _make_vp_result(poc_price="1.1015", vah_price="1.2000", val_price="1.0000")
        vp_levels = volume_profile_levels(vp)
        result = _evaluate(parent, vp_levels=vp_levels)

        poc_matches = [m for m in result.matched_levels if m.level_type == "PREVIOUS_DAY_POC"]
        assert len(poc_matches) == 1

    def test_no_vp_levels_backward_compat(self):
        """Calling without VP levels produces identical result to before."""
        parent = _candle("EURUSD", "H1", datetime(2026, 8, 10, 10, tzinfo=UTC),
                         "1.1010", "1.0990", "1.1000", duration_hours=1)
        result_no_vp = _evaluate(parent, vp_levels=None)
        result_empty_vp = _evaluate(parent, vp_levels=[])
        assert result_no_vp.matched_level_count == result_empty_vp.matched_level_count
        assert result_no_vp.parent_at_htf_zone == result_empty_vp.parent_at_htf_zone

    def test_mixed_structural_and_vp_matches(self):
        """Both a structural level and a VP level can match simultaneously."""
        parent = _candle("EURUSD", "H1", datetime(2026, 8, 10, 10, tzinfo=UTC),
                         "1.1010", "1.0990", "1.1000", duration_hours=1)
        # Previous day high at 1.1014 (within tolerance)
        daily = [_candle("EURUSD", "D1", datetime(2026, 8, 9, tzinfo=UTC),
                         "1.1014", "1.0900", duration_hours=24)]
        weekly = [_candle("EURUSD", "W1", datetime(2026, 7, 27, tzinfo=UTC),
                          "1.4000", "1.3800", duration_hours=168)]
        h4 = [_candle("EURUSD", "H4", datetime(2026, 8, 8, tzinfo=UTC),
                       "1.2500", "1.2400", duration_hours=4)]
        # VP POC at 1.1000 (inside parent range)
        vp = _make_vp_result(poc_price="1.1000", vah_price="1.2000", val_price="1.0000")
        vp_levels = volume_profile_levels(vp)
        result = _evaluate(parent, vp_levels=vp_levels, daily=daily, weekly=weekly, h4=h4)

        assert result.parent_at_htf_zone is True
        types = {m.level_type for m in result.matched_levels}
        assert "PREVIOUS_DAY_HIGH" in types
        assert "PREVIOUS_DAY_POC" in types

    def test_vp_inside_parent_range_has_zero_distance(self):
        """VP level inside [parent.low, parent.high] has distance=0."""
        parent = _candle("EURUSD", "H1", datetime(2026, 8, 10, 10, tzinfo=UTC),
                         "1.1010", "1.0990", "1.1000", duration_hours=1)
        vp = _make_vp_result(poc_price="1.1000", vah_price="1.2000", val_price="1.0000")
        vp_levels = volume_profile_levels(vp)
        result = _evaluate(parent, vp_levels=vp_levels)

        poc = next(m for m in result.matched_levels if m.level_type == "PREVIOUS_DAY_POC")
        assert poc.distance == Decimal("0")

    def test_vp_outside_parent_range_but_within_tolerance_has_positive_distance(self):
        """VP level outside parent but within tolerance has nonzero distance."""
        parent = _candle("EURUSD", "H1", datetime(2026, 8, 10, 10, tzinfo=UTC),
                         "1.1010", "1.0990", "1.1000", duration_hours=1)
        vp = _make_vp_result(poc_price="1.1013", vah_price="1.2000", val_price="1.0000")
        vp_levels = volume_profile_levels(vp)
        result = _evaluate(parent, vp_levels=vp_levels)

        poc = next(m for m in result.matched_levels if m.level_type == "PREVIOUS_DAY_POC")
        assert poc.distance == Decimal("0.0003")
        assert poc.distance_atr is not None

    def test_multiple_vp_levels_can_match(self):
        """POC, VAH, and VAL can all match if within tolerance."""
        parent = _candle("EURUSD", "H1", datetime(2026, 8, 10, 10, tzinfo=UTC),
                         "1.1010", "1.0990", "1.1000", duration_hours=1)
        # All three prices inside parent range
        vp = _make_vp_result(poc_price="1.1000", vah_price="1.1005", val_price="1.0995")
        vp_levels = volume_profile_levels(vp)
        result = _evaluate(parent, vp_levels=vp_levels)

        vp_matches = [m for m in result.matched_levels
                      if m.level_type.endswith(("_POC", "_VAH", "_VAL"))]
        assert len(vp_matches) == 3

    def test_unknown_vp_does_not_affect_structural_result(self):
        """UNKNOWN VP → empty bridge list → evaluator returns structural-only."""
        parent = _candle("EURUSD", "H1", datetime(2026, 8, 10, 10, tzinfo=UTC),
                         "1.1010", "1.0990", "1.1000", duration_hours=1)
        vp_unknown = _make_vp_result(status=VolumeProfileStatus.UNKNOWN)
        vp_levels = volume_profile_levels(vp_unknown)
        assert vp_levels == []

        result_with = _evaluate(parent, vp_levels=vp_levels)
        result_without = _evaluate(parent, vp_levels=None)
        assert result_with.parent_at_htf_zone == result_without.parent_at_htf_zone
        assert result_with.matched_level_count == result_without.matched_level_count

    def test_previous_week_vp_levels_work(self):
        """PREVIOUS_WEEK VP levels are matched correctly."""
        parent = _candle("EURUSD", "H1", datetime(2026, 8, 10, 10, tzinfo=UTC),
                         "1.1010", "1.0990", "1.1000", duration_hours=1)
        vp = _make_vp_result(poc_price="1.1000", period_label="PREVIOUS_WEEK",
                             vah_price="1.2000", val_price="1.0000")
        vp_levels = volume_profile_levels(vp)
        result = _evaluate(parent, vp_levels=vp_levels)

        poc_matches = [m for m in result.matched_levels if m.level_type == "PREVIOUS_WEEK_POC"]
        assert len(poc_matches) == 1
