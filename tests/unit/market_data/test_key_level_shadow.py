from datetime import datetime, timezone
from decimal import Decimal

from core.market_data.key_level_shadow import KeyLevelShadowLedger
from core.models.key_level import KeyLevelEvaluation


class MemoryRuntime:
    def __init__(self):
        self.values = {}

    def get(self, key, default=None):
        return self.values.get(key, default)

    def set(self, key, value):
        self.values[key] = value


def _evaluation(zone):
    return KeyLevelEvaluation(
        status="VALID",
        parent_at_htf_zone=zone,
        symbol="EURUSD",
        parent_timeframe="H1",
        parent_open_time=datetime(2026, 8, 14, tzinfo=timezone.utc),
        tolerance=Decimal("0.0010"),
        tolerance_atr_multiplier=Decimal("0.10"),
        minimum_tolerance_pips=Decimal("5"),
        matched_levels=(),
        missing_sources=(),
        configuration_snapshot_id="snapshot",
        explanation="test",
    )


def _unknown_evaluation():
    return KeyLevelEvaluation(
        status="UNKNOWN",
        parent_at_htf_zone=None,
        symbol="EURUSD",
        parent_timeframe="H1",
        parent_open_time=datetime(2026, 8, 14, tzinfo=timezone.utc),
        tolerance=None,
        tolerance_atr_multiplier=Decimal("0.10"),
        minimum_tolerance_pips=Decimal("5"),
        matched_levels=(),
        missing_sources=("previous_day",),
        configuration_snapshot_id="snapshot",
        explanation="missing source",
    )


def test_shadow_ledger_counts_status_zones_and_contexts():
    runtime = MemoryRuntime()
    ledger = KeyLevelShadowLedger(runtime)

    ledger.observe_evaluation(_evaluation(True))
    ledger.observe_evaluation(_evaluation(False))
    report = ledger.observe_evaluation(_unknown_evaluation())

    assert report["evaluations"] == 3
    assert report["status_counts"] == {"VALID": 2, "UNKNOWN": 1}
    assert report["zone_counts"] == {"true": 1, "false": 1, "unknown": 1}
    assert report["rates"]["zone_true"] == 0.5
    assert report["by_context"]["EURUSD|H1"]["evaluations"] == 3
    assert report["by_context"]["EURUSD|H1"]["unknown"] == 1


def test_shadow_ledger_persists_and_records_hypothetical_scores():
    runtime = MemoryRuntime()
    first = KeyLevelShadowLedger(runtime)
    evaluation = _evaluation(True)
    first.observe_evaluation(evaluation)
    first.observe_confidence(
        evaluation=evaluation,
        current_score=62.0,
        score_if_true=74.0,
        score_if_false=58.0,
        score_without=58.0,
        minimum_threshold=60.0,
    )

    second = KeyLevelShadowLedger(runtime)
    report = second.report()
    assert report["confidence"]["samples"] == 1
    assert report["confidence"]["if_true_total"] == 74.0
    assert report["confidence"]["if_false_total"] == 58.0
    assert report["confidence"]["without_total"] == 58.0
    assert report["confidence"]["current_below_threshold"] == 0
    assert report["confidence"]["without_below_threshold"] == 1
    assert report["confidence"]["threshold_crossings"] == 1
    assert report["confidence"]["average_delta_current_vs_without"] == 4.0
