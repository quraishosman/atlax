"""
Tests for VolumeProfileShadowLedger (Phase 4).

Follows the same pattern as test_key_level_shadow.py:
  - MemoryRuntime for persistence verification
  - Evaluation observation: status counts, confluence counts, context/period
  - Confidence observation: counterfactual scores, threshold crossings
  - Persistence across ledger instances

Authority:
    docs/40_VOLUME_PROFILE_CONFLUENCE_PLAN.md §7.1 (Shadow Stage)
    docs/40_VOLUME_PROFILE_CONFLUENCE_PLAN.md §9 Phase 4
    docs/decisions/ADR-017.md
"""

from datetime import datetime, timezone
from decimal import Decimal

from core.analytics.volume_profile_shadow import VolumeProfileShadowLedger
from core.models.volume_profile_result import (
    VolumeProfileLevel,
    VolumeProfileLevels,
    VolumeProfileResult,
    VolumeProfileStatus,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

UTC = timezone.utc


class MemoryRuntime:
    """In-memory runtime store for testing persistence."""

    def __init__(self):
        self._data = {}

    def get(self, key, default=None):
        return self._data.get(key, default)

    def set(self, key, value):
        self._data[key] = value


def _valid_result(
    *,
    symbol="EURUSD",
    period_label="PREVIOUS_DAY",
    source_timeframe="M15",
) -> VolumeProfileResult:
    return VolumeProfileResult(
        status=VolumeProfileStatus.VALID,
        symbol=symbol,
        period_label=period_label,
        period_start_utc=datetime(2026, 8, 15, tzinfo=UTC),
        period_end_utc=datetime(2026, 8, 16, tzinfo=UTC),
        source_timeframe=source_timeframe,
        volume_source="MT5_TICK_VOLUME",
        volume_quality="BROKER_TICK_PROXY",
        allocation_method="RANGE_UNIFORM",
        value_area_percent=0.70,
        configuration_snapshot_id="snap123",
        levels=VolumeProfileLevels(
            poc=VolumeProfileLevel("PREVIOUS_DAY_POC", Decimal("1.1000"), Decimal("5000")),
            vah=VolumeProfileLevel("PREVIOUS_DAY_VAH", Decimal("1.1050"), Decimal("2000")),
            val=VolumeProfileLevel("PREVIOUS_DAY_VAL", Decimal("1.0950"), Decimal("2000")),
            total_volume=Decimal("10000"),
            bin_width=Decimal("0.00010"),
            source_bars=96,
            valid_volume_bars=90,
        ),
    )


def _unknown_result(
    *,
    symbol="EURUSD",
    period_label="PREVIOUS_DAY",
) -> VolumeProfileResult:
    return VolumeProfileResult(
        status=VolumeProfileStatus.UNKNOWN,
        symbol=symbol,
        period_label=period_label,
        period_start_utc=datetime(2026, 8, 15, tzinfo=UTC),
        period_end_utc=datetime(2026, 8, 16, tzinfo=UTC),
        source_timeframe="M15",
        volume_source="MT5_TICK_VOLUME",
        volume_quality="UNKNOWN",
        allocation_method="RANGE_UNIFORM",
        value_area_percent=0.70,
        configuration_snapshot_id="snap123",
        levels=None,
        unknown_reason="insufficient_source_bars",
    )


# ---------------------------------------------------------------------------
# Evaluation observation tests
# ---------------------------------------------------------------------------


class TestShadowLedgerEvaluationObservation:
    def test_counts_status_and_confluence(self):
        """Three evaluations: VALID+match, VALID+no match, UNKNOWN."""
        store = MemoryRuntime()
        ledger = VolumeProfileShadowLedger(store)

        # VALID with 2 matched levels
        ledger.observe_evaluation(_valid_result(), matched_level_count=2)
        # VALID with 0 matched levels
        ledger.observe_evaluation(_valid_result(), matched_level_count=0)
        # UNKNOWN
        report = ledger.observe_evaluation(_unknown_result(), matched_level_count=0)

        assert report["evaluations"] == 3
        assert report["status_counts"] == {"VALID": 2, "UNKNOWN": 1}
        assert report["confluence_counts"] == {"true": 1, "false": 1, "unknown": 1}
        assert report["matched_level_count"] == 2
        assert report["rates"]["valid"] == 0.6667
        assert report["rates"]["confluence_true"] == 0.5

    def test_by_context_tracks_symbol_and_timeframe(self):
        store = MemoryRuntime()
        ledger = VolumeProfileShadowLedger(store)

        ledger.observe_evaluation(
            _valid_result(symbol="EURUSD", source_timeframe="M15"),
            matched_level_count=1,
        )
        ledger.observe_evaluation(
            _valid_result(symbol="XAUUSD", source_timeframe="M5"),
            matched_level_count=0,
        )

        report = ledger.report()
        assert "EURUSD|M15" in report["by_context"]
        assert "XAUUSD|M5" in report["by_context"]
        eurusd = report["by_context"]["EURUSD|M15"]
        assert eurusd["evaluations"] == 1
        assert eurusd["valid"] == 1
        assert eurusd["confluence_true"] == 1

    def test_by_period_tracks_day_and_week(self):
        store = MemoryRuntime()
        ledger = VolumeProfileShadowLedger(store)

        ledger.observe_evaluation(
            _valid_result(period_label="PREVIOUS_DAY"),
            matched_level_count=1,
        )
        ledger.observe_evaluation(
            _valid_result(period_label="PREVIOUS_WEEK"),
            matched_level_count=0,
        )

        report = ledger.report()
        assert report["by_period"]["PREVIOUS_DAY"]["confluence_true"] == 1
        assert report["by_period"]["PREVIOUS_WEEK"]["confluence_false"] == 1

    def test_unknown_evaluation_counts_as_confluence_unknown(self):
        store = MemoryRuntime()
        ledger = VolumeProfileShadowLedger(store)

        report = ledger.observe_evaluation(_unknown_result(), matched_level_count=0)
        assert report["confluence_counts"]["unknown"] == 1
        assert report["confluence_counts"]["true"] == 0
        assert report["confluence_counts"]["false"] == 0


# ---------------------------------------------------------------------------
# Confidence observation tests
# ---------------------------------------------------------------------------


class TestShadowLedgerConfidenceObservation:
    def test_records_counterfactual_scores(self):
        store = MemoryRuntime()
        ledger = VolumeProfileShadowLedger(store)

        report = ledger.observe_confidence(
            result=_valid_result(),
            current_score=62.0,
            score_if_true=74.0,
            score_if_false=58.0,
            score_without=58.0,
            minimum_threshold=60.0,
        )

        conf = report["confidence"]
        assert conf["samples"] == 1
        assert conf["if_true_total"] == 74.0
        assert conf["if_false_total"] == 58.0
        assert conf["without_total"] == 58.0
        assert conf["current_below_threshold"] == 0  # 62.0 >= 60.0
        assert conf["without_below_threshold"] == 1  # 58.0 < 60.0
        assert conf["threshold_crossings"] == 1

    def test_threshold_crossing_detected_both_directions(self):
        store = MemoryRuntime()
        ledger = VolumeProfileShadowLedger(store)

        # Current above, without below → crossing
        ledger.observe_confidence(
            result=_valid_result(),
            current_score=65.0,
            score_if_true=70.0,
            score_if_false=55.0,
            score_without=55.0,
            minimum_threshold=60.0,
        )
        # Current below, without above → crossing
        report = ledger.observe_confidence(
            result=_valid_result(),
            current_score=55.0,
            score_if_true=65.0,
            score_if_false=50.0,
            score_without=65.0,
            minimum_threshold=60.0,
        )

        assert report["confidence"]["threshold_crossings"] == 2

    def test_no_threshold_crossing_when_both_above(self):
        store = MemoryRuntime()
        ledger = VolumeProfileShadowLedger(store)

        report = ledger.observe_confidence(
            result=_valid_result(),
            current_score=70.0,
            score_if_true=75.0,
            score_if_false=65.0,
            score_without=65.0,
            minimum_threshold=60.0,
        )

        assert report["confidence"]["threshold_crossings"] == 0

    def test_average_delta_calculation(self):
        store = MemoryRuntime()
        ledger = VolumeProfileShadowLedger(store)

        ledger.observe_confidence(
            result=_valid_result(),
            current_score=70.0,
            score_if_true=80.0,
            score_if_false=60.0,
            score_without=60.0,
            minimum_threshold=60.0,
        )
        report = ledger.observe_confidence(
            result=_valid_result(),
            current_score=50.0,
            score_if_true=60.0,
            score_if_false=40.0,
            score_without=40.0,
            minimum_threshold=60.0,
        )

        conf = report["confidence"]
        # avg current = (70+50)/2 = 60.0
        # avg without = (60+40)/2 = 50.0
        assert conf["average_current"] == 60.0
        assert conf["average_without"] == 50.0
        assert conf["average_delta_current_vs_without"] == 10.0


# ---------------------------------------------------------------------------
# Persistence tests
# ---------------------------------------------------------------------------


class TestShadowLedgerPersistence:
    def test_state_survives_new_ledger_instance(self):
        """Simulates scanner restart — new ledger picks up old state."""
        store = MemoryRuntime()

        # First session
        ledger1 = VolumeProfileShadowLedger(store)
        ledger1.observe_evaluation(_valid_result(), matched_level_count=3)
        ledger1.observe_confidence(
            result=_valid_result(),
            current_score=62.0,
            score_if_true=74.0,
            score_if_false=58.0,
            score_without=58.0,
            minimum_threshold=60.0,
        )

        # Second session (same store, new ledger)
        ledger2 = VolumeProfileShadowLedger(store)
        report = ledger2.report()

        assert report["evaluations"] == 1
        assert report["matched_level_count"] == 3
        assert report["confidence"]["samples"] == 1
        assert report["confidence"]["threshold_crossings"] == 1

    def test_corrupted_store_returns_empty_state(self):
        """Malformed runtime data must not crash the scanner."""
        store = MemoryRuntime()
        store.set(VolumeProfileShadowLedger.STATE_KEY, "not a dict")

        ledger = VolumeProfileShadowLedger(store)
        report = ledger.report()

        assert report["evaluations"] == 0
        assert report["version"] == 1

    def test_updated_at_is_set_after_observation(self):
        store = MemoryRuntime()
        ledger = VolumeProfileShadowLedger(store)
        assert ledger.report()["updated_at"] is None

        ledger.observe_evaluation(_valid_result(), matched_level_count=0)
        report = ledger.report()
        assert report["updated_at"] is not None


# ---------------------------------------------------------------------------
# Report structure tests
# ---------------------------------------------------------------------------


class TestShadowLedgerReport:
    def test_report_contains_all_required_sections(self):
        store = MemoryRuntime()
        ledger = VolumeProfileShadowLedger(store)
        report = ledger.report()

        assert "version" in report
        assert "evaluations" in report
        assert "status_counts" in report
        assert "confluence_counts" in report
        assert "matched_level_count" in report
        assert "rates" in report
        assert "by_context" in report
        assert "by_period" in report
        assert "confidence" in report

    def test_empty_report_has_safe_defaults(self):
        store = MemoryRuntime()
        ledger = VolumeProfileShadowLedger(store)
        report = ledger.report()

        assert report["evaluations"] == 0
        assert report["rates"]["valid"] == 0.0
        assert report["rates"]["confluence_true"] == 0.0
        assert report["confidence"]["samples"] == 0
        assert report["confidence"]["average_current"] is None
        assert report["confidence"]["threshold_crossing_rate"] == 0.0
