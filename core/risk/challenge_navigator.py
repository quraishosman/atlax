"""
Prop-Firm Challenge Navigator.

An optional post-risk-evaluation guard that reads the live loss budget and
automatically enforces safe operational modes during prop-firm challenges.

Behaviours:
  - COAST MODE: When remaining distance to the profit target falls within
    `coast_threshold_pct`, the navigator returns enforce_scalper=True. The
    scanner will then skip all profiles except 'scalper', reducing risk.
  - CIRCUIT BREAKER: When the remaining daily loss buffer falls within
    `daily_drawdown_halt_pct` of the nominal balance, the navigator returns
    halt_trading=True. The scanner rejects the candidate immediately with
    NAVIGATOR_CIRCUIT_BREAKER and also persists a halt flag in the runtime
    store so that the Telegram alert is sent once.

Configuration is read from config/atlax.yaml under the prop_firm_navigator
block. All thresholds are configurable — no values are hardcoded.

Architectural boundaries:
  - The navigator ONLY reads LossBudgetDecision and PropFirmRiskContext.
  - It NEVER modifies risk controls, overrides RiskEngine, or executes trades.
  - It evaluates AFTER loss_budget has been calculated, not before.
  - It is optional: if prop_firm_context or loss_budget is None, it is inert.

Authority: docs/10_RISK_ENGINE.md, docs/42_PROP_FIRM_LOSS_BUDGET_PLAN.md,
           docs/43_PROP_FIRM_ACCOUNT_MODE_SWITCH_PLAN.md
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Optional

import yaml

from core.risk.prop_firm_runtime import PropFirmRiskContext
from core.risk.loss_budget import LossBudgetDecision

logger = logging.getLogger("atlax.risk.challenge_navigator")


@dataclass
class NavigatorAction:
    enforce_scalper: bool = False
    halt_trading: bool = False
    reason: str = ""


class ChallengeNavigator:
    """
    Reads the live LossBudgetDecision to auto-pilot prop-firm challenge behaviour.
    """

    def __init__(self, config_path: str = "config/atlax.yaml"):
        self.enabled: bool = False
        self.coast_threshold: Decimal = Decimal("2.0")
        self.daily_drawdown_halt: Decimal = Decimal("1.0")
        # profit_target_pct must come from config — never hardcoded
        self.profit_target_pct: Optional[Decimal] = None
        self._load_config(config_path)

    def _load_config(self, config_path: str) -> None:
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f)
            nav_config = (data or {}).get("prop_firm_navigator", {})
            self.enabled = bool(nav_config.get("enabled", False))
            self.coast_threshold = Decimal(
                str(nav_config.get("coast_threshold_pct", "2.0"))
            )
            self.daily_drawdown_halt = Decimal(
                str(nav_config.get("daily_drawdown_halt_pct", "1.0"))
            )
            raw_target = nav_config.get("profit_target_pct")
            self.profit_target_pct = (
                Decimal(str(raw_target)) if raw_target is not None else None
            )
        except (FileNotFoundError, InvalidOperation, TypeError, ValueError) as exc:
            logger.error(
                '{"event":"navigator_config_load_failed","error":"%s"}', str(exc)[:160]
            )

    def evaluate_navigator(
        self,
        loss_budget: LossBudgetDecision,
        context: PropFirmRiskContext,
    ) -> NavigatorAction:
        """
        Evaluate the current loss budget and return the appropriate action.

        Returns NavigatorAction with enforce_scalper or halt_trading set if
        the configured thresholds are breached. Returns a no-op action when
        the navigator is disabled, the budget is blocked, or data is missing.
        """
        action = NavigatorAction()

        if not self.enabled:
            return action

        if loss_budget is None or context is None:
            return action

        # If loss budget itself is blocked, the existing circuit breaker already
        # handles this. The navigator only operates on an ALLOWED budget.
        if loss_budget.blocked:
            return action

        nominal_balance = getattr(context.profile, "nominal_balance", None)
        if not nominal_balance or nominal_balance <= 0:
            logger.warning(
                '{"event":"navigator_skipped","reason":"nominal_balance_unavailable"}'
            )
            return action

        # --- Circuit Breaker: Daily drawdown proximity ---
        remaining_daily = loss_budget.remaining_daily_buffer
        if remaining_daily is not None and remaining_daily >= 0:
            daily_pct_remaining = (remaining_daily / nominal_balance) * Decimal("100")
            if daily_pct_remaining <= self.daily_drawdown_halt:
                action.halt_trading = True
                action.reason = (
                    f"Daily loss buffer is {daily_pct_remaining:.2f}% of balance. "
                    f"Circuit breaker engaged (threshold: {self.daily_drawdown_halt}%)."
                )
                logger.warning(
                    '{"event":"navigator_circuit_breaker","daily_pct_remaining":"%.4f","threshold":"%.4f"}',
                    float(daily_pct_remaining),
                    float(self.daily_drawdown_halt),
                )
                return action

        # --- Coast Mode: Profit target proximity ---
        # Only active when profit_target_pct is explicitly configured.
        if self.profit_target_pct is None:
            return action

        remaining_overall = loss_budget.remaining_overall_buffer
        if remaining_overall is None:
            return action

        # Derive current account profit from original balance vs current buffer
        # overall_floor is the lowest permitted equity (balance - max_drawdown)
        # remaining_overall_buffer is equity minus that floor
        # We cannot know the current profit directly from LossBudgetDecision,
        # so we use the snapshot balance if available.
        snapshot_balance = getattr(context.snapshot, "balance", None)
        if snapshot_balance is None or snapshot_balance <= 0:
            return action

        current_profit_pct = (
            (snapshot_balance - nominal_balance) / nominal_balance * Decimal("100")
        )
        distance_to_target = self.profit_target_pct - current_profit_pct
        if Decimal("0") < distance_to_target <= self.coast_threshold:
            action.enforce_scalper = True
            action.reason = (
                f"Only {distance_to_target:.2f}% from profit target of {self.profit_target_pct}%. "
                f"Coast mode active (threshold: {self.coast_threshold}%)."
            )
            logger.info(
                '{"event":"navigator_coast_mode","distance_to_target":"%.4f","threshold":"%.4f"}',
                float(distance_to_target),
                float(self.coast_threshold),
            )

        return action
