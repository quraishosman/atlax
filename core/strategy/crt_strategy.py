"""
ATLAX CRT Strategy Engine.

Consumes CRTDetector output and produces TradeCandidate, NoTrade, or UNKNOWN.
One StrategyDecision is produced per profile per detection event.

Authority Documents:
    - docs/08_STRATEGY_ENGINE.md (strategy responsibilities and output contract)
    - docs/05_API_SPECIFICATION.md (Trade Candidate Contract field requirements)
    - docs/03_ARCHITECTURE.md (profile model, layer boundaries)
    - docs/rulebooks/CRT_RULEBOOK.md (CRT-QUALITY-001 geometry for entry/SL/TP)

Direction Authorization:
    The CRT Rulebook classifies patterns as bullish_crt or bearish_crt.
    docs/08_STRATEGY_ENGINE.md permits direction "only when authorized by
    the rulebook and strategy spec." CRT classification directly implies
    direction:
        bullish_crt → BUY  (price reversal expected upward after sweep)
        bearish_crt → SELL (price reversal expected downward after sweep)

Entry / Stop / Target Geometry (from CRT-QUALITY-001):
    Bullish:
        Entry zone: [parent.low (CRL), sweep.close]
        Invalidation: sweep.low (below the liquidity grab)
        Target 1:    parent.midpoint (Mean Threshold, 50% of range)
        Target 2:    parent.high (CRH, opposite extreme)

    Bearish:
        Entry zone: [sweep.close, parent.high (CRH)]
        Invalidation: sweep.high (above the liquidity grab)
        Target 1:    parent.midpoint (Mean Threshold)
        Target 2:    parent.low (CRL, opposite extreme)

Architectural Boundaries:
    - This strategy is STATELESS. No memory of previous detections.
    - This strategy NEVER produces lot_size, risk_percentage, or order type.
    - This strategy NEVER calls MT5, TradingView, or any broker.
    - This strategy returns UNKNOWN when required data is missing.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional

from core.models.detector_output import CRTMetadata, DetectorOutput
from core.models.trade_candidate import (
    Direction,
    EntryModel,
    InvalidationModel,
    StrategyDecision,
    StrategyOutcome,
    TargetModel,
    TradeCandidate,
)
from core.settings.strategy_config import ProfileConfig, StrategyConfig
from core.strategy.base import BaseStrategy

logger = logging.getLogger("atlax.strategy")

# Direction mapping — authorized by CRT classification + docs/08_STRATEGY_ENGINE.md
_CRT_DIRECTION_MAP: dict[str, str] = {
    "bullish_crt": Direction.BUY,
    "bearish_crt": Direction.SELL,
}


class CRTStrategy(BaseStrategy):
    """
    CRT-specific strategy that maps detector output to trade candidates.

    Evaluation Steps:
        1. Gate: detector_output.detected must be True → else no_trade
        2. Gate: classification must be a known CRT type → else UNKNOWN
        3. Gate: metadata must be present → else UNKNOWN
        4. Profile gate: timeframe must be in profile's allowed_timeframes
        5. Optional: session alignment check (if profile requires it)
        6. Build entry/SL/TP models from CRT geometry
        7. Map direction from classification
        8. Build and return TradeCandidate

    Authority: docs/08_STRATEGY_ENGINE.md
    """

    _STRATEGY_NAME = "CRTStrategy"

    @property
    def name(self) -> str:
        return self._STRATEGY_NAME

    def evaluate(
        self,
        detector_output: DetectorOutput,
        profile: ProfileConfig,
        config: StrategyConfig,
    ) -> StrategyDecision:
        """
        Evaluate a CRT detector output for a specific trader profile.

        Args:
            detector_output: Output from CRTDetector.detect().
            profile: The trader profile being evaluated.
            config: Active strategy configuration.

        Returns:
            StrategyDecision with trade_candidate | no_trade | UNKNOWN.
        """
        now = datetime.now(timezone.utc)
        symbol = detector_output.symbol
        timeframe = detector_output.timeframe

        # ------------------------------------------------------------------
        # Gate 1: Must be a detection
        # ------------------------------------------------------------------
        if not detector_output.detected:
            return self._no_trade(
                symbol, timeframe, now,
                reason=f"No pattern detected: {detector_output.reason}",
            )

        # ------------------------------------------------------------------
        # Gate 2: Must be a known CRT classification
        # ------------------------------------------------------------------
        direction = _CRT_DIRECTION_MAP.get(detector_output.classification)
        if direction is None:
            return self._unknown(
                symbol, timeframe, now,
                reason=(
                    f"Unknown classification: {detector_output.classification!r}. "
                    f"Cannot determine direction."
                ),
            )

        # ------------------------------------------------------------------
        # Gate 3: Metadata must be present
        # ------------------------------------------------------------------
        metadata = detector_output.metadata
        if metadata is None:
            return self._unknown(
                symbol, timeframe, now,
                reason="Detector output missing CRT metadata. Cannot build candidate.",
            )

        # ------------------------------------------------------------------
        # Gate 4: Timeframe must be allowed for this profile
        # ------------------------------------------------------------------
        if timeframe not in profile.allowed_timeframes:
            return self._no_trade(
                symbol, timeframe, now,
                reason=(
                    f"Timeframe {timeframe} not allowed for profile "
                    f"'{profile.name}'. Allowed: {profile.allowed_timeframes}."
                ),
            )

        # ------------------------------------------------------------------
        # Gate 5: Session alignment check (profile-configurable)
        # ------------------------------------------------------------------
        if profile.require_session_alignment:
            if metadata.session_alignment is False:
                return self._no_trade(
                    symbol, timeframe, now,
                    reason=(
                        f"Profile '{profile.name}' requires session alignment, "
                        f"but sweep occurred outside all kill zones."
                    ),
                )
            if metadata.session_alignment is None:
                return self._unknown(
                    symbol, timeframe, now,
                    reason=(
                        f"Profile '{profile.name}' requires session alignment "
                        f"but session_alignment is UNKNOWN."
                    ),
                )

        # ------------------------------------------------------------------
        # Gate 6: HTF trend alignment check (profile-configurable)
        # ------------------------------------------------------------------
        if profile.require_htf_trend_alignment:
            if metadata.htf_trend_alignment is False:
                return self._no_trade(
                    symbol, timeframe, now,
                    reason=(
                        f"Profile '{profile.name}' requires HTF trend alignment, "
                        f"but CRT direction opposes the higher-timeframe trend."
                    ),
                )
            if metadata.htf_trend_alignment is None:
                return self._unknown(
                    symbol, timeframe, now,
                    reason=(
                        f"Profile '{profile.name}' requires HTF trend alignment "
                        f"but htf_trend_alignment is UNKNOWN."
                    ),
                )

        # ------------------------------------------------------------------
        # Build geometry models from CRT metadata
        # ------------------------------------------------------------------
        entry_model, invalidation_model, target_model = self._build_geometry(
            metadata, direction
        )

        # ------------------------------------------------------------------
        # Build confidence inputs dict (raw metadata for Confidence Engine)
        # ------------------------------------------------------------------
        confidence_inputs: dict = {
            "classification": detector_output.classification,
            "swept_level": metadata.swept_level,
            "sweep_distance_pips": metadata.sweep_distance_pips,
            "close_location_ratio": metadata.close_location_ratio,
            "sweep_wick_ratio": metadata.sweep_wick_ratio,
            "session_alignment": metadata.session_alignment,
            "parent_at_htf_zone": metadata.parent_at_htf_zone,
            "htf_trend_alignment": metadata.htf_trend_alignment,
            "fvg_present_after_sweep": metadata.fvg_present_after_sweep,
            "msb_on_ltf": metadata.msb_on_ltf,
        }

        # ------------------------------------------------------------------
        # Build explanation
        # ------------------------------------------------------------------
        explanation = (
            f"{detector_output.classification.replace('_', ' ').title()} detected "
            f"on {symbol} {timeframe}. "
            f"Profile: {profile.name}. Direction: {direction}. "
            f"Swept: {metadata.swept_level} at {metadata.crt_low if direction == Direction.BUY else metadata.crt_high}. "
            f"Session aligned: {metadata.session_alignment}. "
            f"Targets: midpoint={metadata.midpoint_target}, "
            f"opposite={metadata.opposite_extreme_target}."
        )

        # ------------------------------------------------------------------
        # Build TradeCandidate
        # ------------------------------------------------------------------
        candidate = TradeCandidate(
            candidate_id=str(uuid.uuid4()),
            strategy_name=self._STRATEGY_NAME,
            source_detector_event_ids=(
                f"{detector_output.detector}:{detector_output.symbol}:"
                f"{detector_output.timeframe}:{detector_output.timestamp.isoformat()}",
            ),
            symbol=symbol,
            timeframe=timeframe,
            profile=profile.name,
            direction=direction,
            entry_model=entry_model,
            invalidation_model=invalidation_model,
            target_model=target_model,
            confidence_inputs=confidence_inputs,
            explanation=explanation,
            created_at=now,
        )

        self._log_candidate(candidate)

        return StrategyDecision(
            outcome=StrategyOutcome.TRADE_CANDIDATE,
            candidate=candidate,
            reason=explanation,
            symbol=symbol,
            timeframe=timeframe,
            timestamp=now,
        )

    # ------------------------------------------------------------------
    # Geometry builders
    # ------------------------------------------------------------------

    def _build_geometry(
        self,
        metadata: CRTMetadata,
        direction: str,
    ) -> tuple[EntryModel, InvalidationModel, TargetModel]:
        """Build entry, stop, and target models from CRT metadata geometry."""

        sweep = metadata.sweep_candle
        parent = metadata.parent_candle

        if direction == Direction.BUY:
            entry = EntryModel(
                entry_zone_high=sweep.close,
                entry_zone_low=metadata.crt_low,
                reference_price=sweep.close,
                description=(
                    f"Bullish CRT entry zone: [{metadata.crt_low}, {sweep.close}]. "
                    f"Price swept below CRL and closed back inside range."
                ),
            )
            invalidation = InvalidationModel(
                invalidation_level=sweep.low,
                description=(
                    f"Bullish CRT invalidation: below sweep low {sweep.low}. "
                    f"If price revisits the sweep low, the liquidity grab failed."
                ),
            )
        else:  # SELL
            entry = EntryModel(
                entry_zone_high=metadata.crt_high,
                entry_zone_low=sweep.close,
                reference_price=sweep.close,
                description=(
                    f"Bearish CRT entry zone: [{sweep.close}, {metadata.crt_high}]. "
                    f"Price swept above CRH and closed back inside range."
                ),
            )
            invalidation = InvalidationModel(
                invalidation_level=sweep.high,
                description=(
                    f"Bearish CRT invalidation: above sweep high {sweep.high}. "
                    f"If price revisits the sweep high, the liquidity grab failed."
                ),
            )

        target = TargetModel(
            midpoint_target=metadata.midpoint_target,
            opposite_extreme_target=metadata.opposite_extreme_target,
            description=(
                f"CRT targets: T1={metadata.midpoint_target} (Mean Threshold / 50%), "
                f"T2={metadata.opposite_extreme_target} (opposite extreme)."
            ),
        )

        return entry, invalidation, target

    # ------------------------------------------------------------------
    # Output helpers
    # ------------------------------------------------------------------

    def _no_trade(
        self, symbol: str, timeframe: str, timestamp: datetime, reason: str
    ) -> StrategyDecision:
        decision = StrategyDecision(
            outcome=StrategyOutcome.NO_TRADE,
            candidate=None,
            reason=reason,
            symbol=symbol,
            timeframe=timeframe,
            timestamp=timestamp,
        )
        logger.debug('{"event":"strategy_no_trade","symbol":"%s","timeframe":"%s","reason":"%s"}',
                     symbol, timeframe, reason)
        return decision

    def _unknown(
        self, symbol: str, timeframe: str, timestamp: datetime, reason: str
    ) -> StrategyDecision:
        decision = StrategyDecision(
            outcome=StrategyOutcome.UNKNOWN,
            candidate=None,
            reason=reason,
            symbol=symbol,
            timeframe=timeframe,
            timestamp=timestamp,
        )
        logger.warning('{"event":"strategy_unknown","symbol":"%s","timeframe":"%s","reason":"%s"}',
                       symbol, timeframe, reason)
        return decision

    def _log_candidate(self, candidate: TradeCandidate) -> None:
        logger.info(
            '{"event":"trade_candidate","candidate_id":"%s","symbol":"%s",'
            '"timeframe":"%s","profile":"%s","direction":"%s","strategy":"%s"}',
            candidate.candidate_id, candidate.symbol, candidate.timeframe,
            candidate.profile, candidate.direction, candidate.strategy_name,
        )
