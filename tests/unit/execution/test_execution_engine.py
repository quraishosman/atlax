"""
Execution Engine Unit Tests.

MT5 is mocked by injecting a fake module into sys.modules — no MT5 installation
required. This is the correct approach when the module under test uses
`import MetaTrader5` inside a function (lazy import).

Authority: docs/11_EXECUTION_ENGINE.md
           docs/16_SECURITY.md (credential/comment sanitization)
"""

import dataclasses
import sys
import types
from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from core.execution.execution_engine import ExecutionEngine
from core.execution.trade_manager import TradeManager
from core.models.execution import ExecutionRecord, ExecutionRequest, ExecutionStatus
from core.models.risk import RiskEvaluation
from core.models.trade_candidate import (
    Direction, EntryModel, InvalidationModel, TargetModel, TradeCandidate,
)
from core.settings.execution_config import ExecutionConfig, load_execution_config
from core.settings.risk_config import load_risk_config


# ---------------------------------------------------------------------------
# MT5 module injection helpers
# ---------------------------------------------------------------------------

def _mt5_check_ok() -> MagicMock:
    r = MagicMock()
    r.retcode = 0
    r.comment = "OK"
    return r


def _mt5_send_done() -> MagicMock:
    r = MagicMock()
    r.retcode = 10009   # TRADE_RETCODE_DONE
    r.order   = 123456
    r.deal    = 789012
    r.comment = "Request completed"
    return r


def _mt5_send_rejected() -> MagicMock:
    r = MagicMock()
    r.retcode = 10015   # TRADE_RETCODE_INVALID_FILL
    r.order   = 0
    r.deal    = 0
    r.comment = "Invalid fill"
    return r


@contextmanager
def _inject_mt5(check_result=None, send_result=None, tick=None):
    """
    Inject a fake MetaTrader5 module for the duration of a test block.
    Restores the original state (or removes the key) on exit.
    """
    fake = types.ModuleType("MetaTrader5")
    fake.order_check       = MagicMock(return_value=check_result)
    fake.order_send        = MagicMock(return_value=send_result)
    fake.symbol_info_tick  = MagicMock(return_value=tick)
    fake.last_error        = MagicMock(return_value=(0, "No error"))

    original = sys.modules.get("MetaTrader5")
    sys.modules["MetaTrader5"] = fake
    try:
        yield fake
    finally:
        if original is None:
            sys.modules.pop("MetaTrader5", None)
        else:
            sys.modules["MetaTrader5"] = original


# ---------------------------------------------------------------------------
# Domain helpers
# ---------------------------------------------------------------------------

def _make_candidate(
    candidate_id: str = "cand-test-0001",
    symbol: str = "EURUSD",
    timeframe: str = "H4",
    profile: str = "swing_trader",
    direction: str = Direction.BUY,
) -> TradeCandidate:
    now = datetime(2026, 7, 21, 10, 0, tzinfo=timezone.utc)
    return TradeCandidate(
        candidate_id=candidate_id,
        strategy_name="CRTStrategy",
        source_detector_event_ids=("det-001",),
        symbol=symbol, timeframe=timeframe, profile=profile, direction=direction,
        entry_model=EntryModel(
            entry_zone_high=Decimal("1.10000"),
            entry_zone_low=Decimal("1.09800"),
            reference_price=Decimal("1.09900"),
            description="test",
        ),
        invalidation_model=InvalidationModel(
            invalidation_level=Decimal("1.09000"), description="test SL"
        ),
        target_model=TargetModel(
            midpoint_target=Decimal("1.105"),
            opposite_extreme_target=Decimal("1.11"),
            description="test TP",
        ),
        confidence_inputs={},
        explanation="test",
        created_at=now,
    )


def _make_approved_risk(candidate_id: str = "cand-test-0001") -> RiskEvaluation:
    return RiskEvaluation(
        candidate_id=candidate_id,
        approved=True,
        lot_size=Decimal("0.15"),
        calculation=None,
        rejection_code=None,
        reason="All controls passed",
        evaluated_at=datetime(2026, 7, 21, 10, 0, tzinfo=timezone.utc),
    )


def _make_rejected_risk(candidate_id: str = "cand-test-0001") -> RiskEvaluation:
    return RiskEvaluation(
        candidate_id=candidate_id,
        approved=False,
        lot_size=None,
        calculation=None,
        rejection_code="DAILY_LOSS_HIT",
        reason="Daily loss limit reached",
        evaluated_at=datetime(2026, 7, 21, 10, 0, tzinfo=timezone.utc),
    )


