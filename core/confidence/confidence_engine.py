"""
ATLAX Confidence Engine.

Scores TradeCandidate quality using configurable, explainable weights.
Every factor is scored independently, documented, and traceable.

Authority Documents:
    - docs/09_CONFIDENCE_ENGINE.md (scoring responsibilities and constraints)
    - docs/03_ARCHITECTURE.md (layer: Confidence Engine layer)

Scoring Model: Static weighted average (v1.0).
    - Approved first model type per docs/09_CONFIDENCE_ENGINE.md:
      "Simple statistical aggregation. Weighted averages. Transparent calculations."
    - Advanced ML models are not used and not allowed until separately approved.

Factor Scoring Logic:
    Binary factors (session_alignment, htf_zone, htf_trend, fvg, msb_ltf):
        True  → full weight score
        False → 0 score
        None  → UNKNOWN, excluded from total and rescaled

    Continuous factors (sweep_distance, close_location, sweep_wick_ratio):
        Scored 0.0–1.0 using a configured ideal range, then scaled by weight.
        Values inside the ideal range → full score.
        Values outside the ideal range → linearly decayed score (min 0.0).

Rescaling for Missing Factors:
    When factors are UNKNOWN, they are excluded from the denominator.
    final_score = (sum of known scores / sum of known max weights) * 100.0
    This prevents the score from being artificially deflated by missing data.
    If ALL factors are UNKNOWN, final_score = 0.0.

Architectural Boundaries:
    - Confidence Engine is STATELESS. No memory between calls.
    - Confidence Engine does NOT execute trades.
    - Confidence Engine does NOT define trading rules.
    - Confidence Engine does NOT override risk controls.
    - Learning/adaptive scoring is NOT active in v1. (docs/09_CONFIDENCE_ENGINE.md)
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

from core.models.confidence_score import ConfidenceScore, FactorScore
from core.models.trade_candidate import Direction, TradeCandidate
from core.settings.confidence_config import ConfidenceConfig

logger = logging.getLogger("atlax.confidence")

_ENGINE_VERSION = "1.0.0"


class ConfidenceEngine:
    """
    Stateless confidence scoring engine.

    Scores a TradeCandidate by evaluating the CRT quality metadata
    fields against configurable weights. Produces a ConfidenceScore
    with a full factor-by-factor breakdown.

    Authority: docs/09_CONFIDENCE_ENGINE.md

    Args:
        config: Immutable confidence configuration with weights.
    """

    def __init__(self, config: ConfidenceConfig) -> None:
        self._config = config

    def score(self, candidate: TradeCandidate) -> ConfidenceScore:
        """
        Score a TradeCandidate and return a ConfidenceScore.

        Args:
            candidate: The trade candidate to score.

        Returns:
            A ConfidenceScore with final_score, breakdown, and explanation.
        """
        inputs = candidate.confidence_inputs
        direction = candidate.direction
        cfg = self._config

        factor_scores: list[FactorScore] = []

        # ----------------------------------------------------------------
        # Factor 1: Session Alignment (binary)
        # Authority: CRT-SESSION-001, CRT-QUALITY-001
        # ----------------------------------------------------------------
        factor_scores.append(self._score_binary(
            name="session_alignment",
            value=inputs.get("session_alignment"),
            weight=cfg.session_alignment_weight,
            true_explanation="Sweep occurred during a configured kill zone (London/NY).",
            false_explanation="Sweep occurred outside all configured kill zones.",
        ))

        # ----------------------------------------------------------------
        # Factor 2: Sweep Distance (continuous)
        # Ideal range: sweep_distance_ideal_min to sweep_distance_ideal_max pips
        # Authority: CRT-QUALITY-001
        # ----------------------------------------------------------------
        factor_scores.append(self._score_continuous(
            name="sweep_distance",
            value=inputs.get("sweep_distance_pips"),
            weight=cfg.sweep_distance_weight,
            ideal_min=cfg.sweep_distance_ideal_min_pips,
            ideal_max=cfg.sweep_distance_ideal_max_pips,
            unit="pips",
            explanation_template=(
                "Sweep extended {value:.1f} pips beyond the CRT level. "
                "Ideal range: {ideal_min}–{ideal_max} pips."
            ),
        ))

        # ----------------------------------------------------------------
        # Factor 3: Close Location Ratio (continuous, direction-aware)
        # Bullish: high ratio = strong recovery (ideal >= 0.40)
        # Bearish: low ratio = strong recovery (ideal <= 0.60)
        # Authority: CRT-QUALITY-001
        # ----------------------------------------------------------------
        factor_scores.append(self._score_close_location(
            value=inputs.get("close_location_ratio"),
            weight=cfg.close_location_weight,
            direction=direction,
            bull_ideal_min=cfg.close_location_bull_ideal_min,
            bear_ideal_max=cfg.close_location_bear_ideal_max,
        ))

        # ----------------------------------------------------------------
        # Factor 4: Sweep Wick Ratio (continuous)
        # Ideal range: not too small (weak) and not too large (overextension)
        # Authority: CRT-QUALITY-001
        # ----------------------------------------------------------------
        factor_scores.append(self._score_continuous(
            name="sweep_wick_ratio",
            value=inputs.get("sweep_wick_ratio"),
            weight=cfg.sweep_wick_ratio_weight,
            ideal_min=cfg.sweep_wick_ideal_min,
            ideal_max=cfg.sweep_wick_ideal_max,
            unit="ratio",
            explanation_template=(
                "Sweep wick ratio: {value:.3f}. "
                "Ideal range: {ideal_min}–{ideal_max} (moderate grab strength)."
            ),
        ))

        # ----------------------------------------------------------------
        # Factor 5: Parent at HTF Zone (binary)
        # Authority: CRT-PARENT-002, CRT-QUALITY-001
        # ----------------------------------------------------------------
        factor_scores.append(self._score_binary(
            name="htf_zone",
            value=inputs.get("parent_at_htf_zone"),
            weight=cfg.htf_zone_weight,
            true_explanation="Parent candle sits at a significant HTF structural level.",
            false_explanation="Parent candle is not at a notable HTF structural level.",
        ))

        # ----------------------------------------------------------------
        # Factor 6: HTF Trend Alignment (binary)
        # Authority: CRT-QUALITY-001
        # ----------------------------------------------------------------
        factor_scores.append(self._score_binary(
            name="htf_trend_alignment",
            value=inputs.get("htf_trend_alignment"),
            weight=cfg.htf_trend_weight,
            true_explanation="CRT direction aligns with the higher-timeframe trend.",
            false_explanation="CRT direction opposes the higher-timeframe trend.",
        ))

        # ----------------------------------------------------------------
        # Factor 7: FVG Present After Sweep (binary)
        # Authority: CRT-QUALITY-001
        # ----------------------------------------------------------------
        factor_scores.append(self._score_binary(
            name="fvg_present",
            value=inputs.get("fvg_present_after_sweep"),
            weight=cfg.fvg_weight,
            true_explanation="A Fair Value Gap formed in the sweep area — added confluence.",
            false_explanation="No Fair Value Gap detected in the sweep area.",
        ))

        # ----------------------------------------------------------------
        # Factor 8: MSB on LTF (binary)
        # Authority: CRT-QUALITY-001
        # ----------------------------------------------------------------
        factor_scores.append(self._score_binary(
            name="msb_on_ltf",
            value=inputs.get("msb_on_ltf"),
            weight=cfg.msb_ltf_weight,
            true_explanation="Lower-timeframe market structure break confirms the reversal.",
            false_explanation="No lower-timeframe market structure break detected.",
        ))

        # ----------------------------------------------------------------
        # Aggregate: rescale for missing factors
        # ----------------------------------------------------------------
        known = [f for f in factor_scores if not f.is_unknown]
        unknown = [f for f in factor_scores if f.is_unknown]

        raw_score = sum(f.score for f in known)
        known_max = sum(f.max_score for f in known)

        if known_max > 0:
            final_score = round((raw_score / known_max) * 100.0, 2)
        else:
            final_score = 0.0

        missing_names = tuple(f.factor_name for f in unknown)

        # ----------------------------------------------------------------
        # Build explanation
        # ----------------------------------------------------------------
        lines = [
            f"Confidence: {final_score:.1f}/100 ({candidate.symbol} "
            f"{candidate.timeframe} | {candidate.profile} | {candidate.direction}).",
        ]
        if missing_names:
            lines.append(
                f"Missing factors (excluded from score): {', '.join(missing_names)}."
            )
        for f in factor_scores:
            status = "UNKNOWN" if f.is_unknown else f"{f.score:.1f}/{f.max_score:.1f}"
            lines.append(f"  [{status}] {f.factor_name}: {f.explanation}")

        explanation = " ".join(lines)

        confidence_score = ConfidenceScore(
            candidate_id=candidate.candidate_id,
            symbol=candidate.symbol,
            timeframe=candidate.timeframe,
            profile=candidate.profile,
            final_score=final_score,
            raw_score=round(raw_score, 4),
            factor_breakdown=tuple(factor_scores),
            missing_factors=missing_names,
            config_snapshot_id=self._config.snapshot_id,
            explanation=explanation,
            version=_ENGINE_VERSION,
            scored_at=datetime.now(timezone.utc),
        )

        self._log_score(confidence_score)
        return confidence_score

    # ------------------------------------------------------------------
    # Scoring helpers
    # ------------------------------------------------------------------

    def _score_binary(
        self,
        name: str,
        value: Any,
        weight: float,
        true_explanation: str,
        false_explanation: str,
    ) -> FactorScore:
        """Score a binary (True/False/None) factor."""
        if value is None:
            return FactorScore(
                factor_name=name,
                raw_value=None,
                score=0.0,
                max_score=weight,
                is_unknown=True,
                explanation=f"UNKNOWN — data not available.",
            )
        if value is True:
            return FactorScore(
                factor_name=name,
                raw_value=True,
                score=weight,
                max_score=weight,
                is_unknown=False,
                explanation=true_explanation,
            )
        return FactorScore(
            factor_name=name,
            raw_value=False,
            score=0.0,
            max_score=weight,
            is_unknown=False,
            explanation=false_explanation,
        )

    def _score_continuous(
        self,
        name: str,
        value: Any,
        weight: float,
        ideal_min: float,
        ideal_max: float,
        unit: str,
        explanation_template: str,
    ) -> FactorScore:
        """
        Score a continuous numeric factor against an ideal range.

        Inside [ideal_min, ideal_max] → full score.
        Outside → linearly decayed, floored at 0.
        """
        if value is None:
            return FactorScore(
                factor_name=name,
                raw_value=None,
                score=0.0,
                max_score=weight,
                is_unknown=True,
                explanation="UNKNOWN — data not available.",
            )

        v = float(value)
        if ideal_min <= v <= ideal_max:
            ratio = 1.0
        elif v < ideal_min:
            # Linear decay from ideal_min to 0 (min pip would be 0 score below midpoint)
            ratio = max(0.0, v / ideal_min) if ideal_min > 0 else 0.0
        else:
            # Overextension: decay from ideal_max upward (capped at 2x ideal_max = 0)
            overshoot_range = ideal_max  # same distance as ideal range for symmetry
            ratio = max(0.0, 1.0 - (v - ideal_max) / overshoot_range)

        computed_score = round(weight * ratio, 4)
        explanation = explanation_template.format(
            value=v, ideal_min=ideal_min, ideal_max=ideal_max, unit=unit,
        )
        return FactorScore(
            factor_name=name,
            raw_value=v,
            score=computed_score,
            max_score=weight,
            is_unknown=False,
            explanation=explanation,
        )

    def _score_close_location(
        self,
        value: Any,
        weight: float,
        direction: str,
        bull_ideal_min: float,
        bear_ideal_max: float,
    ) -> FactorScore:
        """
        Score close_location_ratio with direction-awareness.

        Bullish: ratio >= bull_ideal_min → full score (closed high in range)
        Bearish: ratio <= bear_ideal_max → full score (closed low in range)
        """
        if value is None:
            return FactorScore(
                factor_name="close_location",
                raw_value=None,
                score=0.0,
                max_score=weight,
                is_unknown=True,
                explanation="UNKNOWN — data not available.",
            )

        v = float(value)

        if direction == Direction.BUY:
            if v >= bull_ideal_min:
                ratio = 1.0
                expl = (
                    f"Sweep closed at {v:.3f} of parent range "
                    f"(>= ideal {bull_ideal_min:.2f}). Strong bullish recovery."
                )
            else:
                ratio = max(0.0, v / bull_ideal_min)
                expl = (
                    f"Sweep closed at {v:.3f} of parent range "
                    f"(below ideal {bull_ideal_min:.2f}). Weak close-back."
                )
        else:  # SELL
            if v <= bear_ideal_max:
                ratio = 1.0
                expl = (
                    f"Sweep closed at {v:.3f} of parent range "
                    f"(<= ideal {bear_ideal_max:.2f}). Strong bearish recovery."
                )
            else:
                ratio = max(0.0, (1.0 - v) / (1.0 - bear_ideal_max)) if bear_ideal_max < 1.0 else 0.0
                expl = (
                    f"Sweep closed at {v:.3f} of parent range "
                    f"(above ideal {bear_ideal_max:.2f}). Weak close-back."
                )

        return FactorScore(
            factor_name="close_location",
            raw_value=v,
            score=round(weight * ratio, 4),
            max_score=weight,
            is_unknown=False,
            explanation=expl,
        )

    def _log_score(self, score: ConfidenceScore) -> None:
        logger.info(
            '{"event":"confidence_score","candidate_id":"%s","symbol":"%s",'
            '"timeframe":"%s","profile":"%s","final_score":%.2f,'
            '"missing_factors":%s,"config_snapshot":"%s"}',
            score.candidate_id, score.symbol, score.timeframe,
            score.profile, score.final_score,
            list(score.missing_factors), score.config_snapshot_id,
        )
