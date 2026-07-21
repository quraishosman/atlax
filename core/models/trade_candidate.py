"""
ATLAX Trade Candidate Data Models.

Defines the typed contracts produced by the Strategy Engine and
consumed by the Confidence Engine, Alert Engine, and Execution Engine.

Authority Documents:
    - docs/05_API_SPECIFICATION.md (Trade Candidate Contract)
    - docs/08_STRATEGY_ENGINE.md (Strategy Engine outputs)
    - docs/03_ARCHITECTURE.md (system flow and layer boundaries)

Architectural Boundaries:
    - TradeCandidate must NOT include: lot_size, risk_percentage,
      order_type, account details. Those belong to the Execution Engine.
    - Direction is included only because CRT classification (bullish_crt /
      bearish_crt) explicitly authorizes it per docs/08_STRATEGY_ENGINE.md.
    - StrategyDecision wraps the result so callers always receive a typed
      response with a known outcome: trade_candidate | no_trade | UNKNOWN.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Optional


# ---------------------------------------------------------------------------
# Direction Constants
# ---------------------------------------------------------------------------

class Direction:
    """
    Trade direction values authorized by CRT classification.

    Authority: docs/08_STRATEGY_ENGINE.md
        "Direction only when authorized by the rulebook and strategy spec."

    Mapping authorized by CRT Rulebook:
        bullish_crt → BUY   (price swept below CRL, reversal expected upward)
        bearish_crt → SELL  (price swept above CRH, reversal expected downward)
    """
    BUY = "BUY"
    SELL = "SELL"
    UNKNOWN = "UNKNOWN"


# ---------------------------------------------------------------------------
# Entry and Stop Models
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class EntryModel:
    """
    The entry zone derived from CRT pattern geometry.

    Authority: docs/05_API_SPECIFICATION.md (Trade Candidate Contract)

    For bullish CRT:
        - entry_zone_high = sweep candle close (top of reversal area)
        - entry_zone_low  = parent CRL (bottom of reversal area)
        - reference_price = sweep candle close (conservative entry)

    For bearish CRT:
        - entry_zone_high = parent CRH (top of reversal area)
        - entry_zone_low  = sweep candle close (bottom of reversal area)
        - reference_price = sweep candle close (conservative entry)
    """
    entry_zone_high: Decimal
    entry_zone_low: Decimal
    reference_price: Decimal
    description: str

    def __post_init__(self) -> None:
        if self.entry_zone_high < self.entry_zone_low:
            raise ValueError(
                f"entry_zone_high ({self.entry_zone_high}) must be >= "
                f"entry_zone_low ({self.entry_zone_low})"
            )


@dataclass(frozen=True)
class InvalidationModel:
    """
    The stop-loss reference derived from CRT pattern geometry.

    This is the reference price for the stop-loss level.
    The Execution Engine computes the actual stop with broker offsets.

    For bullish CRT:
        - invalidation_level = sweep candle low (below the liquidity grab)
    For bearish CRT:
        - invalidation_level = sweep candle high (above the liquidity grab)
    """
    invalidation_level: Decimal
    description: str


@dataclass(frozen=True)
class TargetModel:
    """
    Take-profit targets derived from CRT pattern geometry.

    Authority: CRT-QUALITY-001 (midpoint_target, opposite_extreme_target)

    Targets (in order of conservatism):
        - midpoint_target: 50% of parent range (Mean Threshold)
        - opposite_extreme_target: full parent range (CRH for bullish, CRL for bearish)
    """
    midpoint_target: Decimal
    opposite_extreme_target: Decimal
    description: str


# ---------------------------------------------------------------------------
# Trade Candidate
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class TradeCandidate:
    """
    A validated trade opportunity produced by the Strategy Engine.

    Contains all information needed by the Confidence Engine to score
    the setup, and by the Alert Engine to notify the trader.
    The Execution Engine uses this (after approval) to calculate
    lot size, risk, and place the order.

    Authority:
        docs/05_API_SPECIFICATION.md — Trade Candidate Contract (required fields)
        docs/08_STRATEGY_ENGINE.md   — Strategy Engine output specification

    Forbidden fields (belong to Execution Engine, not Strategy Engine):
        lot_size, risk_percentage, order_type, account_id, broker_fields

    Attributes:
        candidate_id: UUID uniquely identifying this trade candidate.
        strategy_name: Name of the strategy that produced this candidate.
        source_detector_event_ids: IDs of the detector events that triggered this.
        symbol: Trading instrument.
        timeframe: Candle timeframe of the pattern.
        profile: Trader profile context (scalper / day_trader / swing_trader).
        direction: BUY or SELL. Authorized because bullish_crt / bearish_crt
                   classification explicitly maps to a direction.
        entry_model: Entry zone derived from CRT geometry.
        invalidation_model: Stop-loss reference from CRT geometry.
        target_model: Take-profit levels from CRT geometry.
        confidence_inputs: Raw metadata dict passed to the Confidence Engine.
        explanation: Human-readable summary of why this candidate was created.
        created_at: UTC timestamp when this candidate was produced.
    """

    candidate_id: str
    strategy_name: str
    source_detector_event_ids: tuple[str, ...]
    symbol: str
    timeframe: str
    profile: str
    direction: str
    entry_model: EntryModel
    invalidation_model: InvalidationModel
    target_model: TargetModel
    confidence_inputs: dict[str, Any]
    explanation: str
    created_at: datetime

    _VALID_DIRECTIONS = frozenset({"BUY", "SELL", "UNKNOWN"})
    _VALID_PROFILES = frozenset({"scalper", "day_trader", "swing_trader"})

    def __post_init__(self) -> None:
        if self.direction not in self._VALID_DIRECTIONS:
            raise ValueError(
                f"direction must be one of {self._VALID_DIRECTIONS}, "
                f"got {self.direction!r}"
            )
        if self.profile not in self._VALID_PROFILES:
            raise ValueError(
                f"profile must be one of {self._VALID_PROFILES}, "
                f"got {self.profile!r}"
            )

    def __str__(self) -> str:
        return (
            f"TradeCandidate({self.candidate_id[:8]}... | "
            f"{self.symbol} {self.timeframe} | {self.profile} | "
            f"{self.direction} | {self.strategy_name})"
        )


# ---------------------------------------------------------------------------
# Strategy Decision (wrapper output)
# ---------------------------------------------------------------------------

class StrategyOutcome:
    """Outcome constants for StrategyDecision."""
    TRADE_CANDIDATE = "trade_candidate"
    NO_TRADE = "no_trade"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class StrategyDecision:
    """
    The typed output of the Strategy Engine for every evaluation.

    Always returned — even when no trade is found — so callers
    always receive a structured response.

    Authority: docs/08_STRATEGY_ENGINE.md
        "A strategy output may produce: Trade Candidate | No Trade | UNKNOWN"

    Attributes:
        outcome: "trade_candidate" | "no_trade" | "UNKNOWN"
        candidate: The TradeCandidate if outcome is trade_candidate. None otherwise.
        reason: Human-readable explanation of the decision.
        symbol: The instrument evaluated.
        timeframe: The timeframe evaluated.
        timestamp: UTC timestamp of this decision.
    """

    outcome: str
    candidate: Optional[TradeCandidate]
    reason: str
    symbol: str
    timeframe: str
    timestamp: datetime

    _VALID_OUTCOMES = frozenset({"trade_candidate", "no_trade", "UNKNOWN"})

    def __post_init__(self) -> None:
        if self.outcome not in self._VALID_OUTCOMES:
            raise ValueError(
                f"outcome must be one of {self._VALID_OUTCOMES}, "
                f"got {self.outcome!r}"
            )
        if self.outcome == StrategyOutcome.TRADE_CANDIDATE and self.candidate is None:
            raise ValueError(
                "candidate must be provided when outcome is trade_candidate"
            )
        if self.outcome != StrategyOutcome.TRADE_CANDIDATE and self.candidate is not None:
            raise ValueError(
                "candidate must be None when outcome is not trade_candidate"
            )

    @property
    def is_trade(self) -> bool:
        return self.outcome == StrategyOutcome.TRADE_CANDIDATE

    def __str__(self) -> str:
        return (
            f"StrategyDecision({self.outcome} | "
            f"{self.symbol} {self.timeframe} | {self.reason})"
        )
