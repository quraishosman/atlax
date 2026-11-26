"""Deterministic Prop-Firm drawdown recovery state machine.

This module is pure risk policy. It receives broker/account observations and
returns a new immutable state plus a risk multiplier. It never reads MT5,
places orders, or attempts to recover a loss by increasing risk.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from decimal import Decimal
from typing import Optional

from core.models.prop_firm import (
    PropFirmRecoveryState,
    RecoveryMode,
    ValidationStatus,
)
from core.settings.prop_firm_config import PropFirmRecoveryConfig

logger = logging.getLogger("atlax.risk.recovery")


@dataclass(frozen=True)
class RecoveryDecision:
    """Auditable result of one recovery-state evaluation."""

    status: ValidationStatus
    state: PropFirmRecoveryState
    risk_multiplier: Decimal
    blocked: bool
    reason: str

    def __post_init__(self) -> None:
        if not isinstance(self.status, ValidationStatus):
            raise ValueError("RecoveryDecision.status must be ValidationStatus")
        if not isinstance(self.risk_multiplier, Decimal) or not self.risk_multiplier.is_finite():
            raise ValueError("risk_multiplier must be a finite Decimal")
        if self.risk_multiplier < 0 or self.risk_multiplier > 1:
            raise ValueError("risk_multiplier must be in the range [0, 1]")
        if not self.reason:
            raise ValueError("RecoveryDecision.reason is required")

    def to_dict(self) -> dict:
        return {
            "status": self.status.value,
            "state": self.state.to_dict(),
            "risk_multiplier": str(self.risk_multiplier),
            "blocked": self.blocked,
            "reason": self.reason,
        }


def _decimal(value: Decimal, field_name: str, *, positive: bool = False) -> None:
    if not isinstance(value, Decimal) or not value.is_finite():
        raise ValueError(f"{field_name} must be a finite Decimal")
    if positive and value <= 0:
        raise ValueError(f"{field_name} must be positive")


def _next_loss_streak(
    current: int,
    latest_closed_trade_pnl: Optional[Decimal],
) -> int:
    if latest_closed_trade_pnl is None:
        return current
    _decimal(latest_closed_trade_pnl, "latest_closed_trade_pnl")
    if latest_closed_trade_pnl < 0:
        return current + 1
    if latest_closed_trade_pnl > 0:
        return 0
    return current


def _recovery_decision(
    status: ValidationStatus,
    state: PropFirmRecoveryState,
    reason: str,
    *,
    risk_multiplier: Decimal,
    blocked: bool,
) -> RecoveryDecision:
    decision = RecoveryDecision(
        status=status,
        state=state,
        risk_multiplier=risk_multiplier,
        blocked=blocked,
        reason=reason,
    )
    if blocked:
        logger.warning(
            '{"event":"prop_recovery_blocked","mode":"%s",'
            '"status":"%s","reason":"%s"}',
            state.mode.value,
            status.value,
            reason[:200],
        )
    return decision


def evaluate_recovery_state(
    config: PropFirmRecoveryConfig,
    state: PropFirmRecoveryState,
    *,
    current_equity: Decimal,
    high_watermark_equity: Optional[Decimal],
    maximum_loss_amount: Decimal,
    latest_closed_trade_pnl: Optional[Decimal] = None,
    manual_resume_requested: bool = False,
    hard_limit_breached: bool = False,
) -> RecoveryDecision:
    """Advance recovery state using the approved maximum-loss budget.

    Drawdown is measured against ``maximum_loss_amount`` rather than nominal
    account balance. Missing equity observations fail closed when recovery is
    enabled. A manual resume can leave PAUSED, but a RULE_VIOLATION remains a
    manual-review state and cannot be cleared by an ordinary resume request.
    """

    if not isinstance(config, PropFirmRecoveryConfig):
        raise ValueError("config must be PropFirmRecoveryConfig")
    if not isinstance(state, PropFirmRecoveryState):
        raise ValueError("state must be PropFirmRecoveryState")
    _decimal(current_equity, "current_equity")
    _decimal(maximum_loss_amount, "maximum_loss_amount", positive=True)
    if high_watermark_equity is not None:
        _decimal(high_watermark_equity, "high_watermark_equity")
    if not isinstance(manual_resume_requested, bool):
        raise ValueError("manual_resume_requested must be a boolean")
    if not isinstance(hard_limit_breached, bool):
        raise ValueError("hard_limit_breached must be a boolean")

    loss_streak = _next_loss_streak(state.loss_streak, latest_closed_trade_pnl)
    if not config.enabled:
        next_state = PropFirmRecoveryState(
            mode=RecoveryMode.NORMAL,
            loss_streak=loss_streak,
        )
        return _recovery_decision(
            ValidationStatus.VALID,
            next_state,
            "Prop-firm recovery mode is disabled",
            risk_multiplier=Decimal("1"),
            blocked=False,
        )

    if hard_limit_breached:
        next_state = PropFirmRecoveryState(
            mode=RecoveryMode.RULE_VIOLATION,
            loss_streak=loss_streak,
            recovery_reference_equity=state.recovery_reference_equity,
            stabilization_closed_trades=state.stabilization_closed_trades,
            manual_resume_required=True,
        )
        return _recovery_decision(
            ValidationStatus.VALID,
            next_state,
            "Prop-firm maximum-loss limit is breached; manual review is required",
            risk_multiplier=Decimal("0"),
            blocked=True,
        )

    if state.mode is RecoveryMode.RULE_VIOLATION:
        return _recovery_decision(
            ValidationStatus.VALID,
            state,
            "Prop-firm rule-violation state requires explicit manual review",
            risk_multiplier=Decimal("0"),
            blocked=True,
        )

    if (
        state.mode is RecoveryMode.PAUSED
        and config.require_manual_resume_after_pause
        and not manual_resume_requested
    ):
        return _recovery_decision(
            ValidationStatus.VALID,
            state,
            "Prop-firm trading is paused pending an authorized resume",
            risk_multiplier=Decimal("0"),
            blocked=True,
        )

    if high_watermark_equity is None:
        unknown_state = PropFirmRecoveryState(
            mode=state.mode,
            loss_streak=loss_streak,
            recovery_reference_equity=state.recovery_reference_equity,
            stabilization_closed_trades=state.stabilization_closed_trades,
            manual_resume_required=state.mode is RecoveryMode.PAUSED,
        )
        return _recovery_decision(
            ValidationStatus.UNKNOWN,
            unknown_state,
            "High-watermark equity is required for recovery evaluation",
            risk_multiplier=Decimal("0"),
            blocked=True,
        )

    drawdown = max(Decimal("0"), high_watermark_equity - current_equity)
    drawdown_fraction = drawdown / maximum_loss_amount
    recovery_triggered = (
        drawdown_fraction >= config.recovery_drawdown_fraction
        or loss_streak >= config.consecutive_loss_trigger
    )

    stabilization_count = state.stabilization_closed_trades
    if state.mode is RecoveryMode.RECOVERY:
        if latest_closed_trade_pnl is not None and latest_closed_trade_pnl > 0:
            stabilization_count += 1
        elif latest_closed_trade_pnl is not None and latest_closed_trade_pnl < 0:
            stabilization_count = 0

    restored = False
    if (
        state.mode is RecoveryMode.RECOVERY
        and config.restore_after_drawdown_recovered
        and state.recovery_reference_equity is not None
        and current_equity
        >= state.recovery_reference_equity + config.restore_profit_cushion
        and stabilization_count >= config.stabilization_closed_trades
        and not recovery_triggered
    ):
        restored = True

    if restored:
        next_state = PropFirmRecoveryState(
            mode=RecoveryMode.NORMAL,
            loss_streak=loss_streak,
            stabilization_closed_trades=0,
        )
        decision = _recovery_decision(
            ValidationStatus.VALID,
            next_state,
            "Recovery target and stabilization conditions are satisfied",
            risk_multiplier=Decimal("1"),
            blocked=False,
        )
    elif recovery_triggered:
        next_state = PropFirmRecoveryState(
            mode=RecoveryMode.RECOVERY,
            loss_streak=loss_streak,
            recovery_reference_equity=(
                state.recovery_reference_equity
                if state.mode is RecoveryMode.RECOVERY
                and state.recovery_reference_equity is not None
                else high_watermark_equity
            ),
            stabilization_closed_trades=stabilization_count,
        )
        decision = _recovery_decision(
            ValidationStatus.VALID,
            next_state,
            "Recovery mode active after drawdown or consecutive-loss trigger",
            risk_multiplier=max(
                config.minimum_risk_multiplier,
                config.recovery_risk_multiplier,
            ),
            blocked=False,
        )
    elif drawdown_fraction >= config.caution_drawdown_fraction:
        next_state = PropFirmRecoveryState(
            mode=RecoveryMode.CAUTION,
            loss_streak=loss_streak,
            recovery_reference_equity=state.recovery_reference_equity,
            stabilization_closed_trades=0,
        )
        decision = _recovery_decision(
            ValidationStatus.VALID,
            next_state,
            "Caution mode active at the configured drawdown threshold",
            risk_multiplier=max(
                config.minimum_risk_multiplier,
                config.caution_risk_multiplier,
            ),
            blocked=False,
        )
    else:
        next_state = PropFirmRecoveryState(
            mode=RecoveryMode.NORMAL,
            loss_streak=loss_streak,
            stabilization_closed_trades=0,
        )
        decision = _recovery_decision(
            ValidationStatus.VALID,
            next_state,
            "Normal prop-firm risk mode",
            risk_multiplier=Decimal("1"),
            blocked=False,
        )

    if decision.state.mode is not state.mode:
        logger.info(
            '{"event":"prop_recovery_state_changed","from":"%s",'
            '"to":"%s","loss_streak":%s,"risk_multiplier":"%s"}',
            state.mode.value,
            decision.state.mode.value,
            decision.state.loss_streak,
            decision.risk_multiplier,
        )
    return decision
