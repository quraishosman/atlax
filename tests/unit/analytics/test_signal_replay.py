from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from core.models.candle import Candle
from core.models.signal_replay import (
    ReplayAvailability,
    ReplayCandle,
    ReplayCandleRole,
    ReplayLifecycleStatus,
    ReplayOutcome,
    ReplaySnapshot,
)


UTC = timezone.utc


def _candle(index: int, *, role: ReplayCandleRole, timeframe: str = "M15") -> ReplayCandle:
    start = datetime(2026, 8, 17, 8, 0, tzinfo=UTC) + timedelta(minutes=index * 15)
    base = Decimal("1.1000") + Decimal(index) / Decimal("100000")
    candle = Candle(
        symbol="EURUSD",
        timeframe=timeframe,
        open_time=start,
        close_time=start + timedelta(minutes=15),
        open=base,
        high=base + Decimal("0.0010"),
        low=base - Decimal("0.0010"),
        close=base + Decimal("0.0005"),
        is_closed=True,
    )
    return ReplayCandle(candle, role)


def _snapshot(**overrides) -> ReplaySnapshot:
    formation = tuple(
        [_candle(index, role=ReplayCandleRole.CONTEXT) for index in range(30)]
        + [
            _candle(30, role=ReplayCandleRole.PARENT),
            _candle(31, role=ReplayCandleRole.SWEEP),
            _candle(32, role=ReplayCandleRole.CONFIRMATION),
        ]
    )
    values = {
        "snapshot_id": "snapshot-1",
        "signal_id": "signal-1",
        "snapshot_version": "1.0",
        "symbol": "EURUSD",
        "source_timeframe": "M15",
        "source": "MT5",
        "created_at": datetime(2026, 8, 17, 16, 15, 1, tzinfo=UTC),
        "captured_at": datetime(2026, 8, 17, 16, 15, 2, tzinfo=UTC),
        "formation_cutoff_at": formation[-1].candle.close_time,
        "context_bars_before_parent": 30,
        "outcome_forward_bars": 50,
        "formation_candles": formation,
        "outcome_candles": (_candle(33, role=ReplayCandleRole.OUTCOME),),
        "rule_context": {"rulebook_version": "1.0", "quality": "A"},
        "availability": ReplayAvailability.AVAILABLE,
        "lifecycle_status": ReplayLifecycleStatus.OPEN,
        "final_outcome": ReplayOutcome.UNKNOWN,
    }
    values.update(overrides)
    return ReplaySnapshot(**values)


def test_snapshot_marks_exact_three_candle_sequence_and_parent_range():
    snapshot = _snapshot()

    assert snapshot.parent_index == 30
    assert snapshot.sweep_index == 31
    assert snapshot.confirmation_index == 32
    assert snapshot.parent_high == snapshot.formation_candles[30].candle.high
    assert snapshot.parent_low == snapshot.formation_candles[30].candle.low
    assert snapshot.to_payload()["parent_high"] == str(snapshot.parent_high)
    assert len(snapshot.snapshot_hash) == 64


def test_snapshot_hash_is_deterministic():
    first = _snapshot()
    second = _snapshot()

    assert first.snapshot_hash == second.snapshot_hash
    assert first.to_payload() == second.to_payload()


def test_snapshot_rejects_ambiguous_or_nonconsecutive_roles():
    formation = list(_snapshot().formation_candles)
    formation[31] = ReplayCandle(formation[31].candle, ReplayCandleRole.CONTEXT)

    with pytest.raises(ValueError, match="exactly one parent, sweep, and confirmation"):
        _snapshot(formation_candles=tuple(formation))


def test_snapshot_rejects_forming_candles():
    source = _candle(30, role=ReplayCandleRole.PARENT).candle
    forming = Candle(
        symbol=source.symbol,
        timeframe=source.timeframe,
        open_time=source.open_time,
        close_time=source.close_time,
        open=source.open,
        high=source.high,
        low=source.low,
        close=source.close,
        is_closed=False,
    )

    with pytest.raises(ValueError, match="closed candles only"):
        ReplayCandle(forming, ReplayCandleRole.PARENT)


def test_snapshot_rejects_future_candle_in_formation_and_wrong_source():
    formation = list(_snapshot().formation_candles)
    cutoff = formation[-1].candle.close_time
    later = _candle(33, role=ReplayCandleRole.CONFIRMATION)
    formation[32] = later

    with pytest.raises(ValueError, match="formation_cutoff_at cannot precede"):
        _snapshot(formation_candles=tuple(formation), formation_cutoff_at=cutoff)

    with pytest.raises(ValueError, match="must use MT5"):
        _snapshot(source="TradingView")


def test_snapshot_rejects_outcome_before_cutoff_or_beyond_limit():
    snapshot = _snapshot()
    before_cutoff = _candle(32, role=ReplayCandleRole.OUTCOME)
    with pytest.raises(ValueError, match="outcome candles must begin"):
        _snapshot(outcome_candles=(before_cutoff,))

    too_many = tuple(
        [_candle(index, role=ReplayCandleRole.OUTCOME) for index in range(33, 35)]
    )
    with pytest.raises(ValueError, match="exceed outcome_forward_bars"):
        _snapshot(outcome_candles=too_many, outcome_forward_bars=1)


def test_resolved_snapshot_requires_outcome_and_clears_win_reason():
    outcome_at = datetime(2026, 8, 17, 17, 0, tzinfo=UTC)
    resolved = _snapshot(
        lifecycle_status=ReplayLifecycleStatus.RESOLVED,
        final_outcome=ReplayOutcome.WIN,
        outcome_at=outcome_at,
    )
    assert resolved.to_payload()["final_outcome"] == "WIN"

    with pytest.raises(ValueError, match="winning replay cannot contain"):
        _snapshot(
            lifecycle_status=ReplayLifecycleStatus.RESOLVED,
            final_outcome=ReplayOutcome.WIN,
            outcome_at=outcome_at,
            invalidation_reason="stop_loss_hit",
        )


def test_snapshot_rejects_time_and_outcome_ordering_contradictions():
    snapshot = _snapshot()

    with pytest.raises(ValueError, match="created_at cannot precede"):
        _snapshot(created_at=snapshot.formation_cutoff_at - timedelta(seconds=1))

    with pytest.raises(ValueError, match="non-resolved replay cannot claim"):
        _snapshot(final_outcome=ReplayOutcome.LOSS)
