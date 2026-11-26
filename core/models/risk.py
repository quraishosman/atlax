"""
ATLAX Risk Engine Data Models.

Defines the immutable data structures that flow through the Risk Engine:
    - AccountState:   Snapshot of live account conditions passed IN to the engine.
    - RiskCalculation: Intermediate lot-size arithmetic (internal, auditable).
    - RiskEvaluation: Output contract from the Risk Engine to the Execution Engine.

Authority: docs/10_RISK_ENGINE.md
           docs/06_DATA_MODELS.md (Approval decision, Execution request)

Architectural Boundaries:
    - RiskEvaluation MUST NOT be consumed by the Strategy Engine or Confidence Engine.
    - RiskEvaluation is the gate between Confidence Engine output and Execution Engine input.
    - AccountState is populated by the Execution Engine from MT5 account info —
      the Risk Engine itself NEVER calls MT5.
    - lot_size is ONLY populated when approved is True.
      It must be None when approved is False.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from decimal import Decimal
from typing import Optional

from core.models.prop_firm import LossBudgetDecision


@dataclass(frozen=True)
class AccountState:
    """
    Live account conditions snapshot passed to the Risk Engine.

    Populated by the Execution Engine from MT5 account info.
    The Risk Engine is purely mathematical — it never reads MT5 directly.

    Attributes:
        balance:             Current account balance (deposit currency).
        equity:              Current account equity (balance + floating PnL).
        daily_pnl:           Net realised PnL for the current trading day.
                             Negative = loss. Positive = profit.
        daily_pnl_available:
                             Whether `daily_pnl` was established from a
                             trustworthy live broker-history read. A false
                             value blocks new live trades.
        weekly_pnl:          Net realised PnL for the current trading week.
        weekly_pnl_available:
                             Whether `weekly_pnl` was established from a
                             trustworthy live broker-history read. A false
                             value blocks new live trades.
        open_trade_count:    Number of currently open positions.
        trades_today:        Number of trades opened today (closed or open).
        daily_trade_count_available:
                             Whether `trades_today` was established from a
                             trustworthy live broker-history read. A false
                             value blocks new live trades.
        open_symbols:        Tuple of symbols currently held open (may include
                             duplicates if multiple positions on same symbol).
        currency:            Deposit currency code (e.g. "USD").
        snapshot_at:         UTC timestamp when this snapshot was taken.
    """
    balance: Decimal
    equity: Decimal
    daily_pnl: Decimal
    weekly_pnl: Decimal
    open_trade_count: int
    trades_today: int
    open_symbols: tuple[str, ...]
    currency: str
    snapshot_at: datetime
    # Broker symbol metadata used for precise cash-risk sizing. Live scanner
    # snapshots populate these values from MT5 symbol_info().
    symbol_tick_value: Optional[Decimal] = None
    symbol_tick_size: Optional[Decimal] = None
    symbol_volume_min: Optional[Decimal] = None
    symbol_volume_max: Optional[Decimal] = None
    symbol_volume_step: Optional[Decimal] = None
    # Live MT5 snapshots set this to true so incomplete symbol economics
    # cannot fall back to global compatibility values.
    broker_metadata_required: bool = False
    # Live MT5 snapshots must explicitly attest that the daily ATLAX opening
    # count was obtained from broker history before the daily cap may pass.
    daily_trade_count_available: bool = False
    # Live MT5 snapshots must explicitly attest that daily realized PnL was
    # obtained from broker history before the daily loss gate may pass.
    daily_pnl_available: bool = False
    # Live MT5 snapshots must explicitly attest that weekly realized PnL was
    # obtained from broker history before the weekly loss gate may pass.
    weekly_pnl_available: bool = False

    def __post_init__(self) -> None:
        if self.balance <= Decimal("0"):
            raise ValueError(
                f"AccountState.balance must be positive, got {self.balance}"
            )
        if self.equity <= Decimal("0"):
            raise ValueError(
                f"AccountState.equity must be positive, got {self.equity}"
            )
        if self.open_trade_count < 0:
            raise ValueError(
                f"AccountState.open_trade_count must be >= 0, got {self.open_trade_count}"
            )
        if self.trades_today < 0:
            raise ValueError(
                f"AccountState.trades_today must be >= 0, got {self.trades_today}"
            )
        if not isinstance(self.daily_trade_count_available, bool):
            raise ValueError(
                "AccountState.daily_trade_count_available must be a boolean"
            )
        if not isinstance(self.daily_pnl_available, bool):
            raise ValueError(
                "AccountState.daily_pnl_available must be a boolean"
            )
        if not isinstance(self.weekly_pnl_available, bool):
            raise ValueError(
                "AccountState.weekly_pnl_available must be a boolean"
            )
        if not self.currency or len(self.currency) != 3:
            raise ValueError(
                f"AccountState.currency must be a 3-letter code (e.g. USD), "
                f"got {self.currency!r}"
            )


@dataclass(frozen=True)
class RiskCalculation:
    """
    Intermediate lot-size arithmetic.

    Exposed for audit logging and unit-test assertion.
    Never modifiable after construction.

    Attributes:
        sl_distance_price:  Stop-loss distance in price terms
                            (|entry_ref - invalidation_level|).
        sl_distance_pips:   Stop-loss distance converted to pips.
        pip_value:          Value of 1 pip per lot in deposit currency.
        risk_amount:        Monetary amount risked on this trade.
        raw_lot_size:       Computed lot size before rounding.
        lot_size:           Final rounded lot size.
        min_lot:            Broker minimum lot size.
        max_lot:            Broker maximum lot size.
        lot_step:           Broker lot step (rounding granularity).
    """
    sl_distance_price: Decimal
    sl_distance_pips: Decimal
    pip_value: Decimal
    risk_amount: Decimal
    raw_lot_size: Decimal
    lot_size: Decimal
    min_lot: Decimal
    max_lot: Decimal
    lot_step: Decimal


@dataclass(frozen=True)
class RiskEvaluation:
    """
    Output contract from the Risk Engine.

    The Execution Engine must never proceed without an approved RiskEvaluation.
    Every rejection must include an explicit, human-readable reason that is logged.

    Attributes:
        candidate_id:   The TradeCandidate this evaluation covers.
        approved:       True only when ALL risk controls pass.
        lot_size:       Computed lot size. None when approved is False.
        calculation:    Full arithmetic breakdown for audit. None if rejected
                        before lot-size calculation (e.g., daily loss hit).
        rejection_code: Short machine-readable rejection code. None if approved.
        reason:         Human-readable explanation — mandatory in all cases.
        evaluated_at:   UTC timestamp of evaluation.
    """
    candidate_id: str
    approved: bool
    lot_size: Optional[Decimal]
    calculation: Optional[RiskCalculation]
    rejection_code: Optional[str]
    reason: str
    evaluated_at: datetime
    loss_budget: Optional[LossBudgetDecision] = None

    def __post_init__(self) -> None:
        # Invariant: lot_size is only set when approved
        if self.approved and self.lot_size is None:
            raise ValueError(
                "RiskEvaluation.lot_size must be set when approved=True."
            )
        if not self.approved and self.lot_size is not None:
            raise ValueError(
                "RiskEvaluation.lot_size must be None when approved=False."
            )
        if not self.reason:
            raise ValueError("RiskEvaluation.reason must never be empty.")

    def __str__(self) -> str:
        status = "APPROVED" if self.approved else f"REJECTED[{self.rejection_code}]"
        lot = f" lot={self.lot_size}" if self.lot_size is not None else ""
        return (
            f"RiskEvaluation({self.candidate_id[:8]}... | "
            f"{status}{lot} | {self.reason[:80]})"
        )

    def to_dict(self) -> dict:
        """Serialize the optional prop-firm budget decision for audit storage."""
        data = asdict(self)
        if self.loss_budget is not None:
            data["loss_budget"] = self.loss_budget.to_dict()
        return data
