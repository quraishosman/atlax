from datetime import datetime, timezone
from decimal import Decimal

from core.analytics.journal import SQLiteJournal
from core.analytics.realized_pnl import RealizedPnl
from core.models.execution import ExecutionRecord, ExecutionStatus, MT5RequestPayload
from core.models.execution_quality import (
    ExecutionQualityEvent,
    ExecutionQualityEventType,
)


def _record(record_id: str = "execution-1") -> ExecutionRecord:
    now = datetime.now(timezone.utc)
    return ExecutionRecord(
        record_id=record_id,
        candidate_id="candidate-1",
        profile="day_trader",
        symbol="EURUSD",
        timeframe="M15",
        direction="BUY",
        lot_size=Decimal("0.10"),
        risk_evaluation_id="candidate-1",
        mt5_request=MT5RequestPayload(
            action="TRADE_ACTION_DEAL",
            symbol="EURUSD",
            volume="0.10",
            order_type="ORDER_TYPE_BUY",
            price=None,
            sl="1.09000",
            tp="1.11000",
            deviation=20,
            type_filling="ORDER_FILLING_IOC",
            magic=8801,
            comment="ATLAX_candidate-1",
        ),
        preflight_result="10009: done",
        order_send_retcode=10009,
        mt5_order_id=123,
        mt5_deal_id=456,
        broker_comment="done",
        status=ExecutionStatus.FILLED,
        error_reason=None,
        recovery_suggestion=None,
        attempted_at=now,
        completed_at=now,
    )


def test_execution_record_is_persisted_and_linked_by_candidate(tmp_path):
    journal = SQLiteJournal(str(tmp_path / "journal.sqlite"))

    journal.record_execution(_record())

    stored = journal.get_execution_record("execution-1")
    assert stored is not None
    assert stored["candidate_id"] == "candidate-1"
    assert stored["execution_status"] == "FILLED"
    assert stored["mt5_order_id"] == 123
    assert stored["mt5_deal_id"] == 456
    assert stored["mt5_request"]["comment"] == "ATLAX_candidate-1"


def test_execution_record_write_is_idempotent_by_record_id(tmp_path):
    journal = SQLiteJournal(str(tmp_path / "journal.sqlite"))
    record = _record()

    journal.record_execution(record)
    journal.record_execution(record)

    with journal._get_connection() as connection:
        count = connection.execute(
            "SELECT COUNT(*) FROM execution_records WHERE candidate_id = ?",
            ("candidate-1",),
        ).fetchone()[0]
    assert count == 1


def test_execution_quality_event_is_created_once_for_an_execution_record(tmp_path):
    journal = SQLiteJournal(str(tmp_path / "journal.sqlite"))
    record = _record()

    journal.record_execution(record)
    journal.record_execution(record)

    events = journal.list_execution_quality_events("execution-1")
    assert len(events) == 1
    assert events[0]["event_type"] == ExecutionQualityEventType.EXECUTION_RESULT
    assert events[0]["execution_record_id"] == "execution-1"
    assert events[0]["broker_retcode"] == 10009
    assert events[0]["metadata"] == {}


def test_quality_event_write_is_idempotent_by_event_id(tmp_path):
    journal = SQLiteJournal(str(tmp_path / "journal.sqlite"))
    event = ExecutionQualityEvent.new(
        ExecutionQualityEventType.CIRCUIT_BREAKER,
        event_id="quality-event-1",
        status="ARMED",
        action="risk_clear",
    )

    journal.record_execution_quality_event(event)
    journal.record_execution_quality_event(event)

    events = journal.list_execution_quality_events(limit=10)
    assert len(events) == 1
    assert events[0]["event_id"] == "quality-event-1"


def test_realization_adds_position_realized_quality_event(tmp_path):
    journal = SQLiteJournal(str(tmp_path / "journal.sqlite"))
    journal.record_execution(_record())
    assert journal.link_execution_position("execution-1", 88)

    reconciliation = RealizedPnl(
        status="OK",
        gross_pnl=Decimal("40"),
        costs=Decimal("-3"),
        net_pnl=Decimal("37"),
        deals_count=1,
    )
    assert journal.record_execution_realization("execution-1", reconciliation)

    events = journal.list_execution_quality_events("execution-1", limit=10)
    assert [event["event_type"] for event in events] == [
        ExecutionQualityEventType.POSITION_REALIZED,
        ExecutionQualityEventType.EXECUTION_RESULT,
    ]
    realized = events[0]
    assert realized["position_id"] == 88
    assert realized["gross_pnl"] == "40"
    assert realized["net_pnl"] == "37"


def test_preflight_failure_persists_without_broker_ids(tmp_path):
    journal = SQLiteJournal(str(tmp_path / "journal.sqlite"))
    record = _record("execution-rejected")
    record = ExecutionRecord(
        **{
            **record.__dict__,
            "mt5_request": None,
            "preflight_result": "10016: invalid stops",
            "order_send_retcode": None,
            "mt5_order_id": None,
            "mt5_deal_id": None,
            "broker_comment": None,
            "status": ExecutionStatus.PREFLIGHT_REJECTED,
            "error_reason": "broker rejected preflight",
        }
    )

    journal.record_execution(record)

    stored = journal.get_execution_record("execution-rejected")
    assert stored["execution_status"] == "PREFLIGHT_REJECTED"
    assert stored["mt5_request"] is None
    assert stored["mt5_deal_id"] is None


def test_closed_execution_is_linked_and_settled_once(tmp_path):
    journal = SQLiteJournal(str(tmp_path / "journal.sqlite"))
    journal.record_execution(_record())

    assert journal.link_execution_position("execution-1", 88)
    pending = journal.list_unsettled_executions()
    assert pending[0]["mt5_position_id"] == 88

    reconciliation = RealizedPnl(
        status="OK",
        gross_pnl=Decimal("40"),
        costs=Decimal("-3"),
        net_pnl=Decimal("37"),
        deals_count=1,
    )
    assert journal.record_execution_realization("execution-1", reconciliation)
    assert not journal.record_execution_realization("execution-1", reconciliation)

    stored = journal.get_execution_record("execution-1")
    assert stored["realized_status"] == "OK"
    assert stored["realized_gross_pnl"] == "40"
    assert stored["realized_costs"] == "-3"
    assert stored["realized_net_pnl"] == "37"
    assert stored["realized_deals_count"] == 1
    assert journal.list_unsettled_executions() == []
