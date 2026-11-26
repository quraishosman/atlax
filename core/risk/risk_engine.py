"""
ATLAX Risk Engine.

Evaluates a TradeCandidate against all configured risk controls and,
when all controls pass, computes the approved lot size.

Authority: docs/10_RISK_ENGINE.md
           docs/13_CONFIGURATION.md (fail-closed config)
           docs/05_API_SPECIFICATION.md (RiskEvaluation output contract)

Architecture:
    - STATELESS. No memory between calls. All state comes in via AccountState.
    - NEVER calls MT5. AccountState is supplied by the Execution Engine.
    - NEVER guesses missing parameters. Returns REJECTED if any required
      input is absent or invalid.
    - NEVER bypasses limits because confidence is high.
    - Returns RiskEvaluation with approved=False (fail-closed) on any error.

Lot-Size Calculation:
    risk_amount     = equity * (risk_per_trade_pct / 100)
    sl_pips         = |entry_reference - invalidation_level| / pip_size
    pip_value       = pip_value_per_lot  (config, deposit currency per lot)
    raw_lot_size    = risk_amount / (sl_pips * pip_value)
    lot_size        = round_to_step(raw_lot_size, lot_step)
    lot_size        = clamp(lot_size, min_lot, max_lot)

Rejection Codes:
    DISABLED            Risk Engine is disabled in config.
    DAILY_LOSS_HIT      Daily loss limit reached or exceeded.
    DAILY_PNL_UNAVAILABLE
                        Live broker history cannot establish today's ATLAX
                        realized PnL.
    WEEKLY_LOSS_HIT     Weekly loss limit reached or exceeded.
    WEEKLY_PNL_UNAVAILABLE
                        Live broker history cannot establish the current ISO
                        week's ATLAX realized PnL.
    MAX_OPEN_TRADES     Global or profile open-trade limit reached.
    DAILY_TRADE_COUNT_UNAVAILABLE
                        Live broker history cannot establish today's ATLAX
                        opening-trade count.
    MAX_DAILY_TRADES    Profile daily-trade limit reached.
    MAX_CORRELATED      Correlated-pair limit reached for this symbol.
    PROFILE_NOT_FOUND   No risk profile configured for the candidate's profile.
    INVALID_SL          Stop-loss distance is zero or negative.
    LOT_BELOW_MIN       Computed lot size is below broker minimum.
    CONFIG_ERROR        Unexpected config or arithmetic failure (fail-closed).
"""

from __future__ import annotations

import logging
import math
from dataclasses import replace
from datetime import datetime, timezone
from decimal import ROUND_DOWN, Decimal, InvalidOperation
from typing import Optional

from core.models.prop_firm import LossBudgetDecision
from core.models.risk import AccountState, RiskCalculation, RiskEvaluation
from core.models.trade_candidate import TradeCandidate
from core.risk.loss_budget import PropFirmRiskContext, evaluate_loss_budget
from core.settings.risk_config import RiskConfig

logger = logging.getLogger("atlax.risk")

# Rejection code constants — never hardcode strings in callers
REJECTED_DISABLED           = "DISABLED"
REJECTED_DAILY_LOSS         = "DAILY_LOSS_HIT"
REJECTED_DAILY_PNL_UNAVAILABLE = "DAILY_PNL_UNAVAILABLE"
REJECTED_WEEKLY_LOSS        = "WEEKLY_LOSS_HIT"
REJECTED_WEEKLY_PNL_UNAVAILABLE = "WEEKLY_PNL_UNAVAILABLE"
REJECTED_MAX_OPEN_TRADES    = "MAX_OPEN_TRADES"
REJECTED_DAILY_TRADE_COUNT_UNAVAILABLE = "DAILY_TRADE_COUNT_UNAVAILABLE"
REJECTED_MAX_DAILY_TRADES   = "MAX_DAILY_TRADES"
REJECTED_MAX_CORRELATED     = "MAX_CORRELATED"
REJECTED_PROFILE_NOT_FOUND  = "PROFILE_NOT_FOUND"
REJECTED_INVALID_SL         = "INVALID_SL"
REJECTED_LOT_BELOW_MIN      = "LOT_BELOW_MIN"
REJECTED_BROKER_METADATA    = "BROKER_METADATA_UNAVAILABLE"
REJECTED_PROP_FIRM_BUDGET   = "PROP_FIRM_LOSS_BUDGET"
REJECTED_ADAPTIVE_LOT_EXCEEDS_RISK = "ADAPTIVE_LOT_EXCEEDS_RISK"
REJECTED_ADAPTIVE_LOT_UNSUPPORTED = "ADAPTIVE_LOT_UNSUPPORTED"
REJECTED_CONFIG_ERROR       = "CONFIG_ERROR"


