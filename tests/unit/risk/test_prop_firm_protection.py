from datetime import datetime, timedelta, timezone
from decimal import Decimal

from core.models.prop_firm import LossBudgetDecision, ValidationStatus
from core.risk.prop_firm_protection import (
    ProtectionAction,
    ProtectionRequest,
    PropFirmProtectionGuard,
    PropFirmProtectionSnapshot,
)


NOW = datetime(2026, 9, 19, 12, 0, tzinfo=timezone.utc)


def _snapshot(**overrides):
    values = dict(
        profile_id="example-funded-usd",
        profile_version="2026-09-19.1",
        observed_at=NOW,
        remaining_overall_buffer=Decimal("120"),
        remaining_daily_buffer=Decimal("60"),
        hard_limit_breached=False,
        recovery_mode="CAUTION",
        transaction_cost_reserve=Decimal("2"),
        slippage_reserve=Decimal("1"),
    )
    values.update(overrides)
    return PropFirmProtectionSnapshot(**values)


def _request(**overrides):
    values = dict(
        action=ProtectionAction.PROFIT_LOCK,
        direction="BUY",
        current_sl=Decimal("1.09000"),
        requested_sl=Decimal("1.10100"),
        current_price=Decimal("1.10200"),
        current_profit=Decimal("2"),
        ticket=17,
        symbol="EURUSD",
    )
    values.update(overrides)
    return ProtectionRequest(**values)


def test_fresh_protective_stop_is_authorized_with_profile_audit_identity():
    guard = PropFirmProtectionGuard(_snapshot())

    result = guard.authorize(_request(), as_of=NOW)

    assert result.status is ValidationStatus.VALID
    assert result.authorized is True
    assert result.profile_id == "example-funded-usd"
    assert result.profile_version == "2026-09-19.1"


def test_guard_can_reuse_the_audited_loss_budget_decision():
    decision = LossBudgetDecision(
        status=ValidationStatus.VALID,
        profile_id="example-funded-usd",
        profile_version="2026-09-19.1",
        overall_floor=Decimal("1840"),
        daily_floor=Decimal("1940"),
        remaining_overall_buffer=Decimal("120"),
        remaining_daily_buffer=Decimal("60"),
        open_risk=Decimal("10"),
        cost_reserve=Decimal("6"),
        max_open_risk_budget=Decimal("60"),
        available_budget=Decimal("44"),
        allowed_new_trade_risk=Decimal("22"),
        blocked=False,
        reason="Loss budget available",
        evaluated_at=NOW,
        recovery_mode="CAUTION",
        risk_multiplier=Decimal("0.75"),
        transaction_cost_reserve=Decimal("3"),
        slippage_reserve=Decimal("1"),
    )

    guard = PropFirmProtectionGuard.from_budget_decision(decision)

    assert guard.snapshot.profile_version == decision.profile_version
    assert guard.snapshot.recovery_mode == "CAUTION"
    assert guard.snapshot.transaction_cost_reserve == Decimal("3")
    assert guard.authorize(_request(), as_of=NOW).authorized is True


def test_stale_snapshot_blocks_protection_action():
    guard = PropFirmProtectionGuard(
        _snapshot(observed_at=NOW - timedelta(minutes=10)),
        max_age_seconds=300,
    )

    result = guard.authorize(_request(), as_of=NOW)

    assert result.status is ValidationStatus.UNKNOWN
    assert result.authorized is False
    assert "stale" in result.reason


def test_backward_stop_move_is_rejected():
    guard = PropFirmProtectionGuard(_snapshot())

    result = guard.authorize(
        _request(requested_sl=Decimal("1.08900")),
        as_of=NOW,
    )

    assert result.authorized is False
    assert "protective" in result.reason


def test_profitable_partial_close_is_authorized_even_after_hard_limit_breach():
    guard = PropFirmProtectionGuard(
        _snapshot(hard_limit_breached=True, remaining_overall_buffer=Decimal("0"))
    )

    result = guard.authorize(
        _request(
            action=ProtectionAction.PARTIAL_CLOSE,
            requested_sl=None,
            current_profit=Decimal("0.5"),
        ),
        as_of=NOW,
    )

    assert result.authorized is True
    assert "limit breach" in result.reason


def test_partial_close_requires_broker_confirmed_profit():
    guard = PropFirmProtectionGuard(_snapshot())

    result = guard.authorize(
        _request(
            action=ProtectionAction.PARTIAL_CLOSE,
            requested_sl=None,
            current_profit=Decimal("0"),
        ),
        as_of=NOW,
    )

    assert result.authorized is False
    assert "positive profit" in result.reason
