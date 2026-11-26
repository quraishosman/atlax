"""Deterministic new-entry safety circuit breaker."""

from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Any, Optional

from core.models.account_mode import AccountModeStatus, TradingAccountMode
from core.models.prop_firm import LossBudgetDecision, ValidationStatus
from core.models.risk import AccountState
from core.settings.circuit_breaker_config import CircuitBreakerConfig
from core.settings.risk_config import RiskConfig

logger = logging.getLogger("atlax.risk.circuit_breaker")

STATE_KEY = "operations.circuit_breaker"


class CircuitBreakerState(str, Enum):
    ARMED = "ARMED"
    WARNING = "WARNING"
    BLOCKED = "BLOCKED"
    PAUSED = "PAUSED"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class CircuitBreakerDecision:
    state: CircuitBreakerState
    allowed: bool
    reason_code: str
    reason: str
    evaluated_at: datetime
    transitioned: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "state": self.state.value,
            "allowed": self.allowed,
            "reason_code": self.reason_code,
            "reason": self.reason[:240],
            "evaluated_at": self.evaluated_at.astimezone(timezone.utc).isoformat(),
        }


def _blocked(
    state: CircuitBreakerState,
    code: str,
    reason: str,
    now: datetime,
) -> CircuitBreakerDecision:
    return CircuitBreakerDecision(state, False, code, reason, now)


def evaluate_circuit_breaker(
    config: CircuitBreakerConfig,
    *,
    mt5_available: Optional[bool],
    trade_allowed: Optional[bool],
    trade_expert: Optional[bool],
    manual_paused: bool,
    storage_available: bool,
    account_mode: TradingAccountMode,
    account_status: AccountModeStatus,
    prop_firm_context_ready: bool,
    account: Optional[AccountState],
    risk_config: Optional[RiskConfig],
    loss_budget: Optional[LossBudgetDecision],
    reconciliation_status: str | None = None,
    now: Optional[datetime] = None,
) -> CircuitBreakerDecision:
    """Evaluate only pre-entry safety conditions; never modifies positions."""

    evaluated_at = now or datetime.now(timezone.utc)
    if evaluated_at.tzinfo is None or evaluated_at.utcoffset() is None:
        raise ValueError("Circuit-breaker evaluation time must be timezone-aware")

    if not config.enabled:
        return CircuitBreakerDecision(
            CircuitBreakerState.ARMED,
            True,
            "CIRCUIT_BREAKER_DISABLED",
            "Circuit breaker is disabled by validated configuration",
            evaluated_at,
        )
    if config.block_on_storage_failure and not storage_available:
        return _blocked(
            CircuitBreakerState.UNKNOWN,
            "STORAGE_UNAVAILABLE",
            "Runtime safety state is unavailable; new entries are blocked",
            evaluated_at,
        )
    if manual_paused:
        return _blocked(
            CircuitBreakerState.PAUSED,
            "MANUAL_PAUSE",
            "New entries are paused by the administrator",
            evaluated_at,
        )
    if config.block_on_mt5_unavailable and mt5_available is not True:
        return _blocked(
            CircuitBreakerState.UNKNOWN,
            "MT5_UNAVAILABLE",
            "MT5 account or position state is unavailable; new entries are blocked",
            evaluated_at,
        )
    if config.block_on_mt5_trading_disabled and (
        trade_allowed is not True or trade_expert is not True
    ):
        return _blocked(
            CircuitBreakerState.BLOCKED,
            "MT5_TRADING_DISABLED",
            "MT5 does not currently allow expert trading; new entries are blocked",
            evaluated_at,
        )
    if config.block_on_reconciliation_uncertain:
        normalized_reconciliation = str(reconciliation_status or "UNKNOWN").upper()
        if normalized_reconciliation == "CONFLICT":
            return _blocked(
                CircuitBreakerState.BLOCKED,
                "RECONCILIATION_CONFLICT",
                "Broker and ATLAX trade state conflict; new entries are blocked",
                evaluated_at,
            )
        if normalized_reconciliation == "UNKNOWN":
            return _blocked(
                CircuitBreakerState.UNKNOWN,
                "RECONCILIATION_UNKNOWN",
                "Broker and ATLAX trade state cannot be verified; new entries are blocked",
                evaluated_at,
            )
    # Shadow mode is an audit mode. It must retain the existing live risk path
    # and record evidence without turning unknown Prop-Firm context into a live
    # entry block. Enforced and Auto-Enforced are the live guard modes.
    if config.block_on_prop_firm_unknown and account_mode in {
        TradingAccountMode.PROP_FIRM_ENFORCED,
        TradingAccountMode.AUTO_ENFORCED,
    }:
        if account_status is not AccountModeStatus.VERIFIED or not prop_firm_context_ready:
            return _blocked(
                CircuitBreakerState.UNKNOWN,
                "PROP_FIRM_CONTEXT_UNKNOWN",
                "Prop-Firm account, profile, or current risk context is not verified",
                evaluated_at,
            )
        if loss_budget is None:
            return _blocked(
                CircuitBreakerState.UNKNOWN,
                "PROP_FIRM_BUDGET_UNKNOWN",
                "Prop-Firm loss budget is unavailable; new entries are blocked",
                evaluated_at,
            )
        if loss_budget.status is not ValidationStatus.VALID or loss_budget.blocked:
            return _blocked(
                CircuitBreakerState.BLOCKED,
                "PROP_FIRM_LOSS_BUDGET",
                loss_budget.reason,
                evaluated_at,
            )
    if config.block_on_loss_limits:
        if account is None or risk_config is None:
            return _blocked(
                CircuitBreakerState.UNKNOWN,
                "RISK_CONTEXT_UNKNOWN",
                "Risk configuration or account state is unavailable",
                evaluated_at,
            )
        if not risk_config.enabled:
            return _blocked(
                CircuitBreakerState.BLOCKED,
                "RISK_ENGINE_DISABLED",
                "Risk Engine is disabled; new entries are blocked",
                evaluated_at,
            )
        if not account.daily_pnl_available:
            return _blocked(
                CircuitBreakerState.UNKNOWN,
                "DAILY_PNL_UNAVAILABLE",
                "Today's ATLAX realized PnL is unavailable from MT5 history; new entries are blocked",
                evaluated_at,
            )
        daily_limit = account.balance * Decimal(str(risk_config.max_daily_loss_pct / 100))
        if account.daily_pnl <= -daily_limit:
            return _blocked(
                CircuitBreakerState.BLOCKED,
                "DAILY_LOSS_HIT",
                "Configured daily loss limit has been reached",
                evaluated_at,
            )
        if not account.weekly_pnl_available:
            return _blocked(
                CircuitBreakerState.UNKNOWN,
                "WEEKLY_PNL_UNAVAILABLE",
                "This week's ATLAX realized PnL is unavailable from MT5 history; new entries are blocked",
                evaluated_at,
            )
        weekly_limit = account.balance * Decimal(str(risk_config.max_weekly_loss_pct / 100))
        if account.weekly_pnl <= -weekly_limit:
            return _blocked(
                CircuitBreakerState.BLOCKED,
                "WEEKLY_LOSS_HIT",
                "Configured weekly loss limit has been reached",
                evaluated_at,
            )

    return CircuitBreakerDecision(
        CircuitBreakerState.ARMED,
        True,
        "NONE",
        "Pre-entry safety checks passed",
        evaluated_at,
    )


