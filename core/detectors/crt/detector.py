"""
ATLAX CRT Detector — Main Detection Engine.

This is the primary CRT pattern detector. It consumes a CandleSequence,
evaluates all approved CRT rules in the documented order, and returns
a DetectorOutput with classification and quality metadata.

Authority Documents:
    - docs/rulebooks/CRT_RULEBOOK.md (all 26 approved CRT rules)
    - docs/07_DETECTOR_SPECIFICATION.md (detector contract)
    - docs/03_ARCHITECTURE.md (layer boundaries)

Detection Flow (from implementation plan Section 6.6):
    1. Validate: sequence has enough closed candles → else UNKNOWN
    2. Validate: parent range >= ATR threshold → else detected=False
    3. Check: both-sides sweep → if True: detected=False (CRT-INVALID-004)
    4. Check: bullish path (sweep.low < parent.low)
       a. Gap check (CRT-SWEEP-INVALID-003)
       b. Close-back check (CRT-INVALID-001)
       c. Confirmation check if required (CRT-BULL-001)
    5. Check: bearish path (sweep.high > parent.high)
       a. Gap check (CRT-SWEEP-INVALID-003)
       b. Close-back check (CRT-INVALID-001)
       c. Confirmation check if required (CRT-BEAR-001)
    6. If neither path: detected=False
    7. Compute quality metadata
    8. Return DetectorOutput

Architectural Boundaries:
    - This detector is STATELESS. Config is immutable and injected
      at construction. No instance state changes between detect() calls.
    - This detector is DETERMINISTIC. Same inputs → same output.
    - This detector NEVER returns BUY, SELL, or any execution instruction.
    - This detector classifies patterns only: bullish_crt, bearish_crt, UNKNOWN.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional

from core.detectors.base import BaseDetector
from core.detectors.crt import rules
from core.detectors.crt import metadata as meta
from core.logging.detection_logger import DetectionEventLogger
from core.models.candle import Candle, CandleSequence
from core.models.detector_output import CRTMetadata, DetectorOutput
from core.settings.crt_config import CRTConfig


class CRTDetector(BaseDetector):
    """
    Candle Range Theory pattern detector.

    Evaluates a sequence of candles for valid bullish or bearish CRT
    patterns per the approved rules in docs/rulebooks/CRT_RULEBOOK.md.

    This detector is stateless and deterministic. Configuration is
    injected at construction and never mutated.

    Authority:
        docs/rulebooks/CRT_RULEBOOK.md — all detection rules
        docs/07_DETECTOR_SPECIFICATION.md — detector contract

    Args:
        config: Immutable CRT configuration schema.
    """

    _DETECTOR_NAME = "CRTDetector"

    def __init__(self, config: CRTConfig) -> None:
        self._config = config
        self._logger = DetectionEventLogger()

    @property
    def name(self) -> str:
        """The unique name of this detector."""
        return self._DETECTOR_NAME

    def detect(
        self,
        sequence: CandleSequence,
        *,
        atr: Optional[Decimal] = None,
        parent_at_htf_zone: Optional[bool] = None,
        htf_trend_alignment: Optional[bool] = None,
        fvg_present_after_sweep: Optional[bool] = None,
        msb_on_ltf: Optional[bool] = None,
    ) -> DetectorOutput:
        """
        Evaluate a candle sequence for a CRT pattern.

        Args:
            sequence: Ordered candle sequence (oldest first).
                      Needs 2 candles (if confirmation disabled) or
                      3 candles (if confirmation enabled).
            atr: Current Average True Range for the instrument.
                 Used by CRT-PARENT-004 for minimum range threshold.
                 If None, the ATR-based check is skipped with a warning.
            parent_at_htf_zone: Optional external input — whether the
                parent candle sits at a significant HTF structural level.
                CRT-PARENT-002, CRT-QUALITY-001.
            htf_trend_alignment: Optional external input — whether
                the CRT direction aligns with HTF trend.
                CRT-QUALITY-001.
            fvg_present_after_sweep: Optional external input — whether
                an FVG formed after the sweep. CRT-QUALITY-001.
            msb_on_ltf: Optional external input — whether MSB was
                confirmed on the lower timeframe. CRT-QUALITY-001.

        Returns:
            DetectorOutput with classification, reason, and metadata.
        """
        try:
            return self._evaluate(
                sequence,
                atr=atr,
                parent_at_htf_zone=parent_at_htf_zone,
                htf_trend_alignment=htf_trend_alignment,
                fvg_present_after_sweep=fvg_present_after_sweep,
                msb_on_ltf=msb_on_ltf,
            )
        except Exception as exc:
            self._logger.log_error(
                self._DETECTOR_NAME,
                sequence.symbol,
                sequence.timeframe,
                exc,
            )
            raise

    # ------------------------------------------------------------------
    # Private implementation
    # ------------------------------------------------------------------

    def _evaluate(
        self,
        sequence: CandleSequence,
        *,
        atr: Optional[Decimal],
        parent_at_htf_zone: Optional[bool],
        htf_trend_alignment: Optional[bool],
        fvg_present_after_sweep: Optional[bool],
        msb_on_ltf: Optional[bool],
    ) -> DetectorOutput:
        """Core detection logic. Separated for clean error handling."""

        required_candles = 2 if not self._config.require_confirmation_candle else 3

        # ---------------------------------------------------------------
        # Step 1: Validate sufficient data (CRT-DATA-002)
        # ---------------------------------------------------------------
        if len(sequence) < required_candles:
            output = self._unknown(
                sequence,
                reason=(
                    f"Insufficient data: need {required_candles} candles, "
                    f"got {len(sequence)}."
                ),
            )
            self._logger.log_detection(output)
            return output

        # Extract candles: last N candles from the sequence
        parent = sequence.candles[-required_candles]
        sweep = sequence.candles[-required_candles + 1]
        confirmation: Optional[Candle] = (
            sequence.candles[-1]
            if self._config.require_confirmation_candle
            else None
        )

        # Check all candles are closed (CRT-DATA-002)
        if not rules.all_candles_closed(parent, sweep, confirmation):
            output = self._unknown(
                sequence,
                reason="One or more candles are not closed (CRT-DATA-002).",
            )
            self._logger.log_detection(output)
            return output

        # ---------------------------------------------------------------
        # Step 2: Parent range threshold (CRT-PARENT-004)
        # ---------------------------------------------------------------
        if atr is not None:
            if not rules.parent_range_meets_minimum(
                parent, atr, self._config.min_parent_range_atr_multiple
            ):
                output = self._not_detected(
                    sequence,
                    sweep,
                    confirmation,
                    reason=(
                        f"Parent range {parent.range} < threshold "
                        f"({self._config.min_parent_range_atr_multiple} * "
                        f"ATR {atr} = {atr * Decimal(str(self._config.min_parent_range_atr_multiple))}). "
                        f"CRT-PARENT-004."
                    ),
                    invalidation_reason="Parent candle range below minimum ATR threshold.",
                )
                self._logger.log_detection(output)
                return output
        else:
            # No ATR provided — skip ATR check but reject zero-range parents
            if parent.range <= Decimal("0"):
                output = self._not_detected(
                    sequence,
                    sweep,
                    confirmation,
                    reason="Parent range is zero (doji). CRT-PARENT-004.",
                    invalidation_reason="Zero-range parent candle.",
                )
                self._logger.log_detection(output)
                return output

        # ---------------------------------------------------------------
        # Step 3: Both-sides sweep check (CRT-INVALID-004)
        # ---------------------------------------------------------------
        if rules.is_both_sides_sweep(parent, sweep):
            output = self._not_detected(
                sequence,
                sweep,
                confirmation,
                reason=(
                    f"Both-sides sweep: sweep.low={sweep.low} < "
                    f"parent.low={parent.low} AND sweep.high={sweep.high} > "
                    f"parent.high={parent.high}. CRT-INVALID-004."
                ),
                invalidation_reason="Both-sides sweep — chaotic price action.",
            )
            self._logger.log_detection(output)
            return output

        # ---------------------------------------------------------------
        # Step 4: Bullish path (CRT-BULL-001)
        # ---------------------------------------------------------------
        if rules.has_strict_bullish_sweep(parent, sweep):
            return self._evaluate_bullish(
                sequence, parent, sweep, confirmation,
                atr=atr,
                parent_at_htf_zone=parent_at_htf_zone,
                htf_trend_alignment=htf_trend_alignment,
                fvg_present_after_sweep=fvg_present_after_sweep,
                msb_on_ltf=msb_on_ltf,
            )

        # ---------------------------------------------------------------
        # Step 5: Bearish path (CRT-BEAR-001)
        # ---------------------------------------------------------------
        if rules.has_strict_bearish_sweep(parent, sweep):
            return self._evaluate_bearish(
                sequence, parent, sweep, confirmation,
                atr=atr,
                parent_at_htf_zone=parent_at_htf_zone,
                htf_trend_alignment=htf_trend_alignment,
                fvg_present_after_sweep=fvg_present_after_sweep,
                msb_on_ltf=msb_on_ltf,
            )

        # ---------------------------------------------------------------
        # Step 6: No sweep detected
        # ---------------------------------------------------------------
        output = self._not_detected(
            sequence,
            sweep,
            confirmation,
            reason=(
                f"No sweep of CRH or CRL. sweep.low={sweep.low} "
                f"(CRL={parent.low}), sweep.high={sweep.high} "
                f"(CRH={parent.high})."
            ),
            invalidation_reason=None,
        )
        self._logger.log_detection(output)
        return output

    def _evaluate_bullish(
        self,
        sequence: CandleSequence,
        parent: Candle,
        sweep: Candle,
        confirmation: Optional[Candle],
        *,
        atr: Optional[Decimal],
        parent_at_htf_zone: Optional[bool],
        htf_trend_alignment: Optional[bool],
        fvg_present_after_sweep: Optional[bool],
        msb_on_ltf: Optional[bool],
    ) -> DetectorOutput:
        """Evaluate bullish CRT conditions after sweep is confirmed."""

        # Step 4a: Gap check (CRT-SWEEP-INVALID-003)
        if not rules.sweep_open_inside_range(
            parent, sweep,
            self._config.max_gap_tolerance_pips,
            direction="bullish",
        ):
            output = self._not_detected(
                sequence, sweep, confirmation,
                reason=(
                    f"Gap candle: sweep.open={sweep.open} is below "
                    f"parent.low={parent.low} beyond tolerance "
                    f"{self._config.max_gap_tolerance_pips}. "
                    f"CRT-SWEEP-INVALID-003."
                ),
                invalidation_reason="Sweep candle opened beyond range with gap.",
            )
            self._logger.log_detection(output)
            return output

        # Step 4b: Close-back check (CRT-INVALID-001)
        if not rules.bullish_close_back(parent, sweep):
            output = self._not_detected(
                sequence, sweep, confirmation,
                reason=(
                    f"Breakout: sweep.close={sweep.close} < "
                    f"parent.low={parent.low}. Body closed outside "
                    f"range. CRT-INVALID-001."
                ),
                invalidation_reason="Sweep candle body closed outside parent range — breakout.",
            )
            self._logger.log_detection(output)
            return output

        # Step 4c: Confirmation check (CRT-BULL-001 condition 3)
        if self._config.require_confirmation_candle:
            if confirmation is None:
                output = self._unknown(
                    sequence,
                    reason="Confirmation candle required but not provided.",
                )
                self._logger.log_detection(output)
                return output

            if not rules.bullish_confirmation(sweep, confirmation):
                output = self._not_detected(
                    sequence, sweep, confirmation,
                    reason=(
                        f"No bullish confirmation: "
                        f"confirmation.close={confirmation.close} <= "
                        f"sweep.high={sweep.high}. CRT-BULL-001."
                    ),
                    invalidation_reason="Confirmation candle did not close above sweep high.",
                )
                self._logger.log_detection(output)
                return output

        # ---- BULLISH CRT DETECTED ----
        return self._build_detected_output(
            sequence, parent, sweep, confirmation,
            direction="bullish",
            parent_at_htf_zone=parent_at_htf_zone,
            htf_trend_alignment=htf_trend_alignment,
            fvg_present_after_sweep=fvg_present_after_sweep,
            msb_on_ltf=msb_on_ltf,
        )

    def _evaluate_bearish(
        self,
        sequence: CandleSequence,
        parent: Candle,
        sweep: Candle,
        confirmation: Optional[Candle],
        *,
        atr: Optional[Decimal],
        parent_at_htf_zone: Optional[bool],
        htf_trend_alignment: Optional[bool],
        fvg_present_after_sweep: Optional[bool],
        msb_on_ltf: Optional[bool],
    ) -> DetectorOutput:
        """Evaluate bearish CRT conditions after sweep is confirmed."""

        # Step 5a: Gap check (CRT-SWEEP-INVALID-003)
        if not rules.sweep_open_inside_range(
            parent, sweep,
            self._config.max_gap_tolerance_pips,
            direction="bearish",
        ):
            output = self._not_detected(
                sequence, sweep, confirmation,
                reason=(
                    f"Gap candle: sweep.open={sweep.open} is above "
                    f"parent.high={parent.high} beyond tolerance "
                    f"{self._config.max_gap_tolerance_pips}. "
                    f"CRT-SWEEP-INVALID-003."
                ),
                invalidation_reason="Sweep candle opened beyond range with gap.",
            )
            self._logger.log_detection(output)
            return output

        # Step 5b: Close-back check (CRT-INVALID-001)
        if not rules.bearish_close_back(parent, sweep):
            output = self._not_detected(
                sequence, sweep, confirmation,
                reason=(
                    f"Breakout: sweep.close={sweep.close} > "
                    f"parent.high={parent.high}. Body closed outside "
                    f"range. CRT-INVALID-001."
                ),
                invalidation_reason="Sweep candle body closed outside parent range — breakout.",
            )
            self._logger.log_detection(output)
            return output

        # Step 5c: Confirmation check (CRT-BEAR-001 condition 3)
        if self._config.require_confirmation_candle:
            if confirmation is None:
                output = self._unknown(
                    sequence,
                    reason="Confirmation candle required but not provided.",
                )
                self._logger.log_detection(output)
                return output

            if not rules.bearish_confirmation(sweep, confirmation):
                output = self._not_detected(
                    sequence, sweep, confirmation,
                    reason=(
                        f"No bearish confirmation: "
                        f"confirmation.close={confirmation.close} >= "
                        f"sweep.low={sweep.low}. CRT-BEAR-001."
                    ),
                    invalidation_reason="Confirmation candle did not close below sweep low.",
                )
                self._logger.log_detection(output)
                return output

        # ---- BEARISH CRT DETECTED ----
        return self._build_detected_output(
            sequence, parent, sweep, confirmation,
            direction="bearish",
            parent_at_htf_zone=parent_at_htf_zone,
            htf_trend_alignment=htf_trend_alignment,
            fvg_present_after_sweep=fvg_present_after_sweep,
            msb_on_ltf=msb_on_ltf,
        )

    # ------------------------------------------------------------------
    # Output builders
    # ------------------------------------------------------------------

    def _build_detected_output(
        self,
        sequence: CandleSequence,
        parent: Candle,
        sweep: Candle,
        confirmation: Optional[Candle],
        *,
        direction: str,
        parent_at_htf_zone: Optional[bool],
        htf_trend_alignment: Optional[bool],
        fvg_present_after_sweep: Optional[bool],
        msb_on_ltf: Optional[bool],
    ) -> DetectorOutput:
        """Build a successful detection output with full metadata."""

        classification = f"{direction}_crt"

        # Compute quality metadata (CRT-QUALITY-001)
        sweep_distance = meta.compute_sweep_distance_pips(
            parent, sweep, self._config.pip_size, direction
        )
        close_ratio = meta.compute_close_location_ratio(parent, sweep)
        wick_ratio = meta.compute_sweep_wick_ratio(parent, sweep, direction)
        session = meta.compute_session_alignment(
            sweep.open_time, self._config.kill_zones
        )

        # Determine targets
        if direction == "bullish":
            swept_level = "CRL"
            opposite_extreme = parent.high
        else:
            swept_level = "CRH"
            opposite_extreme = parent.low

        crt_metadata = CRTMetadata(
            parent_candle=parent,
            sweep_candle=sweep,
            confirmation_candle=confirmation,
            swept_level=swept_level,
            crt_high=parent.high,
            crt_low=parent.low,
            midpoint_target=parent.midpoint,
            opposite_extreme_target=opposite_extreme,
            sweep_distance_pips=sweep_distance,
            close_location_ratio=close_ratio,
            sweep_wick_ratio=wick_ratio,
            parent_at_htf_zone=parent_at_htf_zone,
            session_alignment=session,
            htf_trend_alignment=htf_trend_alignment,
            fvg_present_after_sweep=fvg_present_after_sweep,
            msb_on_ltf=msb_on_ltf,
        )

        # Build reason string with exact price comparisons for auditability
        conf_str = ""
        if confirmation is not None:
            if direction == "bullish":
                conf_str = (
                    f"; confirmation.close={confirmation.close} > "
                    f"sweep.high={sweep.high}"
                )
            else:
                conf_str = (
                    f"; confirmation.close={confirmation.close} < "
                    f"sweep.low={sweep.low}"
                )

        if direction == "bullish":
            reason = (
                f"Bullish CRT: sweep.low={sweep.low} < "
                f"parent.low={parent.low}; sweep.close={sweep.close} >= "
                f"parent.low={parent.low}{conf_str}"
            )
        else:
            reason = (
                f"Bearish CRT: sweep.high={sweep.high} > "
                f"parent.high={parent.high}; sweep.close={sweep.close} <= "
                f"parent.high={parent.high}{conf_str}"
            )

        # Determine timestamp: use confirmation close if available, else sweep close
        detection_time = (
            confirmation.close_time if confirmation is not None
            else sweep.close_time
        )

        output = DetectorOutput(
            detector=self._DETECTOR_NAME,
            symbol=sequence.symbol,
            timeframe=sequence.timeframe,
            detected=True,
            timestamp=detection_time,
            classification=classification,
            reason=reason,
            invalidation_reason=None,
            metadata=crt_metadata,
        )
        self._logger.log_detection(output)
        return output

    def _not_detected(
        self,
        sequence: CandleSequence,
        sweep: Candle,
        confirmation: Optional[Candle],
        reason: str,
        invalidation_reason: Optional[str],
    ) -> DetectorOutput:
        """Build a non-detection output."""
        detection_time = (
            confirmation.close_time if confirmation is not None
            else sweep.close_time
        )
        return DetectorOutput(
            detector=self._DETECTOR_NAME,
            symbol=sequence.symbol,
            timeframe=sequence.timeframe,
            detected=False,
            timestamp=detection_time,
            classification="UNKNOWN",
            reason=reason,
            invalidation_reason=invalidation_reason,
            metadata=None,
        )

    def _unknown(
        self,
        sequence: CandleSequence,
        reason: str,
    ) -> DetectorOutput:
        """Build an UNKNOWN output for insufficient data."""
        return DetectorOutput(
            detector=self._DETECTOR_NAME,
            symbol=sequence.symbol,
            timeframe=sequence.timeframe,
            detected=False,
            timestamp=datetime.now(timezone.utc),
            classification="UNKNOWN",
            reason=reason,
            invalidation_reason=None,
            metadata=None,
        )