class RiskEngine:
    """
    Stateless risk evaluation and lot-size calculation engine.

    Usage:
        engine = RiskEngine()
        evaluation = engine.evaluate(candidate, risk_config, account_state)
        if evaluation.approved:
            # safe to pass to Execution Engine
            lot_size = evaluation.lot_size

    Authority: docs/10_RISK_ENGINE.md
    """

    def evaluate(
        self,
        candidate: TradeCandidate,
        config: RiskConfig,
        account: AccountState,
        prop_firm_context: Optional[PropFirmRiskContext] = None,
    ) -> RiskEvaluation:
        """
        Evaluate a TradeCandidate against all risk controls.

        Args:
            candidate: The TradeCandidate to evaluate.
            config:    Validated RiskConfig (from load_risk_config).
            account:   Live AccountState snapshot (from Execution Engine / MT5).

        Returns:
            RiskEvaluation — approved with lot_size, or rejected with reason.
        """
        now = datetime.now(timezone.utc)

        try:
            return self._evaluate_inner(
                candidate,
                config,
                account,
                now,
                prop_firm_context,
            )
        except Exception as exc:  # noqa: BLE001
            # Any unexpected failure is a CONFIG_ERROR — fail closed
            logger.error(
                '{"event":"risk_config_error","candidate_id":"%s","error":"%s"}',
                candidate.candidate_id, str(exc)[:200],
            )
            return self._reject(
                candidate.candidate_id,
                REJECTED_CONFIG_ERROR,
                f"Unexpected risk engine error (fail-closed): {exc}",
                now,
            )

    def reject_external(
        self,
        candidate: TradeCandidate,
        code: str,
        reason: str,
    ) -> RiskEvaluation:
        """Return the standard fail-closed result for an upstream safety gate."""

        return self._reject(
            candidate.candidate_id,
            str(code),
            str(reason),
            datetime.now(timezone.utc),
        )

    # ------------------------------------------------------------------
    # Internal evaluation sequence
    # ------------------------------------------------------------------

    def _evaluate_inner(
        self,
        candidate: TradeCandidate,
        config: RiskConfig,
        account: AccountState,
        now: datetime,
        prop_firm_context: Optional[PropFirmRiskContext],
    ) -> RiskEvaluation:

        # 1. Master enable gate
        if not config.enabled:
            return self._reject(
                candidate.candidate_id,
                REJECTED_DISABLED,
                "Risk Engine is disabled in configuration (risk.enabled=false). "
                "No trades may be executed. Authority: docs/10_RISK_ENGINE.md",
                now,
            )

        # 2. Resolve profile risk settings
        profile_risk = config.get_profile(candidate.profile)
        if profile_risk is None:
            return self._reject(
                candidate.candidate_id,
                REJECTED_PROFILE_NOT_FOUND,
                f"No risk profile configured for profile={candidate.profile!r}. "
                f"Add an entry under risk.profiles in config/atlax.yaml. "
                f"Authority: docs/10_RISK_ENGINE.md",
                now,
            )

        # Prop-Firm Mode is opt-in. When a validated context is supplied,
        # evaluate its loss budget before any lot-size arithmetic and cap the
        # strategy risk to the smaller remaining allowance.
        loss_budget: Optional[LossBudgetDecision] = None
        risk_amount_cap: Optional[Decimal] = None
        if prop_firm_context is not None:
            loss_budget = evaluate_loss_budget(prop_firm_context, as_of=now)
            if loss_budget.blocked:
                return self._reject(
                    candidate.candidate_id,
                    REJECTED_PROP_FIRM_BUDGET,
                    loss_budget.reason,
                    now,
                    loss_budget=loss_budget,
                )
            risk_amount_cap = loss_budget.allowed_new_trade_risk

        # 3. Daily loss limit
        if not account.daily_pnl_available:
            return self._reject(
                candidate.candidate_id,
                REJECTED_DAILY_PNL_UNAVAILABLE,
                "Today's ATLAX realized PnL is unavailable from MT5 history. "
                "No new trade may be opened until the daily PnL is available. "
                "Authority: docs/10_RISK_ENGINE.md",
                now,
            )

        daily_loss_threshold = account.balance * Decimal(str(config.max_daily_loss_pct / 100))
        if account.daily_pnl <= -daily_loss_threshold:
            return self._reject(
                candidate.candidate_id,
                REJECTED_DAILY_LOSS,
                f"Daily loss limit reached: daily_pnl={account.daily_pnl} "
                f">= -{daily_loss_threshold:.2f} ({config.max_daily_loss_pct}% of "
                f"balance={account.balance}). No further trades today. "
                f"Authority: docs/10_RISK_ENGINE.md",
                now,
            )

        # 4. Weekly loss limit
        if not account.weekly_pnl_available:
            return self._reject(
                candidate.candidate_id,
                REJECTED_WEEKLY_PNL_UNAVAILABLE,
                "This week's ATLAX realized PnL is unavailable from MT5 history. "
                "No new trade may be opened until the weekly PnL is available. "
                "Authority: docs/10_RISK_ENGINE.md",
                now,
            )

        weekly_loss_threshold = account.balance * Decimal(str(config.max_weekly_loss_pct / 100))
        if account.weekly_pnl <= -weekly_loss_threshold:
            return self._reject(
                candidate.candidate_id,
                REJECTED_WEEKLY_LOSS,
                f"Weekly loss limit reached: weekly_pnl={account.weekly_pnl} "
                f">= -{weekly_loss_threshold:.2f} ({config.max_weekly_loss_pct}% of "
                f"balance={account.balance}). No further trades this week. "
                f"Authority: docs/10_RISK_ENGINE.md",
                now,
            )

        # 5. Maximum open trades (profile-specific)
        if account.open_trade_count >= profile_risk.max_open_trades:
            return self._reject(
                candidate.candidate_id,
                REJECTED_MAX_OPEN_TRADES,
                f"Profile '{candidate.profile}' max open trades reached: "
                f"open={account.open_trade_count}, limit={profile_risk.max_open_trades}. "
                f"Authority: docs/10_RISK_ENGINE.md",
                now,
            )

        # 6. Maximum trades per day (profile-specific). A missing broker
        # history result is not equivalent to zero trades; live execution
        # must stop until the scanner can establish an authoritative count.
        if not account.daily_trade_count_available:
            return self._reject(
                candidate.candidate_id,
                REJECTED_DAILY_TRADE_COUNT_UNAVAILABLE,
                "Today's ATLAX opening-trade count is unavailable from MT5 history. "
                "No new trade may be opened until the count is available. "
                "Authority: docs/10_RISK_ENGINE.md",
                now,
            )

        if account.trades_today >= profile_risk.max_trades_per_day:
            return self._reject(
                candidate.candidate_id,
                REJECTED_MAX_DAILY_TRADES,
                f"Profile '{candidate.profile}' daily trade limit reached: "
                f"trades_today={account.trades_today}, "
                f"limit={profile_risk.max_trades_per_day}. "
                f"Authority: docs/10_RISK_ENGINE.md",
                now,
            )

        # 7. Correlated pairs limit
        # Correlation: count open positions that share the same base or quote
        # currency as the candidate symbol (first 3 or last 3 chars of a 6-char pair).
        correlated_count = self._count_correlated(candidate.symbol, account.open_symbols)
        if correlated_count >= profile_risk.max_correlated_pairs:
            return self._reject(
                candidate.candidate_id,
                REJECTED_MAX_CORRELATED,
                f"Profile '{candidate.profile}' correlated-pair limit reached for "
                f"symbol={candidate.symbol!r}: correlated_open={correlated_count}, "
                f"limit={profile_risk.max_correlated_pairs}. "
                f"Authority: docs/10_RISK_ENGINE.md",
                now,
            )

        # 8. Compute lot size
        calc, error = self._compute_lot_size(
            candidate,
            config,
            account,
            profile_risk,
            risk_amount_cap=risk_amount_cap,
        )
        if error:
            return self._reject(
                candidate.candidate_id,
                error["code"],
                error["reason"],
                now,
                loss_budget=loss_budget,
            )

        calc, error = self._apply_adaptive_lot_target(
            candidate,
            config,
            account,
            calc,
        )
        if error:
            return self._reject(
                candidate.candidate_id,
                error["code"],
                error["reason"],
                now,
                loss_budget=loss_budget,
            )

        # 9. Lot size floor check
        if calc.lot_size < calc.min_lot:
            return self._reject(
                candidate.candidate_id,
                REJECTED_LOT_BELOW_MIN,
                f"Computed lot size {calc.lot_size} is below broker minimum "
                f"{calc.min_lot}. Risk amount may be too small for the SL "
                f"distance. Cannot trade with sub-minimum size. "
                f"Authority: docs/10_RISK_ENGINE.md",
                now,
                loss_budget=loss_budget,
            )

        # All controls passed — approved
        reason = (
            f"All risk controls passed for {candidate.symbol} {candidate.timeframe} "
            f"profile={candidate.profile}. "
            f"lot_size={calc.lot_size}, risk_amount={calc.risk_amount:.2f} "
            f"{account.currency}, sl_pips={calc.sl_distance_pips:.1f}."
        )
        logger.info(
            '{"event":"risk_approved","candidate_id":"%s","symbol":"%s",'
            '"profile":"%s","lot_size":"%s","risk_amount":"%s","sl_pips":"%s"}',
            candidate.candidate_id, candidate.symbol, candidate.profile,
            calc.lot_size, calc.risk_amount, calc.sl_distance_pips,
        )
        return RiskEvaluation(
            candidate_id=candidate.candidate_id,
            approved=True,
            lot_size=calc.lot_size,
            calculation=calc,
            rejection_code=None,
            reason=reason,
            evaluated_at=now,
            loss_budget=loss_budget,
        )

    # ------------------------------------------------------------------
    # Lot-size arithmetic
    # ------------------------------------------------------------------

    def _compute_lot_size(
        self,
        candidate: TradeCandidate,
        config: RiskConfig,
        account: AccountState,
        profile_risk,
        *,
        risk_amount_cap: Optional[Decimal] = None,
    ) -> tuple[Optional[RiskCalculation], Optional[dict]]:
        """
        Compute lot size from CRT geometry and risk settings.

        Returns (RiskCalculation, None) on success.
        Returns (None, {"code": ..., "reason": ...}) on failure.
        """
        try:
            entry_ref   = candidate.entry_model.reference_price
            sl_level    = candidate.invalidation_model.invalidation_level
            pip_size    = Decimal(str(candidate.confidence_inputs.get("pip_size") or "0.0001"))

            sl_distance_price = abs(entry_ref - sl_level)

            if sl_distance_price <= Decimal("0"):
                return None, {
                    "code": REJECTED_INVALID_SL,
                    "reason": (
                        f"Stop-loss distance is zero or negative: "
                        f"entry_ref={entry_ref}, sl_level={sl_level}. "
                        f"Cannot compute lot size. "
                        f"Authority: docs/10_RISK_ENGINE.md"
                    ),
                }

            sl_distance_pips = sl_distance_price / pip_size
            strategy_risk_amount = account.equity * Decimal(
                str(profile_risk.risk_per_trade_pct / 100)
            )
            if risk_amount_cap is not None:
                if risk_amount_cap <= 0:
                    return None, {
                        "code": REJECTED_PROP_FIRM_BUDGET,
                        "reason": "Prop-firm loss budget allows no positive new-trade risk.",
                    }
                risk_amount = min(strategy_risk_amount, risk_amount_cap)
            else:
                risk_amount = strategy_risk_amount

            broker_fields = {
                "trade_tick_value": account.symbol_tick_value,
                "trade_tick_size": account.symbol_tick_size,
                "volume_min": account.symbol_volume_min,
                "volume_max": account.symbol_volume_max,
                "volume_step": account.symbol_volume_step,
            }

            def _is_positive(value: Optional[Decimal]) -> bool:
                try:
                    return value is not None and value > Decimal("0")
                except (TypeError, InvalidOperation):
                    return False

            incomplete_fields = [
                name for name, value in broker_fields.items()
                if not _is_positive(value)
            ]
            if account.broker_metadata_required and incomplete_fields:
                return None, {
                    "code": REJECTED_BROKER_METADATA,
                    "reason": (
                        f"Required broker symbol metadata is missing or invalid for "
                        f"{candidate.symbol}: {', '.join(incomplete_fields)}. "
                        "Live sizing is blocked; refresh MT5 symbol_info and retry. "
                        "Authority: docs/10_RISK_ENGINE.md"
                    ),
                }

            # Prefer broker tick economics and volume constraints. Global
            # config values are an explicit offline fallback only when
            # broker_metadata_required is false.
            tick_value = account.symbol_tick_value
            tick_size = account.symbol_tick_size
            if _is_positive(tick_value) and _is_positive(tick_size):
                value_per_price_unit = tick_value / tick_size
                pip_value_per_lot = value_per_price_unit * pip_size
                cash_per_lot_at_sl = sl_distance_price * value_per_price_unit
            else:
                logger.warning(
                    '{"event":"risk_symbol_valuation_fallback","symbol":"%s",'
                    '"candidate_id":"%s"}',
                    candidate.symbol, candidate.candidate_id,
                )
                pip_value_per_lot = Decimal(str(config.pip_value_per_lot))
                cash_per_lot_at_sl = sl_distance_pips * pip_value_per_lot

            min_lot = account.symbol_volume_min or Decimal(str(config.min_lot))
            max_lot = account.symbol_volume_max or Decimal(str(config.max_lot))
            lot_step = account.symbol_volume_step or Decimal(str(config.lot_step))
            if cash_per_lot_at_sl <= 0 or min_lot <= 0 or max_lot < min_lot or lot_step <= 0:
                return None, {
                    "code": REJECTED_CONFIG_ERROR,
                    "reason": "Broker cash-risk valuation or volume constraints are invalid.",
                }
            raw_lot_size = risk_amount / cash_per_lot_at_sl

            # Round DOWN to lot_step to never exceed intended risk
            lot_size = self._round_to_step(raw_lot_size, lot_step)
            lot_size = max(Decimal("0"), min(lot_size, max_lot))

            calc = RiskCalculation(
                sl_distance_price=sl_distance_price,
                sl_distance_pips=sl_distance_pips,
                pip_value=pip_value_per_lot,
                risk_amount=risk_amount,
                raw_lot_size=raw_lot_size,
                lot_size=lot_size,
                min_lot=min_lot,
                max_lot=max_lot,
                lot_step=lot_step,
            )
            return calc, None

        except (InvalidOperation, ZeroDivisionError, ArithmeticError) as exc:
            return None, {
                "code": REJECTED_CONFIG_ERROR,
                "reason": (
                    f"Arithmetic error in lot-size calculation: {exc}. "
                    f"Check pip_size, pip_value_per_lot, and SL geometry. "
                    f"Authority: docs/10_RISK_ENGINE.md"
                ),
            }

    def _apply_adaptive_lot_target(
        self,
        candidate: TradeCandidate,
        config: RiskConfig,
        account: AccountState,
        calc: RiskCalculation,
    ) -> tuple[RiskCalculation, Optional[dict]]:
        """Apply the configured daily-loss lot target without exceeding risk.

        The normal risk calculation remains the upper bound. Gold and any
        other configured excluded symbol prefix keep their ordinary sizing.
        """
        policy = config.adaptive_lot_sizing
        if policy is None or not policy.enabled:
            return calc, None

        symbol = candidate.symbol.upper()
        if any(symbol.startswith(prefix) for prefix in policy.excluded_symbol_prefixes):
            return calc, None

        daily_loss_pct = max(
            Decimal("0"),
            (-account.daily_pnl / account.balance) * Decimal("100"),
        )
        caution_threshold = Decimal(str(policy.caution_daily_loss_pct))
        recovery_threshold = Decimal(str(policy.recovery_daily_loss_pct))
        if daily_loss_pct >= recovery_threshold:
            target_lot = Decimal(str(policy.recovery_lot))
            tier = "RECOVERY"
        elif daily_loss_pct >= caution_threshold:
            target_lot = Decimal(str(policy.caution_lot))
            tier = "CAUTION"
        else:
            target_lot = Decimal(str(policy.normal_lot))
            tier = "NORMAL"

        if (
            target_lot < calc.min_lot
            or target_lot > calc.max_lot
            or self._round_to_step(target_lot, calc.lot_step) != target_lot
        ):
            return calc, {
                "code": REJECTED_ADAPTIVE_LOT_UNSUPPORTED,
                "reason": (
                    f"Configured {tier} target lot {target_lot} is not supported "
                    f"by broker volume constraints for {candidate.symbol}: "
                    f"min={calc.min_lot}, max={calc.max_lot}, step={calc.lot_step}."
                ),
            }

        if calc.lot_size < target_lot:
            return calc, {
                "code": REJECTED_ADAPTIVE_LOT_EXCEEDS_RISK,
                "reason": (
                    f"Configured {tier} target lot {target_lot} for {candidate.symbol} "
                    f"exceeds the broker-derived risk-safe lot {calc.lot_size} "
                    f"at daily_loss_pct={daily_loss_pct:.2f}."
                ),
            }

        logger.info(
            '{"event":"adaptive_lot_target_selected","candidate_id":"%s",'
            '"symbol":"%s","tier":"%s","daily_loss_pct":"%s",'
            '"target_lot":"%s","risk_safe_lot":"%s"}',
            candidate.candidate_id,
            candidate.symbol,
            tier,
            daily_loss_pct,
            target_lot,
            calc.lot_size,
        )
        actual_risk_amount = (calc.risk_amount / calc.raw_lot_size) * target_lot
        return replace(
            calc,
            lot_size=target_lot,
            risk_amount=actual_risk_amount,
        ), None

    @staticmethod
    def _round_to_step(value: Decimal, step: Decimal) -> Decimal:
        """Round a Decimal DOWN to the nearest lot step."""
        if step <= Decimal("0"):
            raise ValueError(f"lot_step must be > 0, got {step}")
        steps = (value / step).to_integral_value(rounding=ROUND_DOWN)
        return steps * step

    # ------------------------------------------------------------------
    # Correlation helper
    # ------------------------------------------------------------------

    @staticmethod
    def _count_correlated(symbol: str, open_symbols: tuple[str, ...]) -> int:
        """
        Count how many open positions share a currency with the candidate.

        For a 6-character forex pair (e.g., EURUSD):
            base  = symbol[:3]   (EUR)
            quote = symbol[3:6]  (USD)

        A position is correlated if it shares either the base or quote currency.
        Special assets like XAUUSD (Gold) are handled: the first 3 chars (XAU)
        are matched literally.

        Returns:
            The count of currently open symbols that are correlated with symbol.
        """
        if len(symbol) < 6:
            return 0
        base  = symbol[:3].upper()
        quote = symbol[3:6].upper()
        count = 0
        for open_sym in open_symbols:
            if len(open_sym) < 6:
                continue
            ob = open_sym[:3].upper()
            oq = open_sym[3:6].upper()
            if ob == base or oq == base or ob == quote or oq == quote:
                count += 1
        return count

    # ------------------------------------------------------------------
    # Rejection helper
    # ------------------------------------------------------------------

    @staticmethod
    def _reject(
        candidate_id: str,
        code: str,
        reason: str,
        now: datetime,
        loss_budget: Optional[LossBudgetDecision] = None,
    ) -> RiskEvaluation:
        logger.warning(
            '{"event":"risk_rejected","candidate_id":"%s","code":"%s","reason":"%s"}',
            candidate_id, code, reason[:200],
        )
        return RiskEvaluation(
            candidate_id=candidate_id,
            approved=False,
            lot_size=None,
            calculation=None,
            rejection_code=code,
            reason=reason,
            evaluated_at=now,
            loss_budget=loss_budget,
        )