def _make_request(
    candidate: TradeCandidate | None = None,
    risk: RiskEvaluation | None = None,
) -> ExecutionRequest:
    c = candidate or _make_candidate()
    r = risk or _make_approved_risk(c.candidate_id)
    return ExecutionRequest(
        candidate=c,
        risk_evaluation=r,
        magic_number=20260101,
        requested_at=datetime(2026, 7, 21, 10, 0, tzinfo=timezone.utc),
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def exec_config() -> ExecutionConfig:
    return load_execution_config("config/atlax.yaml")


@pytest.fixture
def risk_config():
    return load_risk_config("config/atlax.yaml")


@pytest.fixture
def engine(exec_config, risk_config) -> ExecutionEngine:
    return ExecutionEngine(exec_config, risk_config)


# ---------------------------------------------------------------------------
# Config Validation Tests
# ---------------------------------------------------------------------------

class TestExecutionConfigValidation:
    def test_loads_from_yaml(self, exec_config):
        assert exec_config.enabled is True
        assert exec_config.require_alert_eligibility is True
        assert exec_config.magic_number == 20260101
        assert exec_config.deviation_points == 20
        assert exec_config.order_filling_mode == "FOK"
        assert exec_config.comment_prefix == "ATLAX"

    def test_return_filling_mode_is_allowed(self):
        config = ExecutionConfig(
            enabled=True, magic_number=1234,
            require_alert_eligibility=True,
            deviation_points=20, order_filling_mode="RETURN",
            use_market_price=True, max_retries=2,
            retry_delay_seconds=1.0, comment_prefix="ATLAX",
            partial_profit_enabled=True, partial_profit_ratio=0.5,
            move_sl_to_breakeven=True,
        )

        assert config.order_filling_mode == "RETURN"

    def test_invalid_filling_mode_rejects(self):
        with pytest.raises(ValueError, match="order_filling_mode"):
            ExecutionConfig(
                enabled=True, magic_number=1234,
                require_alert_eligibility=True,
                deviation_points=20, order_filling_mode="GFD",
                use_market_price=True, max_retries=2,
                retry_delay_seconds=1.0, comment_prefix="ATLAX",
                partial_profit_enabled=True, partial_profit_ratio=0.5,
                move_sl_to_breakeven=True,
            )

    def test_zero_magic_rejects(self):
        with pytest.raises(ValueError, match="magic_number"):
            ExecutionConfig(
                enabled=True, magic_number=0,
                require_alert_eligibility=True,
                deviation_points=20, order_filling_mode="FOK",
                use_market_price=True, max_retries=2,
                retry_delay_seconds=1.0, comment_prefix="ATLAX",
                partial_profit_enabled=True, partial_profit_ratio=0.5,
                move_sl_to_breakeven=True,
            )

    def test_comment_prefix_too_long_rejects(self):
        with pytest.raises(ValueError, match="comment_prefix"):
            ExecutionConfig(
                enabled=True, magic_number=1234,
                require_alert_eligibility=True,
                deviation_points=20, order_filling_mode="FOK",
                use_market_price=True, max_retries=2,
                retry_delay_seconds=1.0, comment_prefix="A" * 21,
                partial_profit_enabled=True, partial_profit_ratio=0.5,
                move_sl_to_breakeven=True,
            )

    def test_missing_execution_section_raises(self, tmp_path):
        cfg = tmp_path / "no_exec.yaml"
        cfg.write_text("crt:\n  enabled: true\n")
        with pytest.raises(ValueError, match="execution"):
            load_execution_config(str(cfg))


# ---------------------------------------------------------------------------
# Model Invariant Tests
# ---------------------------------------------------------------------------

class TestExecutionRequestInvariants:
    def test_candidate_id_mismatch_raises(self):
        c = _make_candidate("cand-A")
        r = _make_approved_risk("cand-B")
        with pytest.raises(ValueError, match="mismatch"):
            ExecutionRequest(
                candidate=c, risk_evaluation=r,
                magic_number=20260101,
                requested_at=datetime.now(timezone.utc),
            )

    def test_zero_magic_raises(self):
        c = _make_candidate()
        r = _make_approved_risk(c.candidate_id)
        with pytest.raises(ValueError, match="magic_number"):
            ExecutionRequest(
                candidate=c, risk_evaluation=r,
                magic_number=0,
                requested_at=datetime.now(timezone.utc),
            )


# ---------------------------------------------------------------------------
# Gate: Engine disabled
# ---------------------------------------------------------------------------

class TestDisabledGate:
    def test_disabled_returns_request_invalid(self, exec_config, risk_config):
        disabled = dataclasses.replace(exec_config, enabled=False)
        eng = ExecutionEngine(disabled, risk_config)
        record = eng.execute(_make_request())
        assert record.status == ExecutionStatus.REQUEST_INVALID
        assert "disabled" in record.error_reason.lower()

    def test_disabled_record_has_all_fields(self, exec_config, risk_config):
        disabled = dataclasses.replace(exec_config, enabled=False)
        eng = ExecutionEngine(disabled, risk_config)
        record = eng.execute(_make_request())
        assert record.candidate_id is not None
        assert record.symbol == "EURUSD"
        assert record.direction == Direction.BUY


# ---------------------------------------------------------------------------
# Gate: Risk not approved
# ---------------------------------------------------------------------------

class TestRiskNotApprovedGate:
    def test_unapproved_risk_returns_risk_not_approved(self, engine):
        c = _make_candidate()
        request = _make_request(candidate=c, risk=_make_rejected_risk(c.candidate_id))
        record = engine.execute(request)
        assert record.status == ExecutionStatus.RISK_NOT_APPROVED
        assert "DAILY_LOSS_HIT" in record.error_reason

    def test_unapproved_produces_complete_record(self, engine):
        c = _make_candidate()
        request = _make_request(candidate=c, risk=_make_rejected_risk(c.candidate_id))
        record = engine.execute(request)
        assert record.record_id is not None
        assert record.candidate_id == c.candidate_id
        assert record.lot_size is None


# ---------------------------------------------------------------------------
# Gate: MT5 not installed (preflight → PREFLIGHT_REJECTED)
# ---------------------------------------------------------------------------

class TestMT5NotInstalled:
    def test_no_mt5_returns_preflight_rejected(self, engine):
        """When MT5 is absent, preflight must fail closed (not crash)."""
        # Ensure MT5 is NOT in sys.modules
        sys.modules.pop("MetaTrader5", None)
        record = engine.execute(_make_request())
        assert record.status == ExecutionStatus.PREFLIGHT_REJECTED
        assert any(
            marker in record.error_reason.lower()
            for marker in ("not installed", "no ipc connection")
        )


# ---------------------------------------------------------------------------
# Gate: Pre-flight check rejected by MT5
# ---------------------------------------------------------------------------

class TestPreflightRejected:
    def test_order_check_failure_returns_preflight_rejected(self, engine):
        bad_check = MagicMock()
        bad_check.retcode = 10016
        bad_check.comment = "Insufficient margin"

        with _inject_mt5(check_result=bad_check):
            record = engine.execute(_make_request())

        assert record.status == ExecutionStatus.PREFLIGHT_REJECTED
        assert "10016" in record.error_reason

    def test_invalid_fill_falls_back_only_after_preflight(self, engine):
        bad_fill = MagicMock()
        bad_fill.retcode = 10030
        bad_fill.comment = "Unsupported filling mode"
        good_check = _mt5_check_ok()

        with _inject_mt5(check_result=bad_fill, send_result=_mt5_send_done()) as fake:
            fake.order_check.side_effect = [bad_fill, good_check]
            record = engine.execute(_make_request())

        assert record.status == ExecutionStatus.FILLED
        assert fake.order_check.call_count == 2
        assert fake.order_send.call_args.args[0]["type_filling"] == 1


class TestAmbiguousSubmission:
    def test_none_order_send_is_ambiguous_and_not_retried(self, engine):
        with _inject_mt5(check_result=_mt5_check_ok(), send_result=None) as fake:
            record = engine.execute(_make_request())

        assert record.status == ExecutionStatus.AMBIGUOUS
        assert "reconcile broker state" in record.error_reason.lower()
        assert fake.order_send.call_count == 1


class TestTradeManagerBreakeven:
    def test_breakeven_preserves_tp2(self, tmp_path):
        mt5 = MagicMock()
        result = MagicMock()
        result.retcode = 10009
        mt5.order_send.return_value = result
        position = types.SimpleNamespace(ticket=17, symbol="EURUSD")
        manager = TradeManager(state_file=str(tmp_path / "watches.json"))

        manager._move_sl_to_breakeven(
            mt5,
            position,
            Decimal("1.10000"),
            Decimal("1.12000"),
            20260101,
        )

        request = mt5.order_send.call_args.args[0]
        assert request["sl"] == 1.1
        assert request["tp"] == 1.12


# ---------------------------------------------------------------------------
# Happy path: FILLED (retcode 10009)
# ---------------------------------------------------------------------------

class TestHappyPathFilled:
    def test_filled_status_on_retcode_10009(self, engine):
        with _inject_mt5(check_result=_mt5_check_ok(), send_result=_mt5_send_done()):
            record = engine.execute(_make_request())

        assert record.status == ExecutionStatus.FILLED
        assert record.order_send_retcode == 10009
        assert record.mt5_order_id == 123456
        assert record.mt5_deal_id == 789012

    def test_filled_lot_size_in_record(self, engine):
        with _inject_mt5(check_result=_mt5_check_ok(), send_result=_mt5_send_done()):
            record = engine.execute(_make_request())

        assert record.lot_size == Decimal("0.15")

    def test_filled_mt5_request_stored_in_record(self, engine):
        with _inject_mt5(check_result=_mt5_check_ok(), send_result=_mt5_send_done()):
            record = engine.execute(_make_request())

        assert record.mt5_request is not None
        assert record.mt5_request.symbol == "EURUSD"
        assert record.mt5_request.magic == 20260101

    def test_filled_mt5_request_carries_tp2(self, engine):
        with _inject_mt5(check_result=_mt5_check_ok(), send_result=_mt5_send_done()):
            record = engine.execute(_make_request())

        assert record.mt5_request is not None
        assert record.mt5_request.tp == "1.11"

    def test_filled_no_error_reason(self, engine):
        with _inject_mt5(check_result=_mt5_check_ok(), send_result=_mt5_send_done()):
            record = engine.execute(_make_request())

        assert record.error_reason is None


# ---------------------------------------------------------------------------
# Broker rejection (non-zero retcode ≠ DONE/PLACED/PARTIAL)
# ---------------------------------------------------------------------------

class TestBrokerRejection:
    def test_non_zero_retcode_returns_rejected_by_broker(self, engine):
        with _inject_mt5(check_result=_mt5_check_ok(), send_result=_mt5_send_rejected()):
            record = engine.execute(_make_request())

        assert record.status == ExecutionStatus.REJECTED_BY_BROKER
        assert record.order_send_retcode == 10015
        assert record.error_reason is not None


# ---------------------------------------------------------------------------
# Security: comment sanitization
# ---------------------------------------------------------------------------

class TestCommentSanitization:
    def test_comment_contains_no_credentials(self, engine):
        with _inject_mt5(check_result=_mt5_check_ok(), send_result=_mt5_send_done()):
            record = engine.execute(_make_request())

        comment = record.mt5_request.comment
        assert comment.startswith("ATLAX_")
        assert len(comment) <= 31
        for word in ["password", "token", "secret", "key", "credential"]:
            assert word not in comment.lower()

    def test_comment_contains_truncated_candidate_id(self, engine):
        c = _make_candidate(candidate_id="abcd1234-efgh-5678-ijkl-mnop")
        req = _make_request(candidate=c, risk=_make_approved_risk(c.candidate_id))

        with _inject_mt5(check_result=_mt5_check_ok(), send_result=_mt5_send_done()):
            record = engine.execute(req)

        assert "abcd1234" in record.mt5_request.comment


# ---------------------------------------------------------------------------
# Record completeness: every branch produces a full record
# ---------------------------------------------------------------------------

class TestRecordCompleteness:
    def _assert_complete(self, record: ExecutionRecord) -> None:
        assert record.record_id and len(record.record_id) > 0
        assert record.candidate_id is not None
        assert record.symbol is not None
        assert record.profile is not None
        assert record.direction is not None
        assert record.status is not None
        assert record.attempted_at is not None
        assert record.completed_at is not None

    def test_disabled_record_complete(self, exec_config, risk_config):
        eng = ExecutionEngine(dataclasses.replace(exec_config, enabled=False), risk_config)
        self._assert_complete(eng.execute(_make_request()))

    def test_risk_rejected_record_complete(self, engine):
        c = _make_candidate()
        req = _make_request(candidate=c, risk=_make_rejected_risk(c.candidate_id))
        self._assert_complete(engine.execute(req))

    def test_preflight_rejected_record_complete(self, engine):
        bad_check = MagicMock()
        bad_check.retcode = 10016
        bad_check.comment = "No margin"
        with _inject_mt5(check_result=bad_check):
            self._assert_complete(engine.execute(_make_request()))

    def test_filled_record_complete(self, engine):
        with _inject_mt5(check_result=_mt5_check_ok(), send_result=_mt5_send_done()):
            self._assert_complete(engine.execute(_make_request()))


# ---------------------------------------------------------------------------
# Direction: SELL builds ORDER_TYPE_SELL
# ---------------------------------------------------------------------------

class TestSellDirection:
    def test_sell_uses_order_type_sell(self, engine):
        c = _make_candidate(direction=Direction.SELL)
        req = _make_request(candidate=c, risk=_make_approved_risk(c.candidate_id))

        with _inject_mt5(check_result=_mt5_check_ok(), send_result=_mt5_send_done()):
            record = engine.execute(req)

        assert record.mt5_request.order_type == "ORDER_TYPE_SELL"
        assert record.direction == Direction.SELL
