from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

from core.analytics.signal_replay_capture import ReplayCaptureService
from core.models.candle import Candle, CandleSequence
from core.storage.replay_repository import SQLiteReplaySnapshotRepository


def _fixture(*, include_confirmation: bool = True):
    now = datetime.now(timezone.utc)
    start = now - timedelta(hours=8)
    candles = []
    for index in range(6):
        open_time = start + timedelta(minutes=15 * index)
        candles.append(
            Candle(
                symbol="EURUSD",
                timeframe="M15",
                open_time=open_time,
                close_time=open_time + timedelta(minutes=15),
                open=Decimal("1.1000"),
                high=Decimal("1.1020"),
                low=Decimal("1.0980"),
                close=Decimal("1.1010"),
                is_closed=True,
            )
        )

    parent = candles[3]
    sweep = Candle(
        symbol="EURUSD",
        timeframe="M15",
        open_time=candles[4].open_time,
        close_time=candles[4].close_time,
        open=Decimal("1.1000"),
        high=Decimal("1.1040"),
        low=Decimal("1.0990"),
        close=Decimal("1.1010"),
        is_closed=True,
    )
    confirmation = Candle(
        symbol="EURUSD",
        timeframe="M15",
        open_time=candles[5].open_time,
        close_time=candles[5].close_time,
        open=Decimal("1.1010"),
        high=Decimal("1.1030"),
        low=Decimal("1.0995"),
        close=Decimal("1.1020"),
        is_closed=True,
    )
    candles[4] = sweep
    candles[5] = confirmation
    sequence = CandleSequence(tuple(candles), "EURUSD", "M15")
    metadata = SimpleNamespace(
        parent_candle=parent,
        sweep_candle=sweep,
        confirmation_candle=confirmation if include_confirmation else None,
    )
    detector = SimpleNamespace(detector="CRTDetector", metadata=metadata)
    alert = SimpleNamespace(
        alert_id="alert-1",
        created_at=now,
        classification="bullish_crt",
        direction="BUY",
        profile="day_trader",
        confidence_score=82.5,
        atr_quality=None,
        parent_at_htf_zone=None,
        key_level_evaluation={"status": "VALID", "matched_level_count": 1},
        quality_tightening=None,
        entry_zone_high=Decimal("1.1015"),
        entry_zone_low=Decimal("1.1005"),
        invalidation_level=Decimal("1.0975"),
        midpoint_target=Decimal("1.1030"),
        opposite_extreme_target=Decimal("1.1060"),
    )
    return alert, detector, sequence


def test_capture_uses_exact_closed_formation_and_is_idempotent(tmp_path):
    repository = SQLiteReplaySnapshotRepository(str(tmp_path / "journal.sqlite"))
    service = ReplayCaptureService(
        repository,
        context_bars_before_parent=3,
        outcome_forward_bars=50,
    )
    alert, detector, sequence = _fixture()

    first = service.capture(alert, detector, sequence)
    # A fresh service instance models scanner restart/reload behavior.
    second_service = ReplayCaptureService(
        repository,
        context_bars_before_parent=3,
        outcome_forward_bars=50,
    )
    second = second_service.capture(alert, detector, sequence)

    assert first is not None
    assert second is not None
    assert first.snapshot_hash == second.snapshot_hash
    assert [item.role.value for item in first.formation_candles] == [
        "CONTEXT",
        "CONTEXT",
        "CONTEXT",
        "PARENT",
        "SWEEP",
        "CONFIRMATION",
    ]
    assert first.parent_high == Decimal("1.1020")
    assert first.parent_low == Decimal("1.0980")
    assert first.source == "MT5"
    assert len(repository.list()) == 1


def test_capture_skips_missing_confirmation_without_persisting(tmp_path):
    repository = SQLiteReplaySnapshotRepository(str(tmp_path / "journal.sqlite"))
    service = ReplayCaptureService(repository, context_bars_before_parent=3)
    alert, detector, sequence = _fixture(include_confirmation=False)

    assert service.capture(alert, detector, sequence) is None
    assert repository.list() == []


def test_capture_skips_when_context_window_is_not_available(tmp_path):
    repository = SQLiteReplaySnapshotRepository(str(tmp_path / "journal.sqlite"))
    service = ReplayCaptureService(repository, context_bars_before_parent=30)
    alert, detector, sequence = _fixture()

    assert service.capture(alert, detector, sequence) is None
    assert repository.list() == []


def test_capture_keeps_only_higher_timeframe_candles_known_at_formation(tmp_path):
    repository = SQLiteReplaySnapshotRepository(str(tmp_path / "journal.sqlite"))
    service = ReplayCaptureService(repository, context_bars_before_parent=3)
    alert, detector, sequence = _fixture()
    first_open = sequence.candles[0].open_time - timedelta(hours=2)
    higher = CandleSequence(
        (
            Candle(
                symbol="EURUSD",
                timeframe="H1",
                open_time=first_open,
                close_time=first_open + timedelta(hours=1),
                open=Decimal("1.1000"),
                high=Decimal("1.1030"),
                low=Decimal("1.0970"),
                close=Decimal("1.1010"),
                is_closed=True,
            ),
            Candle(
                symbol="EURUSD",
                timeframe="H1",
                open_time=sequence.candles[-1].close_time,
                close_time=sequence.candles[-1].close_time + timedelta(hours=1),
                open=Decimal("1.1010"),
                high=Decimal("1.1040"),
                low=Decimal("1.0990"),
                close=Decimal("1.1020"),
                is_closed=True,
            ),
        ),
        "EURUSD",
        "H1",
    )

    snapshot = service.capture(
        alert,
        detector,
        sequence,
        higher_timeframe_sequence=higher,
    )

    assert snapshot is not None
    assert len(snapshot.higher_timeframe_candles) == 1
    assert snapshot.higher_timeframe_candles[0].candle.timeframe == "H1"
