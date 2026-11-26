"""
ATLAX Alert Event Data Model.

The structured alert payload produced by the Alert Engine and
delivered to configured alert channels. Immutable and fully typed.

Authority Documents:
    - docs/03_ARCHITECTURE.md (Alert Engine responsibilities)
    - docs/04_SYSTEM_DESIGN.md (Alert Engine layer: sends alerts only)
    - docs/05_API_SPECIFICATION.md (message envelope fields)

Architectural Boundaries:
    - AlertEvent must NEVER contain execution instructions.
    - AlertEvent must NEVER trigger trades.
    - AlertEvent is a notification payload only.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Optional

from core.confidence.atr_quality import ATRQualityResult
from core.detectors.crt.quality_tightening import CRTQualityTighteningResult
from core.models.volume_profile_result import VolumeProfileResult


@dataclass(frozen=True)
class AlertEvent:
    """
    A fully-formed alert notification derived from a scored TradeCandidate.

    Contains everything a trader needs to evaluate and act on a setup.
    Delivered to configured channels (log, Telegram, etc.) by AlertEngine.

    Authority: docs/03_ARCHITECTURE.md (profile-aware alert content)

    Attributes:
        alert_id: Unique identifier for this alert instance.
        candidate_id: ID of the originating TradeCandidate.
        symbol: Trading instrument (e.g., "EURUSD").
        timeframe: Candle timeframe (e.g., "H4").
        profile: Trader profile that generated this candidate.
        direction: "BUY" or "SELL".
        classification: CRT pattern type ("bullish_crt" / "bearish_crt").
        confidence_score: Final confidence score (0.0–100.0).
        confidence_breakdown: Dict of factor_name → score string for display.
        missing_factors: Factors that were UNKNOWN during scoring.
        crt_high: The CRH price level of the parent candle.
        crt_low: The CRL price level of the parent candle.
        entry_zone_high: Upper bound of the entry zone.
        entry_zone_low: Lower bound of the entry zone.
        invalidation_level: Stop-loss reference price.
        midpoint_target: Take-profit Target 1 (Mean Threshold).
        opposite_extreme_target: Take-profit Target 2 (opposite extreme).
        explanation: Human-readable summary of the setup.
        created_at: UTC timestamp when this alert was produced.
    """

    alert_id: str
    candidate_id: str
    symbol: str
    timeframe: str
    profile: str
    direction: str
    classification: str
    confidence_score: float
    confidence_breakdown: dict[str, str]
    missing_factors: tuple[str, ...]
    crt_high: Decimal
    crt_low: Decimal
    entry_zone_high: Decimal
    entry_zone_low: Decimal
    invalidation_level: Decimal
    midpoint_target: Decimal
    opposite_extreme_target: Decimal
    explanation: str
    created_at: datetime
    atr_quality: Optional[ATRQualityResult] = None
    parent_at_htf_zone: Optional[bool] = None
    key_level_evaluation: Optional[dict] = None
    quality_tightening: Optional[CRTQualityTighteningResult] = None
    volume_profile: Optional[VolumeProfileResult] = None
    volume_profiles: tuple[VolumeProfileResult, ...] = ()
    volume_profile_confluence: Optional[bool] = None
    volume_profile_matches: tuple[dict, ...] = ()

    def __str__(self) -> str:
        return (
            f"AlertEvent({self.alert_id[:8]}... | "
            f"{self.symbol} {self.timeframe} | {self.profile} | "
            f"{self.direction} | score={self.confidence_score:.1f})"
        )

    def to_display_dict(self) -> dict:
        """Return a display-friendly dict for formatters."""
        display = {
            "alert_id": self.alert_id,
            "candidate_id": self.candidate_id,
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "profile": self.profile,
            "direction": self.direction,
            "classification": self.classification,
            "confidence_score": round(self.confidence_score, 1),
            "crt_high": str(self.crt_high),
            "crt_low": str(self.crt_low),
            "entry_zone_high": str(self.entry_zone_high),
            "entry_zone_low": str(self.entry_zone_low),
            "invalidation_level": str(self.invalidation_level),
            "midpoint_target": str(self.midpoint_target),
            "opposite_extreme_target": str(self.opposite_extreme_target),
            "missing_factors": list(self.missing_factors),
            "explanation": self.explanation,
            "created_at": self.created_at.isoformat(),
            "parent_at_htf_zone": self.parent_at_htf_zone,
            "key_level_evaluation": self.key_level_evaluation,
        }
        profiles = self.volume_profiles or (
            (self.volume_profile,) if self.volume_profile is not None else ()
        )
        if profiles:
            display["volume_profiles"] = [profile.as_log_dict() for profile in profiles]
        if self.volume_profile is not None:
            display["volume_profile"] = self.volume_profile.as_log_dict()
        display["volume_profile_confluence"] = self.volume_profile_confluence
        display["volume_profile_matches"] = list(self.volume_profile_matches)
        if self.atr_quality is not None:
            display["atr_quality"] = {
                "status": self.atr_quality.status,
                "grade": self.atr_quality.grade,
                "atr": str(self.atr_quality.atr) if self.atr_quality.atr is not None else None,
                "atr_period": self.atr_quality.atr_period,
                "method": self.atr_quality.method,
                "parent_range": str(self.atr_quality.parent_range) if self.atr_quality.parent_range is not None else None,
                "parent_atr_ratio": str(self.atr_quality.parent_atr_ratio) if self.atr_quality.parent_atr_ratio is not None else None,
                "sweep_depth": str(self.atr_quality.sweep_depth) if self.atr_quality.sweep_depth is not None else None,
                "sweep_atr_ratio": str(self.atr_quality.sweep_atr_ratio) if self.atr_quality.sweep_atr_ratio is not None else None,
                "configuration_snapshot_id": self.atr_quality.configuration_snapshot_id,
                "explanation": self.atr_quality.explanation,
            }
        if self.quality_tightening is not None:
            display["quality_tightening"] = {
                "status": self.quality_tightening.status,
                "would_suppress": self.quality_tightening.would_suppress,
                "reasons": list(self.quality_tightening.reasons),
                "version": self.quality_tightening.version,
            }
        return display
