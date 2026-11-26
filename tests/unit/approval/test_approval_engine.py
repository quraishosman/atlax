import pytest
import sqlite3
import json
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock

from core.models.execution import ExecutionRequest
from core.models.approval import ApprovalStatus
from core.settings.approval_config import ApprovalConfig
from core.approval.approval_engine import ApprovalEngine
from core.models.trade_candidate import TradeCandidate, EntryModel, InvalidationModel, TargetModel, Direction
from core.models.risk import RiskEvaluation
from decimal import Decimal


@pytest.fixture
def approval_config():
    return ApprovalConfig(require_manual=True, auto_reject_after_seconds=300)


@pytest.fixture
def approval_engine(tmp_path, approval_config):
    return ApprovalEngine(approval_config, db_path=str(tmp_path / "approval.sqlite"))


def _make_candidate(candidate_id="cand-1"):
    return TradeCandidate(
        candidate_id=candidate_id,
        strategy_name="CRTStrategy",
        source_detector_event_ids=("det-1",),
        symbol="EURUSD",
        timeframe="H4",
        profile="swing_trader",
        direction=Direction.BUY,
        entry_model=EntryModel(Decimal("1.1"), Decimal("1.0"), Decimal("1.05"), "desc"),
        invalidation_model=InvalidationModel(Decimal("0.9"), "sl"),
        target_model=TargetModel(Decimal("1.2"), Decimal("1.3"), "tp"),
        confidence_inputs={},
        explanation="test",
        created_at=datetime.now(timezone.utc)
    )

def _make_request(candidate_id="cand-1"):
    cand = _make_candidate(candidate_id)
    risk = RiskEvaluation(candidate_id, True, Decimal("0.1"), None, None, "test", datetime.now(timezone.utc))
    return ExecutionRequest(cand, risk, 1234, datetime.now(timezone.utc))


class TestApprovalEngine:
    def test_submit_for_approval(self, approval_engine):
        engine = approval_engine
        req = _make_request()
        
        pending = engine.submit_for_approval(req)
        
        assert pending.status == ApprovalStatus.PENDING
        assert pending.request == req
        assert len(engine.get_all_pending()) == 1
        
    def test_approve_trade(self, approval_engine):
        engine = approval_engine
        req = _make_request("cand-1")
        engine.submit_for_approval(req)
        
        approved_req = engine.approve_trade("cand-1")
        
        assert approved_req == req
        assert len(engine.get_all_pending()) == 0
        
    def test_reject_trade(self, approval_engine):
        engine = approval_engine
        req = _make_request("cand-2")
        engine.submit_for_approval(req)
        
        assert engine.reject_trade("cand-2") is True
        assert len(engine.get_all_pending()) == 0
        
    def test_cleanup_expired(self, approval_engine):
        engine = approval_engine
        req = _make_request("cand-expire")
        pending = engine.submit_for_approval(req)
        
        # Backdate the persisted row to simulate time passing.
        with sqlite3.connect(engine._queue.db_path) as conn:
            conn.execute(
                "UPDATE pending_approvals SET expires_at = ? WHERE candidate_id = ?",
                ((datetime.now(timezone.utc) - timedelta(seconds=10)).isoformat(), "cand-expire"),
            )
        
        expired_count = engine.cleanup_expired()
        
        assert expired_count == 1
        assert len(engine.get_all_pending()) == 0

    def test_cleanup_expired_approved_request_cannot_be_claimed(self, approval_engine):
        engine = approval_engine
        engine.submit_for_approval(_make_request("cand-approved-expire"))
        assert engine.approve_trade("cand-approved-expire") is not None

        with sqlite3.connect(engine._queue.db_path) as conn:
            conn.execute(
                "UPDATE pending_approvals SET expires_at = ? WHERE candidate_id = ?",
                ((datetime.now(timezone.utc) - timedelta(seconds=10)).isoformat(), "cand-approved-expire"),
            )

        assert engine.cleanup_expired() == 1
        assert engine.get_status("cand-approved-expire") == ApprovalStatus.EXPIRED
        assert engine.get_all_approved_ready() == []

    def test_execution_ready_gate_and_atomic_claim(self, approval_engine):
        engine = approval_engine
        engine.submit_for_approval(_make_request("cand-fvg"), execution_ready=False)

        assert engine.approve_trade("cand-fvg") is not None
        assert engine.get_all_approved_ready() == []
        assert engine.mark_execution_ready("cand-fvg") is True

        ready = engine.get_all_approved_ready()
        assert [item.request.candidate.candidate_id for item in ready] == ["cand-fvg"]

        claimed = engine.claim_approved("cand-fvg")
        assert claimed is not None
        assert claimed.status == ApprovalStatus.EXECUTING
        assert engine.claim_approved("cand-fvg") is None
        assert engine.mark_execution_status("cand-fvg", ApprovalStatus.EXECUTED) is True
        assert engine.get_status("cand-fvg") == ApprovalStatus.EXECUTED
        assert engine.mark_execution_status("cand-fvg", ApprovalStatus.EXECUTED) is False

    def test_malformed_approved_row_is_quarantined_without_blocking_valid_rows(
        self, approval_engine
    ):
        engine = approval_engine
        engine.submit_for_approval(_make_request("cand-valid"))
        engine.approve_trade("cand-valid")
        engine.submit_for_approval(_make_request("cand-malformed"))
        engine.approve_trade("cand-malformed")

        with sqlite3.connect(engine._queue.db_path) as conn:
            request_json = conn.execute(
                "SELECT request_json FROM pending_approvals WHERE candidate_id = ?",
                ("cand-malformed",),
            ).fetchone()[0]
            payload = json.loads(request_json)
            del payload["candidate"]["entry_model"]["entry_zone_high"]
            conn.execute(
                "UPDATE pending_approvals SET request_json = ? WHERE candidate_id = ?",
                (json.dumps(payload), "cand-malformed"),
            )

        ready = engine.get_all_approved_ready()

        assert [item.request.candidate.candidate_id for item in ready] == ["cand-valid"]
        assert engine.get_status("cand-malformed") == ApprovalStatus.QUARANTINED
        with sqlite3.connect(engine._queue.db_path) as conn:
            reason = conn.execute(
                "SELECT quarantine_reason FROM pending_approvals WHERE candidate_id = ?",
                ("cand-malformed",),
            ).fetchone()[0]
        assert "entry_zone_high" in reason

    def test_malformed_pending_row_is_skipped_and_quarantined(self, approval_engine):
        engine = approval_engine
        engine.submit_for_approval(_make_request("cand-pending-malformed"))
        with sqlite3.connect(engine._queue.db_path) as conn:
            conn.execute(
                "UPDATE pending_approvals SET request_json = ? WHERE candidate_id = ?",
                ("{not-json", "cand-pending-malformed"),
            )

        assert engine.get_all_pending() == []
        assert engine.get_status("cand-pending-malformed") == ApprovalStatus.QUARANTINED
