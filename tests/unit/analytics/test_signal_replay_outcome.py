from datetime import timedelta
from decimal import Decimal

from core.analytics.signal_replay_capture import ReplayCaptureService
from core.analytics.signal_replay_outcome import ReplayOutcomeService
from core.models.candle import Candle, CandleSequence
from core.models.signal_replay import ReplayLifecycleStatus, ReplayOutcome
from core.storage.replay_repository import SQLiteReplaySnapshotRepository

from tests.unit.analytics.test_signal_replay_capture import _fixture


class FakeJournal:
    def __init__(self, state=None):
        self.state = state

    def get_signal_outcome(self, record_id):
        return self.state


def _captured_snapshot(tmp_path):
    repository = SQLiteReplaySnapshotRepository(str(tmp_path / "journal.sqlite"))
    alert, detector, formation = _fixture()
    capture = ReplayCaptureService(
        repository,
        context_bars_before_parent=3,
        outcome_forward_bars=2,
    )
    snapshot = capture.capture(alert, detector, formation)
    assert snapshot is not None
    return repository, snapshot


def _outcome_sequence(snapshot):
    start = snapshot.formation_cutoff_at
    candles = []
    for index in range(2):
        open_time = start + timedelta(minutes=15 * index)
        candles.append(
            Candle(
                symbol=snapshot.symbol,
                timeframe=snapshot.source_timeframe,
                open_time=open_time,
                close_time=open_time + timedelta(minutes=15),
                open=Decimal("1.1020"),
                high=Decimal("1.1040"),
                low=Decimal("1.1000"),
                close=Decimal("1.1030"),
                is_closed=True,
            )
        )
    return CandleSequence(tuple(candles), snapshot.symbol, snapshot.source_timeframe)


def test_outcome_service_appends_closed_candles_without_claiming_result(tmp_path):
    repository, snapshot = _captured_snapshot(tmp_path)
    journal = FakeJournal(
        {
            "record_id": snapshot.signal_id,
            "symbol": snapshot.symbol,
            "timeframe": snapshot.source_timeframe,
            "signal_status": "OPEN",
            "final_outcome": "UNKNOWN",
            "exit_reason": "signal_pending",
            "outcome_at": None,
        }
    )

    assert ReplayOutcomeService(repository, journal).sync_sequence(
        _outcome_sequence(snapshot)
    ) == 1
    updated = repository.get(snapshot.signal_id)
    assert updated is not None
    assert len(updated.outcome_candles) == 2
    assert updated.lifecycle_status is ReplayLifecycleStatus.OPEN
    assert updated.final_outcome is ReplayOutcome.UNKNOWN


def test_outcome_service_applies_win_and_never_rewrites_terminal_result(tmp_path):
    repository, snapshot = _captured_snapshot(tmp_path)
    sequence = _outcome_sequence(snapshot)
    journal = FakeJournal(
        {
            "record_id": snapshot.signal_id,
            "symbol": snapshot.symbol,
            "timeframe": snapshot.source_timeframe,
            "signal_status": "RESOLVED",
            "final_outcome": "WIN",
            "exit_reason": "tp2_hit",
            "invalidation_reason": "",
            "outcome_at": sequence.candles[0].close_time.isoformat(),
        }
    )
    service = ReplayOutcomeService(repository, journal)

    assert service.sync_sequence(sequence) == 1
    resolved = repository.get(snapshot.signal_id)
    assert resolved is not None
    assert resolved.lifecycle_status is ReplayLifecycleStatus.RESOLVED
    assert resolved.final_outcome is ReplayOutcome.WIN

    journal.state.update(
        {
            "final_outcome": "LOSS",
            "exit_reason": "sl_hit",
            "invalidation_reason": "stop_loss_hit",
        }
    )
    assert service.sync_sequence(sequence) == 0
    unchanged = repository.get(snapshot.signal_id)
    assert unchanged is not None
    assert unchanged.final_outcome is ReplayOutcome.WIN


def test_inconsistent_journal_state_becomes_unknown(tmp_path):
    repository, snapshot = _captured_snapshot(tmp_path)
    journal = FakeJournal(
        {
            "record_id": snapshot.signal_id,
            "symbol": snapshot.symbol,
            "timeframe": snapshot.source_timeframe,
            "signal_status": "RESOLVED",
            "final_outcome": "WIN",
            "exit_reason": "sl_hit",
            "outcome_at": "2026-08-16T12:00:00+00:00",
        }
    )

    assert ReplayOutcomeService(repository, journal).sync_sequence(
        _outcome_sequence(snapshot)
    ) == 1
    updated = repository.get(snapshot.signal_id)
    assert updated is not None
    assert updated.lifecycle_status is ReplayLifecycleStatus.UNKNOWN
    assert updated.final_outcome is ReplayOutcome.UNKNOWN


def test_expiry_is_mirrored_without_claiming_win_or_loss(tmp_path):
    repository, snapshot = _captured_snapshot(tmp_path)
    journal = FakeJournal(
        {
            "record_id": snapshot.signal_id,
            "symbol": snapshot.symbol,
            "timeframe": snapshot.source_timeframe,
            "signal_status": "EXPIRED",
            "final_outcome": "UNKNOWN",
            "exit_reason": "signal_expired",
            "outcome_at": snapshot.formation_cutoff_at.isoformat(),
        }
    )

    assert ReplayOutcomeService(repository, journal).sync_sequence(
        _outcome_sequence(snapshot)
    ) == 1
    updated = repository.get(snapshot.signal_id)
    assert updated is not None
    assert updated.lifecycle_status is ReplayLifecycleStatus.EXPIRED
    assert updated.final_outcome is ReplayOutcome.UNKNOWN
