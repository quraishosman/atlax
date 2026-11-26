"""Authorization boundary for protective actions on Prop-Firm positions.

The existing Trade Manager remains the only component that talks to MT5. This
guard supplies an optional, fail-closed check before a stop update or a
profitable partial close. It does not choose exits and it never permits a
non-protective stop move.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from enum import Enum

from core.models.prop_firm import LossBudgetDecision, ValidationStatus

logger = logging.getLogger("atlax.risk.prop_firm_protection")


class ProtectionAction(str, Enum):
    BREAKEVEN = "BREAKEVEN"
    PROFIT_LOCK = "PROFIT_LOCK"
    MOMENTUM_STOP = "MOMENTUM_STOP"
    PARTIAL_CLOSE = "PARTIAL_CLOSE"


@dataclass(frozen=True)
class PropFirmProtectionSnapshot:
    """Fresh, already-validated risk information for one protection check."""

    profile_id: str
    profile_version: str
    observed_at: datetime
    remaining_overall_buffer: Decimal
    remaining_daily_buffer: Decimal | None = None
    hard_limit_breached: bool = False
    recovery_mode: str = "NORMAL"
    transaction_cost_reserve: Decimal = Decimal("0")
    slippage_reserve: Decimal = Decimal("0")

    def __post_init__(self) -> None:
        if not self.profile_id or not self.profile_version:
            raise ValueError("profile identity is required")
        if self.observed_at.tzinfo is None or self.observed_at.utcoffset() is None:
            raise ValueError("observed_at must be timezone-aware")
        for field_name in (
            "remaining_overall_buffer",
            "remaining_daily_buffer",
            "transaction_cost_reserve",
            "slippage_reserve",
        ):
            value = getattr(self, field_name)
            if value is None:
                continue
            if not isinstance(value, Decimal) or not value.is_finite():
                raise ValueError(f"{field_name} must be a finite Decimal")
            if value < 0:
                raise ValueError(f"{field_name} must not be negative")
        if not isinstance(self.hard_limit_breached, bool):
            raise ValueError("hard_limit_breached must be a boolean")


@dataclass(frozen=True)
class ProtectionRequest:
    """Normalized request presented by Trade Manager to the guard."""

    action: ProtectionAction
    direction: str
    current_sl: Decimal
    requested_sl: Decimal | None
    current_price: Decimal
    current_profit: Decimal | None
    ticket: int
    symbol: str


@dataclass(frozen=True)
class ProtectionAuthorization:
    status: ValidationStatus
    authorized: bool
    reason: str
    profile_id: str | None = None
    profile_version: str | None = None


def _blocked(reason: str, snapshot: PropFirmProtectionSnapshot | None) -> ProtectionAuthorization:
    return ProtectionAuthorization(
        status=ValidationStatus.UNKNOWN,
        authorized=False,
        reason=reason,
        profile_id=snapshot.profile_id if snapshot else None,
        profile_version=snapshot.profile_version if snapshot else None,
    )


class PropFirmProtectionGuard:
    """Fail-closed authorization for already-selected protective actions."""

    def __init__(
        self,
        snapshot: PropFirmProtectionSnapshot,
        *,
        max_age_seconds: int = 300,
    ) -> None:
        if max_age_seconds <= 0:
            raise ValueError("max_age_seconds must be positive")
        self._snapshot = snapshot
        self._max_age = timedelta(seconds=max_age_seconds)

    @classmethod
    def from_budget_decision(
        cls,
        decision: LossBudgetDecision,
        *,
        max_age_seconds: int = 300,
    ) -> "PropFirmProtectionGuard":
        """Build a guard from the exact decision already used for sizing."""
        if not isinstance(decision, LossBudgetDecision):
            raise ValueError("decision must be LossBudgetDecision")
        if decision.status is not ValidationStatus.VALID:
            raise ValueError("Only a VALID loss-budget decision can authorize protection")
        if decision.remaining_overall_buffer is None:
            raise ValueError("Loss-budget decision lacks overall buffer")
        if decision.remaining_daily_buffer is not None and decision.remaining_daily_buffer < 0:
            raise ValueError("Loss-budget decision has an invalid daily buffer")
        snapshot = PropFirmProtectionSnapshot(
            profile_id=decision.profile_id,
            profile_version=decision.profile_version,
            observed_at=decision.evaluated_at,
            remaining_overall_buffer=decision.remaining_overall_buffer,
            remaining_daily_buffer=decision.remaining_daily_buffer,
            hard_limit_breached=decision.remaining_overall_buffer <= 0,
            recovery_mode=decision.recovery_mode or "NORMAL",
            transaction_cost_reserve=decision.transaction_cost_reserve or Decimal("0"),
            slippage_reserve=decision.slippage_reserve or Decimal("0"),
        )
        return cls(snapshot, max_age_seconds=max_age_seconds)

    @property
    def snapshot(self) -> PropFirmProtectionSnapshot:
        return self._snapshot

    def authorize(
        self,
        request: ProtectionRequest,
        *,
        as_of: datetime | None = None,
    ) -> ProtectionAuthorization:
        """Authorize only risk-reducing, fresh and auditable actions."""

        snapshot = self._snapshot
        now = as_of or datetime.now(timezone.utc)
        if now.tzinfo is None or now.utcoffset() is None:
            return _blocked("Protection evaluation time is not timezone-aware", snapshot)
        if snapshot.observed_at > now:
            return _blocked("Prop-firm protection snapshot is from the future", snapshot)
        if now - snapshot.observed_at > self._max_age:
            return _blocked("Prop-firm protection snapshot is stale", snapshot)
        if snapshot.remaining_overall_buffer < 0:
            return _blocked("Overall protection buffer is invalid", snapshot)
        if (
            snapshot.remaining_daily_buffer is not None
            and snapshot.remaining_daily_buffer < 0
        ):
            return _blocked("Daily protection buffer is invalid", snapshot)

        if request.action is ProtectionAction.PARTIAL_CLOSE:
            if request.current_profit is None or request.current_profit <= 0:
                return _blocked(
                    "Partial close requires broker-confirmed positive profit",
                    snapshot,
                )
            reason = "Profitable partial close is risk-reducing"
        elif request.action in {
            ProtectionAction.BREAKEVEN,
            ProtectionAction.PROFIT_LOCK,
            ProtectionAction.MOMENTUM_STOP,
        }:
            if not self._protective_stop_is_valid(request):
                return _blocked(
                    "Requested stop is not a valid protective tightening",
                    snapshot,
                )
            reason = "Protective stop tightening is risk-reducing"
        else:
            return _blocked("Protection action is not approved", snapshot)

        if snapshot.hard_limit_breached:
            reason += "; allowed to preserve protection after limit breach"
        authorization = ProtectionAuthorization(
            status=ValidationStatus.VALID,
            authorized=True,
            reason=reason,
            profile_id=snapshot.profile_id,
            profile_version=snapshot.profile_version,
        )
        logger.info(
            '{"event":"prop_firm_protection_authorized","ticket":%s,'
            '"symbol":"%s","action":"%s","profile_id":"%s",'
            '"profile_version":"%s","recovery_mode":"%s",'
            '"remaining_overall_buffer":"%s"}',
            request.ticket,
            request.symbol,
            request.action.value,
            snapshot.profile_id,
            snapshot.profile_version,
            snapshot.recovery_mode,
            snapshot.remaining_overall_buffer,
        )
        return authorization

    @staticmethod
    def _protective_stop_is_valid(request: ProtectionRequest) -> bool:
        if request.requested_sl is None:
            return False
        values = (
            request.current_sl,
            request.requested_sl,
            request.current_price,
        )
        if not all(isinstance(value, Decimal) and value.is_finite() for value in values):
            return False
        direction = request.direction.upper()
        if direction == "BUY":
            return (
                request.requested_sl <= request.current_price
                and (
                    request.current_sl <= 0
                    or request.requested_sl > request.current_sl
                )
            )
        if direction == "SELL":
            return (
                request.requested_sl >= request.current_price
                and (
                    request.current_sl <= 0
                    or request.requested_sl < request.current_sl
                )
            )
        return False
