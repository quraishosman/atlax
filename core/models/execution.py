"""
ATLAX Execution Engine Data Models.

Defines the immutable data structures for the Execution Engine I/O contract:

    ExecutionRequest:  Input to the Execution Engine — the approved candidate
                       combined with its RiskEvaluation.
    MT5RequestPayload: Sanitized representation of the MqlTradeRequest built
                       for MT5 (never contains raw broker credentials).
    ExecutionRecord:   Full audit record of one execution attempt. Persisted
                       regardless of success or failure.
    ExecutionStatus:   Enum of all possible terminal states.

Authority: docs/11_EXECUTION_ENGINE.md
           docs/05_API_SPECIFICATION.md (Execution request required inputs)
           docs/06_DATA_MODELS.md (Execution request, Trade lifecycle event)
           docs/16_SECURITY.md (credential redaction from logs and records)

Architectural Boundaries:
    - ExecutionRequest must carry a RiskEvaluation with approved=True.
      The Execution Engine must refuse any request without it.
    - MT5RequestPayload must NEVER store broker credentials, passwords,
      or API keys. Sanitized before storage. Authority: docs/16_SECURITY.md
    - ExecutionRecord must be created for EVERY attempt — whether it
      succeeds, fails pre-flight, or is rejected. No silent failures.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Optional

from core.models.risk import RiskEvaluation
from core.models.trade_candidate import TradeCandidate


class ExecutionStatus(str, Enum):
    """
    All possible terminal states for an execution attempt.

    Authority: docs/11_EXECUTION_ENGINE.md (step 7-8: result handling)
    """
    # Pre-flight outcomes
    RISK_NOT_APPROVED       = "RISK_NOT_APPROVED"       # RiskEvaluation.approved is False
    REQUEST_INVALID         = "REQUEST_INVALID"         # Validation failed before MT5 call
    PREFLIGHT_REJECTED      = "PREFLIGHT_REJECTED"      # MT5 order_check refused
    # Execution outcomes
    SENT                    = "SENT"                    # order_send called, result pending reconciliation
    FILLED                  = "FILLED"                  # Confirmed filled by MT5
    PARTIALLY_FILLED        = "PARTIALLY_FILLED"        # Partial fill returned
    REJECTED_BY_BROKER      = "REJECTED_BY_BROKER"      # MT5 returned non-zero retcode
    AMBIGUOUS               = "AMBIGUOUS"               # MT5 returned unclear state — requires reconciliation
    ERROR                   = "ERROR"                   # Unexpected Python/platform error


@dataclass(frozen=True)
class ExecutionRequest:
    """
    Input to the Execution Engine.

    Carries the approved TradeCandidate and its RiskEvaluation together.
    The Execution Engine must refuse to proceed if risk_evaluation.approved is False.

    Authority: docs/11_EXECUTION_ENGINE.md (Required Inputs section)

    Attributes:
        candidate:        The approved TradeCandidate from the Strategy Engine.
        risk_evaluation:  The approved RiskEvaluation from the Risk Engine.
                          MUST have approved=True. The engine validates this.
        magic_number:     ATLAX MT5 magic number — uniquely identifies ATLAX orders.
                          Must match the value in risk_config to allow position tracking.
        requested_at:     UTC timestamp when this execution was requested.
    """
    candidate: TradeCandidate
    risk_evaluation: RiskEvaluation
    magic_number: int
    requested_at: datetime

    def __post_init__(self) -> None:
        if self.risk_evaluation.candidate_id != self.candidate.candidate_id:
            raise ValueError(
                f"ExecutionRequest mismatch: "
                f"candidate_id={self.candidate.candidate_id!r} does not match "
                f"risk_evaluation.candidate_id={self.risk_evaluation.candidate_id!r}. "
                f"Authority: docs/11_EXECUTION_ENGINE.md step 2."
            )
        if self.magic_number <= 0:
            raise ValueError(
                f"magic_number must be a positive integer, got {self.magic_number}"
            )


@dataclass(frozen=True)
class MT5RequestPayload:
    """
    Sanitized representation of the MqlTradeRequest sent to MT5.

    Stored in the ExecutionRecord for audit. Credentials are NEVER stored.
    Price fields are stored as strings (from Decimal) to avoid float precision loss.

    Authority: docs/11_EXECUTION_ENGINE.md (MT5 request lifecycle, field list)
               docs/16_SECURITY.md (credential redaction)

    Attributes:
        action:        MT5 TRADE_ACTION constant string (e.g., "TRADE_ACTION_DEAL").
        symbol:        Instrument symbol.
        volume:        Lot size as Decimal string.
        order_type:    MT5 ORDER_TYPE constant string (e.g., "ORDER_TYPE_BUY").
        price:         Requested price, or None for market orders.
        sl:            Stop-loss level.
        tp:            Take-profit level (TP2 = approved opposite-extreme target).
        deviation:     Maximum slippage in points.
        type_filling:  MT5 ORDER_FILLING constant string.
        magic:         ATLAX magic number.
        comment:       Sanitized candidate reference (no credentials).
    """
    action:       str
    symbol:       str
    volume:       str           # Decimal as string — no float imprecision
    order_type:   str
    price:        Optional[str]
    sl:           str
    tp:           str
    deviation:    int
    type_filling: str
    magic:        int
    comment:      str


@dataclass(frozen=True)
class ExecutionRecord:
    """
    Complete audit record for one execution attempt.

    Created and persisted for EVERY attempt, regardless of outcome.
    No execution attempt may be silent.

    Authority: docs/11_EXECUTION_ENGINE.md (Execution Result Handling section)
               docs/16_SECURITY.md (redaction requirements)

    Attributes:
        record_id:            Unique ID for this execution record.
        candidate_id:         Sourced from ExecutionRequest.
        profile:              Trader profile name.
        symbol:               Instrument symbol.
        timeframe:            Candle timeframe context.
        direction:            "BUY" or "SELL".
        lot_size:             Approved lot size from RiskEvaluation.
        risk_evaluation_id:   candidate_id from RiskEvaluation (cross-reference).
        mt5_request:          Sanitized MT5 request payload. None if rejected pre-build.
        preflight_result:     MT5 order_check return code and comment. None if skipped.
        order_send_retcode:   MT5 order_send return code. None if send not reached.
        mt5_order_id:         MT5 order ID returned after send. None if not available.
        mt5_deal_id:          MT5 deal ID returned after fill. None if not available.
        broker_comment:       Broker/platform comment from MT5 result.
        status:               Final ExecutionStatus for this attempt.
        error_reason:         Human-readable error description. None if no error.
        recovery_suggestion:  How to recover. None if no error.
        attempted_at:         UTC timestamp when execution was attempted.
        completed_at:         UTC timestamp when execution settled (or errored).
    """
    record_id:              str
    candidate_id:           str
    profile:                str
    symbol:                 str
    timeframe:              str
    direction:              str
    lot_size:               Optional[Decimal]
    risk_evaluation_id:     str
    mt5_request:            Optional[MT5RequestPayload]
    preflight_result:       Optional[str]
    order_send_retcode:     Optional[int]
    mt5_order_id:           Optional[int]
    mt5_deal_id:            Optional[int]
    broker_comment:         Optional[str]
    status:                 ExecutionStatus
    error_reason:           Optional[str]
    recovery_suggestion:    Optional[str]
    attempted_at:           datetime
    completed_at:           datetime

    def __str__(self) -> str:
        lot = f" lot={self.lot_size}" if self.lot_size else ""
        return (
            f"ExecutionRecord({self.record_id[:8]}... | "
            f"{self.symbol} {self.timeframe} | {self.direction}{lot} | "
            f"{self.status.value})"
        )
