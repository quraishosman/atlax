"""Deterministic Prop-Firm loss-budget evaluation.

Phase 2 owns budget arithmetic and Risk Engine integration. It does not read
MT5 directly, place orders, or decide entries. Callers provide a validated
profile and broker account snapshot.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional

from core.models.prop_firm import (
    AccountReferenceBasis,
    LossBudgetDecision,
    LossLimitSpec,
    PropFirmAccountSnapshot,
    PropFirmRecoveryState,
    PropFirmRuleProfile,
    ValidationStatus,
)
from core.risk.prop_firm_validation import (
    validate_profile,
    validate_snapshot_for_profile,
)
from core.risk.recovery import evaluate_recovery_state
from core.settings.prop_firm_config import PropFirmRecoveryConfig

logger = logging.getLogger("atlax.risk.loss_budget")


@dataclass(frozen=True)
class PropFirmRiskContext:
    """Validated inputs and configured caps for one risk evaluation."""

    profile: PropFirmRuleProfile
    snapshot: PropFirmAccountSnapshot
    open_risk: Decimal
    max_open_risk_fraction: Decimal
    new_trade_buffer_fraction: Decimal
    cost_reserve: Decimal
    max_profile_age_hours: float
    recovery_config: Optional[PropFirmRecoveryConfig] = None
    recovery_state: Optional[PropFirmRecoveryState] = None
    latest_closed_trade_pnl: Optional[Decimal] = None
    manual_resume_requested: bool = False
    transaction_cost_reserve: Decimal = Decimal("0")
    slippage_reserve: Decimal = Decimal("0")
    recovery_state_key: Optional[str] = None

    def __post_init__(self) -> None:
        if not isinstance(self.open_risk, Decimal) or not self.open_risk.is_finite():
            raise ValueError("open_risk must be a finite Decimal")
        if self.open_risk < 0:
            raise ValueError("open_risk must not be negative")
        if not isinstance(self.cost_reserve, Decimal) or not self.cost_reserve.is_finite():
            raise ValueError("cost_reserve must be a finite Decimal")
        if self.cost_reserve < 0:
            raise ValueError("cost_reserve must not be negative")
        for field_name in ("max_open_risk_fraction", "new_trade_buffer_fraction"):
            value = getattr(self, field_name)
            if not isinstance(value, Decimal) or not value.is_finite():
                raise ValueError(f"{field_name} must be a finite Decimal")
            if not (0 < value <= 1):
                raise ValueError(f"{field_name} must be in the range (0, 1]")
        if self.max_profile_age_hours <= 0:
            raise ValueError("max_profile_age_hours must be positive")
        if self.recovery_config is not None and not isinstance(
            self.recovery_config, PropFirmRecoveryConfig
        ):
            raise ValueError("recovery_config must be PropFirmRecoveryConfig")
        if self.recovery_state is not None and not isinstance(
            self.recovery_state, PropFirmRecoveryState
        ):
            raise ValueError("recovery_state must be PropFirmRecoveryState")
        if self.latest_closed_trade_pnl is not None and (
            not isinstance(self.latest_closed_trade_pnl, Decimal)
            or not self.latest_closed_trade_pnl.is_finite()
        ):
            raise ValueError("latest_closed_trade_pnl must be a finite Decimal")
        if not isinstance(self.manual_resume_requested, bool):
            raise ValueError("manual_resume_requested must be a boolean")
        for field_name in ("transaction_cost_reserve", "slippage_reserve"):
            value = getattr(self, field_name)
            if not isinstance(value, Decimal) or not value.is_finite():
                raise ValueError(f"{field_name} must be a finite Decimal")
            if value < 0:
                raise ValueError(f"{field_name} must not be negative")
        if self.recovery_state_key is not None and not str(
            self.recovery_state_key
        ).strip():
            raise ValueError("recovery_state_key must be non-empty when supplied")


def _reference_value(
    profile: PropFirmRuleProfile,
    snapshot: PropFirmAccountSnapshot,
    basis: AccountReferenceBasis,
) -> Optional[Decimal]:
    if basis is AccountReferenceBasis.INITIAL_BALANCE:
        return (
            snapshot.initial_balance
            if snapshot.initial_balance is not None
            else profile.nominal_balance
        )
    if basis is AccountReferenceBasis.OPENING_BALANCE:
        return snapshot.opening_balance
    if basis is AccountReferenceBasis.OPENING_EQUITY:
        return snapshot.opening_equity
    if basis is AccountReferenceBasis.HIGH_WATERMARK_BALANCE:
        return snapshot.high_watermark_balance
    if basis is AccountReferenceBasis.HIGH_WATERMARK_EQUITY:
        return snapshot.high_watermark_equity
    return None


def _limit_amount(
    profile: PropFirmRuleProfile,
    snapshot: PropFirmAccountSnapshot,
    limit: LossLimitSpec,
) -> Optional[Decimal]:
    if limit.amount is not None:
        return limit.amount
    reference = _reference_value(profile, snapshot, limit.basis)
    if reference is None:
        return None
    return reference * limit.percentage / Decimal("100")


def _floor(
    profile: PropFirmRuleProfile,
    snapshot: PropFirmAccountSnapshot,
    limit: LossLimitSpec,
) -> Optional[Decimal]:
    reference = _reference_value(profile, snapshot, limit.basis)
    amount = _limit_amount(profile, snapshot, limit)
    if reference is None or amount is None:
        return None
    return reference - amount


def _blocked_decision(
    context: PropFirmRiskContext,
    status: ValidationStatus,
    reason: str,
    evaluated_at: datetime,
    *,
    overall_floor: Optional[Decimal] = None,
    daily_floor: Optional[Decimal] = None,
    remaining_overall_buffer: Optional[Decimal] = None,
    remaining_daily_buffer: Optional[Decimal] = None,
    recovery_mode: Optional[str] = None,
    risk_multiplier: Optional[Decimal] = None,
    recovery_state: Optional[PropFirmRecoveryState] = None,
) -> LossBudgetDecision:
    total_cost_reserve = (
        context.cost_reserve
        + context.transaction_cost_reserve
        + context.slippage_reserve
    )
    return LossBudgetDecision(
        status=status,
        profile_id=context.profile.profile_id,
        profile_version=context.profile.version,
        overall_floor=overall_floor,
        daily_floor=daily_floor,
        remaining_overall_buffer=remaining_overall_buffer,
        remaining_daily_buffer=remaining_daily_buffer,
        open_risk=context.open_risk,
        cost_reserve=total_cost_reserve,
        max_open_risk_budget=None,
        available_budget=None,
        allowed_new_trade_risk=None,
        blocked=True,
        reason=reason,
        evaluated_at=evaluated_at,
        recovery_mode=recovery_mode,
        risk_multiplier=risk_multiplier,
        transaction_cost_reserve=context.transaction_cost_reserve,
        slippage_reserve=context.slippage_reserve,
        recovery_state=recovery_state or context.recovery_state,
    )


def evaluate_loss_budget(
    context: PropFirmRiskContext,
    *,
    as_of: datetime | None = None,
) -> LossBudgetDecision:
    """Calculate remaining prop-firm headroom and new-trade risk budget."""

    now = as_of or datetime.now(timezone.utc)
    profile_result = validate_profile(
        context.profile,
        as_of=now,
        max_age_hours=context.max_profile_age_hours,
    )
    if not profile_result.usable:
        reason = "; ".join(
            profile_result.errors or profile_result.unknown_reasons
        )
        decision = _blocked_decision(
            context,
            profile_result.status,
            f"Prop-firm rule profile is not usable: {reason}",
            now,
        )
        logger.warning(
            '{"event":"loss_budget_blocked","profile_id":"%s",'
            '"status":"%s","reason":"%s"}',
            context.profile.profile_id,
            decision.status.value,
            decision.reason[:200],
        )
        return decision

    snapshot_result = validate_snapshot_for_profile(
        context.profile,
        context.snapshot,
    )
    if not snapshot_result.usable:
        reason = "; ".join(
            snapshot_result.errors or snapshot_result.unknown_reasons
        )
        decision = _blocked_decision(
            context,
            snapshot_result.status,
            f"Prop-firm account snapshot is not usable: {reason}",
            now,
        )
        logger.warning(
            '{"event":"loss_budget_blocked","profile_id":"%s",'
            '"status":"%s","reason":"%s"}',
            context.profile.profile_id,
            decision.status.value,
            decision.reason[:200],
        )
        return decision

    overall_floor = _floor(
        context.profile,
        context.snapshot,
        context.profile.overall_loss,
    )
    daily_floor = (
        _floor(context.profile, context.snapshot, context.profile.daily_loss)
        if context.profile.daily_loss is not None
        else None
    )
    if overall_floor is None or (
        context.profile.daily_loss is not None and daily_floor is None
    ):
        return _blocked_decision(
            context,
            ValidationStatus.UNKNOWN,
            "Required loss-limit reference could not be resolved",
            now,
        )

    equity = context.snapshot.equity
    remaining_overall = max(Decimal("0"), equity - overall_floor)
    remaining_daily = (
        max(Decimal("0"), equity - daily_floor)
        if daily_floor is not None
        else None
    )
    governing_buffer = (
        min(remaining_overall, remaining_daily)
        if remaining_daily is not None
        else remaining_overall
    )
    max_open_risk_budget = (
        governing_buffer * context.max_open_risk_fraction
    )
    total_cost_reserve = (
        context.cost_reserve
        + context.transaction_cost_reserve
        + context.slippage_reserve
    )
    available_budget = (
        max_open_risk_budget - context.open_risk - total_cost_reserve
    )
    allowed_new_trade_risk = max(
        Decimal("0"),
        available_budget * context.new_trade_buffer_fraction,
    )
    recovery_mode = None
    risk_multiplier = None
    recovery_reason = None
    next_recovery_state = context.recovery_state
    if context.recovery_config is not None and context.recovery_config.enabled:
        if context.recovery_state is None:
            return _blocked_decision(
                context,
                ValidationStatus.UNKNOWN,
                "Recovery state is required when Prop-Firm recovery mode is enabled",
                now,
                overall_floor=overall_floor,
                daily_floor=daily_floor,
                remaining_overall_buffer=remaining_overall,
                remaining_daily_buffer=remaining_daily,
            )
        maximum_loss_amount = _limit_amount(
            context.profile,
            context.snapshot,
            context.profile.overall_loss,
        )
        if maximum_loss_amount is None:
            return _blocked_decision(
                context,
                ValidationStatus.UNKNOWN,
                "Maximum-loss amount is required for recovery evaluation",
                now,
                overall_floor=overall_floor,
                daily_floor=daily_floor,
                remaining_overall_buffer=remaining_overall,
                remaining_daily_buffer=remaining_daily,
            )
        recovery = evaluate_recovery_state(
            context.recovery_config,
            context.recovery_state,
            current_equity=equity,
            high_watermark_equity=context.snapshot.high_watermark_equity,
            maximum_loss_amount=maximum_loss_amount,
            latest_closed_trade_pnl=context.latest_closed_trade_pnl,
            manual_resume_requested=context.manual_resume_requested,
            hard_limit_breached=remaining_overall <= 0,
        )
        recovery_mode = recovery.state.mode.value
        risk_multiplier = recovery.risk_multiplier
        recovery_reason = recovery.reason
        next_recovery_state = recovery.state
        if recovery.status is ValidationStatus.UNKNOWN or recovery.blocked:
            allowed_new_trade_risk = Decimal("0")
        else:
            allowed_new_trade_risk = max(
                Decimal("0"),
                allowed_new_trade_risk * recovery.risk_multiplier,
            )

    blocked = available_budget <= 0 or allowed_new_trade_risk <= 0
    reason = (
        recovery_reason
        if recovery_reason and (blocked or recovery_mode != "NORMAL")
        else (
            "Loss budget available"
            if not blocked
            else "Remaining prop-firm loss budget is exhausted after open risk and reserves"
        )
    )
    decision = LossBudgetDecision(
        status=ValidationStatus.VALID,
        profile_id=context.profile.profile_id,
        profile_version=context.profile.version,
        overall_floor=overall_floor,
        daily_floor=daily_floor,
        remaining_overall_buffer=remaining_overall,
        remaining_daily_buffer=remaining_daily,
        open_risk=context.open_risk,
        cost_reserve=total_cost_reserve,
        max_open_risk_budget=max_open_risk_budget,
        available_budget=available_budget,
        allowed_new_trade_risk=allowed_new_trade_risk,
        blocked=blocked,
        reason=reason,
        evaluated_at=now,
        recovery_mode=recovery_mode,
        risk_multiplier=risk_multiplier,
        transaction_cost_reserve=context.transaction_cost_reserve,
        slippage_reserve=context.slippage_reserve,
        recovery_state=next_recovery_state,
    )
    logger.info(
        '{"event":"loss_budget_evaluated","profile_id":"%s",'
        '"profile_version":"%s","equity":"%s",'
        '"remaining_overall_buffer":"%s","remaining_daily_buffer":"%s",'
        '"open_risk":"%s","allowed_new_trade_risk":"%s",'
        '"blocked":%s}',
        decision.profile_id,
        decision.profile_version,
        equity,
        decision.remaining_overall_buffer,
        decision.remaining_daily_buffer,
        decision.open_risk,
        decision.allowed_new_trade_risk,
        str(decision.blocked).lower(),
    )
    return decision