class CircuitBreaker:
    """Persist state transitions and fail closed if state persistence fails."""

    def __init__(self, runtime_store: Any, config: CircuitBreakerConfig) -> None:
        self._runtime_store = runtime_store
        self._config = config

    @property
    def config(self) -> CircuitBreakerConfig:
        return self._config

    def evaluate(self, **kwargs: Any) -> CircuitBreakerDecision:
        try:
            storage_available = bool(kwargs.pop("storage_available"))
            decision = evaluate_circuit_breaker(
                self._config,
                storage_available=storage_available,
                **kwargs,
            )
            previous = self._runtime_store.get(STATE_KEY, {})
            previous_state = previous if isinstance(previous, dict) else {}
            transitioned = (
                previous_state.get("state") != decision.state.value
                or previous_state.get("reason_code") != decision.reason_code
            )
            if transitioned:
                self._runtime_store.set(
                    STATE_KEY,
                    {
                        **decision.to_dict(),
                        "reason_code": decision.reason_code,
                    },
                )
            return replace(decision, transitioned=transitioned)
        except Exception as exc:  # noqa: BLE001 - an unpersistable guard fails closed
            now = datetime.now(timezone.utc)
            fallback = _blocked(
                CircuitBreakerState.UNKNOWN,
                "CIRCUIT_BREAKER_STATE_UNAVAILABLE",
                f"Circuit-breaker state could not be evaluated safely: {str(exc)[:160]}",
                now,
            )
            logger.error(
                '{"event":"circuit_breaker_state_failed","reason":"%s"}',
                str(exc)[:200],
            )
            return fallback
