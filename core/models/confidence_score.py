"""
ATLAX Confidence Score Data Model.

The structured output of the Confidence Engine — one score per
TradeCandidate evaluation. Fully explainable, versioned, and
immutable.

Authority Documents:
    - docs/09_CONFIDENCE_ENGINE.md (required output fields)
    - docs/05_API_SPECIFICATION.md (Trade Candidate Contract)

Required Output Fields (docs/09_CONFIDENCE_ENGINE.md):
    - Final score
    - Breakdown by factor
    - Configuration snapshot ID
    - Data inputs used
    - Missing or UNKNOWN factors
    - Explanation
    - Version
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional


@dataclass(frozen=True)
class FactorScore:
    """
    The score for a single confidence factor.

    Attributes:
        factor_name: Identifier for this factor (e.g., "session_alignment").
        raw_value: The raw metadata value used for scoring (None = UNKNOWN).
        score: The computed score contribution (0.0 to factor max weight).
        max_score: The maximum possible contribution for this factor.
        is_unknown: True if the raw_value was None and could not be scored.
        explanation: Human-readable reason for this factor's score.
    """
    factor_name: str
    raw_value: object
    score: float
    max_score: float
    is_unknown: bool
    explanation: str

    @property
    def percentage(self) -> Optional[float]:
        """Score as a percentage of max_score. None if UNKNOWN."""
        if self.is_unknown or self.max_score == 0:
            return None
        return (self.score / self.max_score) * 100.0


@dataclass(frozen=True)
class ConfidenceScore:
    """
    The complete, explainable confidence score for a TradeCandidate.

    Produced by the Confidence Engine. Consumed by the Alert Engine
    to gate notifications and by Analytics for performance tracking.

    Authority: docs/09_CONFIDENCE_ENGINE.md
        "Confidence output must include: final score, breakdown by factor,
        configuration snapshot ID, data inputs used, missing or UNKNOWN
        factors, explanation, version."

    Attributes:
        candidate_id: ID of the TradeCandidate being scored.
        symbol: Trading instrument.
        timeframe: Candle timeframe.
        profile: Trader profile context.
        final_score: Overall confidence score (0.0 – 100.0).
                     Rescaled to account for missing/UNKNOWN factors.
        raw_score: Score before rescaling for missing factors.
        factor_breakdown: Per-factor scores with explanation.
        missing_factors: Factor names that returned UNKNOWN (not counted).
        config_snapshot_id: Version ID of the weights used for this score.
        explanation: Human-readable summary of the full score.
        version: Scoring model version string.
        scored_at: UTC timestamp when scoring was performed.
    """

    candidate_id: str
    symbol: str
    timeframe: str
    profile: str
    final_score: float
    raw_score: float
    factor_breakdown: tuple[FactorScore, ...]
    missing_factors: tuple[str, ...]
    config_snapshot_id: str
    explanation: str
    version: str
    scored_at: datetime

    def __post_init__(self) -> None:
        if not (0.0 <= self.final_score <= 100.0):
            raise ValueError(
                f"final_score must be between 0.0 and 100.0, got {self.final_score}"
            )

    @property
    def passes_threshold(self) -> bool:
        """
        Always False — threshold is evaluated externally by the Alert Engine
        using the profile-specific min_confidence_threshold from config.
        Use AlertEngine.should_alert() instead.
        """
        return False  # Threshold evaluation belongs to Alert Engine

    def __str__(self) -> str:
        return (
            f"ConfidenceScore({self.candidate_id[:8]}... | "
            f"{self.symbol} {self.timeframe} | {self.profile} | "
            f"score={self.final_score:.1f}/100 | "
            f"missing={list(self.missing_factors)})"
        )
