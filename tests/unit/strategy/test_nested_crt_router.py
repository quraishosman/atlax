from datetime import datetime, timedelta, timezone
from decimal import Decimal

from core.models.candle import Candle
from core.models.detector_output import CRTMetadata, DetectorOutput
from core.operations.monitoring import OperationalMetricsLedger
from core.settings.nested_crt_config import (
    NestedCrtRelationshipConfig,
    NestedCrtRoutingConfig,
    load_nested_crt_routing_config,
)
from core.strategy.nested_crt_router import NestedCrtRouter


class MemoryRuntime:
    def __init__(self):
        self.values = {}

    def get(self, key, default=None):
        return self.values.get(key, default)

    def set(self, key, value):
        self.values[key] = value


def _config(shadow_mode=False):
    return NestedCrtRoutingConfig(
        enabled=True,
        shadow_mode=shadow_mode,
        relationships=(
            NestedCrtRelationshipConfig(
                controller_timeframe="M15",
                child_timeframes=("M5",),
                same_timeframe_policy="suppress_inside",
                child_policy="allow_aligned_only",
                containment_mode="parent_range_and_child_closes",
            ),
        ),
    )


def _candle(timeframe, open_time, open_, high, low, close):
    return Candle(
        symbol="EURUSD",
        timeframe=timeframe,
        open_time=open_time,
        close_time=open_time + timedelta(minutes=15 if timeframe == "M15" else 5),
        open=Decimal(str(open_)),
        high=Decimal(str(high)),
        low=Decimal(str(low)),
        close=Decimal(str(close)),
        is_closed=True,
    )


def _output(timeframe, classification, start, *, parent, sweep, confirmation=None):
    metadata = CRTMetadata(
        parent_candle=parent,
        sweep_candle=sweep,
        confirmation_candle=confirmation,
        swept_level="CRL" if classification == "bullish_crt" else "CRH",
        crt_high=parent.high,
        crt_low=parent.low,
        midpoint_target=parent.midpoint,
        opposite_extreme_target=parent.high if classification == "bullish_crt" else parent.low,
        sweep_distance_pips=1.0,
        close_location_ratio=0.5,
        sweep_wick_ratio=0.2,
        parent_at_htf_zone=None,
        session_alignment=None,
        htf_trend_alignment=None,
        fvg_present_after_sweep=None,
        msb_on_ltf=None,
    )
    return DetectorOutput(
        detector="CRTDetector",
        symbol="EURUSD",
        timeframe=timeframe,
        detected=True,
        timestamp=sweep.close_time,
        classification=classification,
        reason="test",
        invalidation_reason=None,
        metadata=metadata,
    )


def _controller():
    start = datetime(2026, 8, 14, 10, 0, tzinfo=timezone.utc)
    return _output(
        "M15",
        "bullish_crt",
        start,
        parent=_candle("M15", start, 105, 110, 100, 106),
        sweep=_candle("M15", start + timedelta(minutes=15), 106, 108, 99, 103),
    )


def test_nested_router_suppresses_same_timeframe_and_opposite_child():
    runtime = MemoryRuntime()
    metrics = OperationalMetricsLedger(runtime)
    router = NestedCrtRouter(_config(), runtime, monitoring=metrics)
    assert router.route(_controller()).reason_code == "nested_controller_started"
    assert router.route(_controller()).reason_code == "nested_controller_already_active"

    child_start = datetime(2026, 8, 14, 10, 35, tzinfo=timezone.utc)
    m15_inside = _output(
        "M15",
        "bearish_crt",
        child_start,
        parent=_candle("M15", child_start, 104, 107, 102, 103),
        sweep=_candle("M15", child_start + timedelta(minutes=15), 103, 108, 101, 104),
    )
    assert router.route(m15_inside).reason_code == "nested_m15_contained"

    m5_opposite = _output(
        "M5",
        "bearish_crt",
        child_start,
        parent=_candle("M5", child_start, 104, 107, 102, 103),
        sweep=_candle("M5", child_start + timedelta(minutes=5), 103, 108, 101, 104),
    )
    decision = router.route(m5_opposite)
    assert decision.suppress
    assert decision.reason_code == "nested_m5_opposing_bias"

    report = metrics.report()["nested_crt"]
    assert report["suppressed"] == 2
    assert report["by_reason"] == {
        "nested_m15_contained": 1,
        "nested_m5_opposing_bias": 1,
    }


def test_aligned_child_allows_wick_outside_and_persists_controller():
    runtime = MemoryRuntime()
    router = NestedCrtRouter(_config(), runtime)
    router.route(_controller())
    child_start = datetime(2026, 8, 14, 10, 35, tzinfo=timezone.utc)
    aligned = _output(
        "M5",
        "bullish_crt",
        child_start,
        parent=_candle("M5", child_start, 104, 107, 102, 103),
        sweep=_candle("M5", child_start + timedelta(minutes=5), 103, 108, 99, 104),
    )
    decision = router.route(aligned)
    assert decision.action == "ALLOW"
    assert decision.reason_code == "nested_m5_aligned_bias"
    assert decision.contained

    restored = NestedCrtRouter(_config(), runtime)
    assert len(restored.report()) == 1


def test_non_contained_m15_replaces_controller_and_mitigation_expires_it():
    runtime = MemoryRuntime()
    router = NestedCrtRouter(_config(), runtime)
    router.route(_controller())
    start = datetime(2026, 8, 14, 11, 0, tzinfo=timezone.utc)
    outside = _output(
        "M15",
        "bearish_crt",
        start,
        parent=_candle("M15", start, 115, 120, 111, 114),
        sweep=_candle("M15", start + timedelta(minutes=15), 114, 121, 112, 116),
    )
    assert router.route(outside).reason_code == "nested_controller_replaced_outside"

    later = _candle("M15", start + timedelta(minutes=30), 116, 119, 109, 110)
    from core.models.candle import CandleSequence

    router.update_market(CandleSequence((later,), "EURUSD", "M15"))
    assert router.report() == {}


def test_shadow_mode_records_would_be_suppression_without_blocking():
    runtime = MemoryRuntime()
    metrics = OperationalMetricsLedger(runtime)
    router = NestedCrtRouter(_config(shadow_mode=True), runtime, monitoring=metrics)
    router.route(_controller())
    start = datetime(2026, 8, 14, 10, 35, tzinfo=timezone.utc)
    opposing = _output(
        "M5",
        "bearish_crt",
        start,
        parent=_candle("M5", start, 104, 107, 102, 103),
        sweep=_candle("M5", start + timedelta(minutes=5), 103, 108, 101, 104),
    )
    decision = router.route(opposing)
    assert decision.action == "ALLOW"
    assert decision.shadow
    report = metrics.report()["nested_crt"]
    assert report["suppressed"] == 0
    assert report["shadow_suppressed"] == 1


def test_nested_routing_config_loads_from_project_config():
    config = load_nested_crt_routing_config("config/atlax.yaml")
    assert config.enabled is True
    assert config.shadow_mode is False
    assert config.relationships[0].controller_timeframe == "M15"
    assert config.relationships[0].child_timeframes == ("M5",)
