"""
ATLAX Detector Output Data Models.

Defines the immutable DetectorOutput and CRTMetadata dataclasses that
form the contract between the Detector Layer and the Strategy Engine.

Authority Documents:
    - docs/05_API_SPECIFICATION.md (detector result contract)
    - docs/06_DATA_MODELS.md (detector result required fields)
    - docs/07_DETECTOR_SPECIFICATION.md (output structure and boundaries)
    - docs/rulebooks/CRT_RULEBOOK.md, CRT-QUALITY-001 (quality metadata fields)
    - docs/rulebooks/CRT_RULEBOOK.md, CRT-STRAT-001 (detector output boundary)

Architectural Boundaries (CRT-STRAT-001):
    - DetectorOutput must NEVER contain: BUY, SELL, EXECUTE, lot_size,
      risk_percentage, entry_price, stop_loss_price, take_profit_price.
    - The detector classifies patterns only. Trade decisions belong to
      the Strategy Engine downstream.

Design Decisions:
    - frozen=True: immutable output. Once created, never modified.
    - Optional quality metadata fields: external systems (HTF zone detector,
      FVG detector, etc.) may not always be available. None means "not computed."
    - CRTMetadata is CRT-specific. Future detectors (e.g., LiquidityDetector)
      will define their own metadata dataclass.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Optional

from core.models.candle import Candle


@dataclass(frozen=True)
class CRTMetadata:
    """
    CRT-specific quality metadata passed to the Confidence Engine.

    These fields describe the quality of a detected CRT pattern.
    They are quality inputs for scoring, NOT binary detection gates.
    Detection is never blocked solely because a quality field is
    unfavourable (CRT-QUALITY-001).

    Authority:
        CRT-QUALITY-001: Defines the quality fields and their types.
        CRT-PARENT-001: CRH/CRL assignment.
        CRT-SESSION-001: Session alignment check.

    Attributes:
        parent_candle: The reference candle establishing CRH and CRL.
        sweep_candle: The candle that raids CRH or CRL.
        confirmation_candle: The candle confirming the pattern (MSS).
                             May be None if require_confirmation_candle is False.
        swept_level: Which level was swept — "CRL" (bullish) or "CRH" (bearish).
        crt_high: The CRH value (parent.high). CRT-PARENT-001.
        crt_low: The CRL value (parent.low). CRT-PARENT-001.
        midpoint_target: 50% midpoint of the parent range (Mean Threshold).
        opposite_extreme_target: The opposite boundary of the parent range
                                  (CRH for bullish, CRL for bearish).
        sweep_distance_pips: How far price extended beyond CRH or CRL, in pips.
                             CRT-QUALITY-001.
        close_location_ratio: Where the sweep candle closed within the parent
                              range. 0.0 = at CRL, 1.0 = at CRH.
                              CRT-QUALITY-001.
        sweep_wick_ratio: Ratio of the sweep wick length (beyond CRH/CRL)
                          to the parent range. CRT-QUALITY-001.
        parent_at_htf_zone: Whether the parent candle sits at a significant
                            higher-timeframe structural level. CRT-PARENT-002.
                            None if not computed. CRT-QUALITY-001.
        session_alignment: Whether the sweep occurred during a configured
                           kill zone (London Open, New York AM).
                           CRT-SESSION-001, CRT-QUALITY-001.
        htf_trend_alignment: Whether the CRT direction aligns with the
                             higher-timeframe trend. CRT-QUALITY-001.
                             None if not computed.
        fvg_present_after_sweep: Whether a Fair Value Gap formed in the
                                 sweep candle area. CRT-QUALITY-001.
                                 None if not computed.
        msb_on_ltf: Whether a Market Structure Break was confirmed on the
                    lower execution timeframe. CRT-QUALITY-001.
                    None if not computed.
    """

    parent_candle: Candle
    sweep_candle: Candle
    confirmation_candle: Optional[Candle]
    swept_level: str
    crt_high: Decimal
    crt_low: Decimal
    midpoint_target: Decimal
    opposite_extreme_target: Decimal
    sweep_distance_pips: Optional[float]
    close_location_ratio: Optional[float]
    sweep_wick_ratio: Optional[float]
    parent_at_htf_zone: Optional[bool]
    session_alignment: Optional[bool]
    htf_trend_alignment: Optional[bool]
    fvg_present_after_sweep: Optional[bool]
    msb_on_ltf: Optional[bool]

    def __post_init__(self) -> None:
        """Validate metadata invariants on construction."""
        valid_swept_levels = ("CRL", "CRH")
        if self.swept_level not in valid_swept_levels:
            raise ValueError(
                f"swept_level must be one of {valid_swept_levels}, "
                f"got {self.swept_level!r}"
            )
        if (
            self.close_location_ratio is not None
            and not (0.0 <= self.close_location_ratio <= 1.0)
        ):
            raise ValueError(
                f"close_location_ratio must be between 0.0 and 1.0, "
                f"got {self.close_location_ratio}"
            )


@dataclass(frozen=True)
class DetectorOutput:
    """
    The standard output contract for all ATLAX detectors.

    Every detector returns this structure. The Strategy Engine, Confidence
    Engine, and Alert Engine all consume DetectorOutput instances.

    Authority:
        docs/05_API_SPECIFICATION.md: Defines the detector result contract.
        docs/06_DATA_MODELS.md: Required fields for detector results.
        docs/07_DETECTOR_SPECIFICATION.md: Output structure and forbidden fields.
        CRT-STRAT-001: Detector must NEVER return BUY/SELL/EXECUTE.

    Forbidden Fields (CRT-STRAT-001, docs/07_DETECTOR_SPECIFICATION.md):
        This dataclass deliberately excludes: direction (BUY/SELL),
        entry_price, stop_loss_price, take_profit_price, lot_size,
        risk_percentage, or any execution instruction.

    Attributes:
        detector: Name of the detector that produced this output
                  (e.g., "CRTDetector").
        symbol: The trading instrument evaluated.
        timeframe: The candle timeframe evaluated.
        detected: True if a valid pattern was found.
        timestamp: UTC timestamp of detection (close time of the
                   triggering candle — confirmation candle if used,
                   otherwise sweep candle).
        classification: Pattern classification string.
                        "bullish_crt" | "bearish_crt" | "UNKNOWN".
                        Never "BUY" or "SELL".
        reason: Human-readable explanation of why the detection
                succeeded or failed. Includes the specific price
                comparisons that were evaluated.
        invalidation_reason: If detected is False, explains why
                             the pattern was invalidated. None if
                             detected is True or if no pattern was
                             attempted.
        metadata: Detector-specific quality metadata. CRTMetadata for
                  the CRT detector. None if detected is False or
                  if classification is UNKNOWN.
    """

    detector: str
    symbol: str
    timeframe: str
    detected: bool
    timestamp: datetime
    classification: str
    reason: str
    invalidation_reason: Optional[str]
    metadata: Optional[CRTMetadata]

    # Valid classification values for the CRT detector.
    _VALID_CRT_CLASSIFICATIONS = frozenset({"bullish_crt", "bearish_crt", "UNKNOWN"})

    # Forbidden classification values per CRT-STRAT-001 and
    # docs/07_DETECTOR_SPECIFICATION.md. Detectors must NEVER return these.
    _FORBIDDEN_CLASSIFICATIONS = frozenset({
        "BUY", "SELL", "EXECUTE", "LONG", "SHORT",
        "buy", "sell", "execute", "long", "short",
    })

    def __post_init__(self) -> None:
        """Validate output contract invariants on construction."""
        if self.classification in self._FORBIDDEN_CLASSIFICATIONS:
            raise ValueError(
                f"DetectorOutput.classification must NEVER be a trade "
                f"instruction. Got {self.classification!r}. "
                f"Authority: CRT-STRAT-001, docs/07_DETECTOR_SPECIFICATION.md"
            )

    def __str__(self) -> str:
        status = "DETECTED" if self.detected else "NOT DETECTED"
        return (
            f"DetectorOutput({self.detector} | {self.symbol} {self.timeframe} "
            f"| {status} | {self.classification} | {self.reason})"
        )

    def __repr__(self) -> str:
        return (
            f"DetectorOutput(detector={self.detector!r}, "
            f"symbol={self.symbol!r}, timeframe={self.timeframe!r}, "
            f"detected={self.detected!r}, timestamp={self.timestamp!r}, "
            f"classification={self.classification!r}, "
            f"reason={self.reason!r}, "
            f"invalidation_reason={self.invalidation_reason!r}, "
            f"metadata={self.metadata!r})"
        )
