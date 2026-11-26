from decimal import Decimal

import pytest

from core.models.prop_firm import PropFirmRecoveryState, RecoveryMode, ValidationStatus
from core.risk.recovery import evaluate_recovery_state
from core.settings.prop_firm_config import PropFirmRecoveryConfig


def _config(**overrides):
    values = dict(
        enabled=True,
        caution_drawdown_fraction=Decimal("0.25"),
        recovery_drawdown_fraction=Decimal("0.50"),
        caution_risk_multiplier=Decimal("0.75"),
        recovery_risk_multiplier=Decimal("0.40"),
        minimum_risk_multiplier=Decimal("0.20"),
        consecutive_loss_trigger=3,
        restore_after_drawdown_recovered=True,
        restore_profit_cushion=Decimal("10"),
        stabilization_closed_trades=2,
        require_manual_resume_after_pause=True,
    )
    values.update(overrides)
    return PropFirmRecoveryConfig(**values)


def _evaluate(state, **overrides):
    values = dict(
        current_equity=Decimal("2000"),
        high_watermark_equity=Decimal("2000"),
        maximum_loss_amount=Decimal("160"),
    )
    values.update(overrides)
    return evaluate_recovery_state(_config(), state, **values)


def test_normal_mode_uses_full_risk_without_drawdown():
    decision = _evaluate(PropFirmRecoveryState())

    assert decision.status is ValidationStatus.VALID
    assert decision.state.mode is RecoveryMode.NORMAL
    assert decision.risk_multiplier == Decimal("1")
    assert decision.blocked is False


def test_caution_reduces_risk_from_maximum_loss_budget_drawdown():
    decision = _evaluate(
        PropFirmRecoveryState(),
        current_equity=Decimal("1950"),
    )

    assert decision.state.mode is RecoveryMode.CAUTION
    assert decision.risk_multiplier == Decimal("0.75")
    assert decision.blocked is False


def test_recovery_is_triggered_by_consecutive_losses_and_never_increases_risk():
    decision = _evaluate(
        PropFirmRecoveryState(loss_streak=2),
        latest_closed_trade_pnl=Decimal("-1"),
    )

    assert decision.state.mode is RecoveryMode.RECOVERY
    assert decision.state.loss_streak == 3
    assert decision.risk_multiplier == Decimal("0.40")
    assert decision.risk_multiplier < Decimal("1")


def test_loss_sequence_replay_never_increases_the_next_risk_multiplier():
    state = PropFirmRecoveryState()
    multipliers = []
    for pnl in (Decimal("-1"), Decimal("-1"), Decimal("-1"), Decimal("-1")):
        decision = _evaluate(state, latest_closed_trade_pnl=pnl)
        multipliers.append(decision.risk_multiplier)
        state = decision.state

    assert multipliers == [
        Decimal("1"),
        Decimal("1"),
        Decimal("0.40"),
        Decimal("0.40"),
    ]
    assert all(
        next_multiplier <= current_multiplier
        for current_multiplier, next_multiplier in zip(
            multipliers,
            multipliers[1:],
        )
    )


def test_recovery_restores_only_after_cushion_and_stabilization():
    state = PropFirmRecoveryState(
        mode=RecoveryMode.RECOVERY,
        loss_streak=0,
        recovery_reference_equity=Decimal("2000"),
        stabilization_closed_trades=1,
    )

    decision = _evaluate(
        state,
        current_equity=Decimal("2010"),
        latest_closed_trade_pnl=Decimal("1"),
    )

    assert decision.state.mode is RecoveryMode.NORMAL
    assert decision.state.stabilization_closed_trades == 0
    assert decision.risk_multiplier == Decimal("1")


def test_paused_state_requires_manual_resume():
    state = PropFirmRecoveryState(
        mode=RecoveryMode.PAUSED,
        manual_resume_required=True,
    )

    blocked = _evaluate(state)
    resumed = _evaluate(state, manual_resume_requested=True)

    assert blocked.blocked is True
    assert blocked.risk_multiplier == Decimal("0")
    assert resumed.blocked is False
    assert resumed.state.mode is RecoveryMode.NORMAL


def test_paused_state_can_use_configured_automatic_resume_policy():
    state = PropFirmRecoveryState(mode=RecoveryMode.PAUSED)

    decision = evaluate_recovery_state(
        _config(require_manual_resume_after_pause=False),
        state,
        current_equity=Decimal("2000"),
        high_watermark_equity=Decimal("2000"),
        maximum_loss_amount=Decimal("160"),
    )

    assert decision.blocked is False
    assert decision.state.mode is RecoveryMode.NORMAL


def test_rule_violation_cannot_be_cleared_by_normal_resume():
    state = PropFirmRecoveryState(mode=RecoveryMode.RULE_VIOLATION)

    decision = _evaluate(state, manual_resume_requested=True)

    assert decision.blocked is True
    assert decision.state.mode is RecoveryMode.RULE_VIOLATION
    assert decision.risk_multiplier == Decimal("0")


def test_hard_limit_breach_enters_rule_violation():
    decision = _evaluate(
        PropFirmRecoveryState(),
        current_equity=Decimal("1840"),
        hard_limit_breached=True,
    )

    assert decision.state.mode is RecoveryMode.RULE_VIOLATION
    assert decision.blocked is True
    assert decision.risk_multiplier == Decimal("0")


def test_missing_high_watermark_is_unknown_and_blocked():
    decision = _evaluate(
        PropFirmRecoveryState(),
        high_watermark_equity=None,
    )

    assert decision.status is ValidationStatus.UNKNOWN
    assert decision.blocked is True
    assert decision.risk_multiplier == Decimal("0")


def test_recovery_config_rejects_multiplier_that_could_increase_risk():
    with pytest.raises(ValueError, match="risk multiplier|range"):
        _config(
            recovery_risk_multiplier=Decimal("1.1"),
        )


def test_recovery_state_round_trips():
    state = PropFirmRecoveryState(
        mode=RecoveryMode.RECOVERY,
        loss_streak=3,
        recovery_reference_equity=Decimal("2000"),
        stabilization_closed_trades=1,
        manual_resume_required=False,
    )

    assert PropFirmRecoveryState.from_dict(state.to_dict()) == state
