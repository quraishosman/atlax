import sqlite3
from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal

from core.analytics.journal import SQLiteJournal
from core.analytics.signal_replay_capture import ReplayCaptureService
from core.analytics.signal_replay_reporting import build_signal_replay_report
from core.models.signal_replay import ReplayLifecycleStatus, ReplayOutcome
from core.storage.replay_repository import SQLiteReplaySnapshotRepository

from tests.unit.analytics.test_signal_replay_capture import _fixture


def test_report_joins_passport_coverage_and_public_dimensions(tmp_path):
    db_path = str(tmp_path / "journal.sqlite")
    journal = SQLiteJournal(db_path)
    repository = SQLiteReplaySnapshotRepository(db_path)
    alert, detector, sequence = _fixture()
    journal.add_signal_entry(
        record_id=alert.alert_id,
        candidate_id="candidate-1",
        symbol="EURUSD",
        timeframe="M15",
        profile="day_trader",
        direction="BUY",
        confidence_score=82.0,
        confidence_breakdown={},
        entry_price=Decimal("1.1010"),
        sl_price=Decimal("1.0975"),
        tp_price=Decimal("1.1060"),
    )
    snapshot = ReplayCaptureService(repository, context_bars_before_parent=3).capture(
        alert,
        detector,
        sequence,
        rule_context={
            "authorized_chat_ids": ["123"],
            "delivery_routes": ["premium"],
        },
    )
    assert snapshot is not None
    resolved = replace(
        snapshot,
        lifecycle_status=ReplayLifecycleStatus.RESOLVED,
        final_outcome=ReplayOutcome.WIN,
        outcome_at=datetime.now(timezone.utc),
    )
    repository.update_outcome(resolved)

    report = build_signal_replay_report(db_path, days=30, repository=repository)

    assert report["status"] == "OK"
    assert report["passport_total_signals"] == 1
    assert report["replay_available_count"] == 1
    assert report["replay_coverage_pct"] == 100.0
    assert report["outcomes"]["wins"] == 1
    assert report["by_pair"]["EURUSD"]["win_rate_pct"] == 100.0
    assert report["by_timeframe"]["M15"]["snapshots"] == 1
    assert report["by_style"]["day_trader"]["snapshots"] == 1
    assert report["by_route"]["premium"]["snapshots"] == 1


def test_report_returns_unknown_when_repository_is_unavailable(tmp_path):
    db_path = str(tmp_path / "journal.sqlite")
    journal = SQLiteJournal(db_path)
    journal.add_signal_entry(
        record_id="signal-1",
        candidate_id="candidate-1",
        symbol="EURUSD",
        timeframe="M15",
        profile="day_trader",
        direction="BUY",
        confidence_score=80.0,
        confidence_breakdown={},
        entry_price=Decimal("1.1000"),
        sl_price=Decimal("1.0900"),
        tp_price=Decimal("1.1200"),
    )

    class BrokenRepository:
        def list(self):
            raise RuntimeError("storage unavailable")

    report = build_signal_replay_report(
        db_path,
        days=30,
        repository=BrokenRepository(),
    )
    assert report["status"] == "UNKNOWN"


def test_report_does_not_initialize_missing_replay_schema(tmp_path):
    db_path = str(tmp_path / "journal.sqlite")
    journal = SQLiteJournal(db_path)
    journal.add_signal_entry(
        record_id="signal-1",
        candidate_id="candidate-1",
        symbol="EURUSD",
        timeframe="M15",
        profile="day_trader",
        direction="BUY",
        confidence_score=80.0,
        confidence_breakdown={},
        entry_price=Decimal("1.1000"),
        sl_price=Decimal("1.0900"),
        tp_price=Decimal("1.1200"),
    )

    report = build_signal_replay_report(db_path, days=30)

    assert report["status"] == "UNKNOWN"
    with sqlite3.connect(db_path) as connection:
        assert connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='signal_replay_snapshots'"
        ).fetchone() is None
