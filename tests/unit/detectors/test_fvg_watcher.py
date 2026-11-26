from datetime import datetime, timezone
from decimal import Decimal

from core.detectors.fvg.detector import FVGResult
from core.detectors.fvg.watcher import FVGWatcher


class MemoryState:
    def __init__(self):
        self.values = {}

    def get(self, key, default=None):
        return self.values.get(key, default)

    def set(self, key, value):
        self.values[key] = value


def test_canonical_fvg_watch_registration_preserves_symbol_and_triggers():
    state = MemoryState()
    watcher = FVGWatcher(max_age_seconds=300, state_store=state)
    fvg = FVGResult(
        found=True,
        direction="bullish",
        fvg_high=Decimal("1.10500"),
        fvg_low=Decimal("1.10000"),
        formed_at=datetime(2026, 8, 14, 10, 0, tzinfo=timezone.utc),
        ltf="M5",
    )

    watch = watcher.add_watch_full(
        symbol="EURUSD",
        fvg=fvg,
        candidate_id="candidate-1",
        confidence_score=85.0,
        profile="day_trader",
        entry_zone_high=Decimal("1.10400"),
        entry_zone_low=Decimal("1.10100"),
        invalidation_level=Decimal("1.09800"),
        midpoint_target=Decimal("1.10700"),
        opposite_extreme_target=Decimal("1.11000"),
        htf_timeframe="M15",
    )

    assert watch.symbol == "EURUSD"
    assert watcher.active_watch_count() == 1
    triggered = watcher.check_live_price("EURUSD", live_bid=1.10400, live_ask=1.10450)
    assert [item.candidate_id for item in triggered] == ["candidate-1"]


def test_triggered_watch_preserves_subscriber_routing_context():
    state = MemoryState()
    watcher = FVGWatcher(max_age_seconds=300, state_store=state)
    fvg = FVGResult(
        found=True,
        direction="bullish",
        fvg_high=Decimal("1.10500"),
        fvg_low=Decimal("1.10000"),
        formed_at=datetime(2026, 8, 14, 10, 0, tzinfo=timezone.utc),
        ltf="M5",
    )

    watcher.add_watch_full(
        symbol="EURUSD",
        fvg=fvg,
        candidate_id="candidate-routing-context",
        confidence_score=80.0,
        profile="day_trader",
        entry_zone_high=Decimal("1.10400"),
        entry_zone_low=Decimal("1.10100"),
        invalidation_level=Decimal("1.09800"),
        midpoint_target=Decimal("1.10700"),
        opposite_extreme_target=Decimal("1.11000"),
        htf_timeframe="M15",
        parent_at_htf_zone=True,
        profile_min_confidence=55.0,
        quality_status="VALID",
        quality_would_suppress=False,
    )

    triggered = watcher.check_live_price("EURUSD", live_bid=1.10400, live_ask=1.10450)

    assert len(triggered) == 1
    assert triggered[0].parent_at_htf_zone is True
    assert triggered[0].profile_min_confidence == 55.0
    assert triggered[0].quality_status == "VALID"
    assert triggered[0].quality_would_suppress is False


def test_clear_watches_removes_pending_deferred_entries():
    state = MemoryState()
    watcher = FVGWatcher(max_age_seconds=300, state_store=state)
    fvg = FVGResult(
        found=True,
        direction="bearish",
        fvg_high=Decimal("1.10500"),
        fvg_low=Decimal("1.10000"),
        formed_at=datetime(2026, 8, 14, 10, 0, tzinfo=timezone.utc),
        ltf="M5",
    )

    watcher.add_watch_full(
        symbol="EURUSD",
        fvg=fvg,
        candidate_id="stale-deferred-entry",
        confidence_score=80.0,
        profile="day_trader",
        entry_zone_high=Decimal("1.10400"),
        entry_zone_low=Decimal("1.10100"),
        invalidation_level=Decimal("1.10800"),
        midpoint_target=Decimal("1.09700"),
        opposite_extreme_target=Decimal("1.09400"),
        htf_timeframe="M15",
    )

    assert watcher.clear_watches("execution_gate_disabled") == 1
    assert watcher.active_watch_count() == 0
    assert state.get("detector.fvg_watches") == []
    assert watcher.check_live_price("EURUSD", live_bid=1.10200, live_ask=1.10250) == []
