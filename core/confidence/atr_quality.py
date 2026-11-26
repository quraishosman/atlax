"""ATR quality classification for already-detected CRT events.

This module is intentionally downstream of the CRT detector. It classifies
quality for reporting and subscriber routing; it never validates CRTs or
creates an execution decision.

Authority:
    - docs/rulebooks/ATR_QUALITY_OVERLAY.md
    - docs/25_ATR_QUALITY_OVERLAY_PLAN.md
    - docs/decisions/ADR-010.md
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from decimal import Decimal
from typing import Optional

from core.models.detector_output import DetectorOutput


_VALID_GRADES = frozenset({"A+", "A", "B", "C"})


@dataclass(frozen=True)
class ATRPlanEntitlement:
    """Configured ATR entitlement for one subscription plan."""

    plan_name: str
    atr_quality_filter: bool
    minimum_grade: Optional[str]

    def __post_init__(self) -> None:
        if not self.plan_name.strip():
            raise ValueError("ATR plan name must not be empty.")
        if self.minimum_grade is not None and self.minimum_grade not in _VALID_GRADES:
            raise ValueError(
                "ATR plan minimum_grade must be one of 'A+', 'A', 'B', or 'C'."
            )
        if self.atr_quality_filter and self.minimum_grade is None:
            raise ValueError(
                "A plan with atr_quality_filter enabled must define minimum_grade."
            )


@dataclass(frozen=True)
class ATRQualityConfig:
    """Validated thresholds for the proposed ATR quality matrix.

    Thresholds are required constructor inputs so the Settings Engine remains
    the source of truth. No trading or routing default is hidden here.
    """

    enabled: bool
    version: str
    method: str
    atr_period: int
    parent_high_min: Decimal
    parent_a_min: Decimal
    parent_b_min: Decimal
    sweep_high_min: Decimal
    sweep_a_min: Decimal
    sweep_b_min: Decimal
    minimum_premium_grade: str
    missing_data_policy: str
    advanced_regime_filter_enabled: bool
    max_parent_atr_ratio: Optional[Decimal]
    max_sweep_atr_ratio: Optional[Decimal]
    volatility_regime_min_ratio: Optional[Decimal]
    volatility_regime_max_ratio: Optional[Decimal]
    default_plan: str
    plans: tuple[ATRPlanEntitlement, ...]
    shadow_mode: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.shadow_mode, bool):
            raise ValueError("shadow_mode must be a boolean.")
        if self.shadow_mode and not self.enabled:
            raise ValueError("shadow_mode requires enabled=true.")
        if self.method != "SIMPLE_TR_MEAN":
            raise ValueError(
                "Unsupported ATR quality method. Only SIMPLE_TR_MEAN is "
                "implemented in this phase."
            )
        if not self.version.strip():
            raise ValueError("ATR quality config version must not be empty.")
        if not self.method.strip():
            raise ValueError("ATR quality method must not be empty.")
        if self.atr_period < 1:
            raise ValueError("ATR quality period must be at least 1.")

        if not (
            self.parent_high_min > self.parent_a_min > self.parent_b_min > Decimal("0")
        ):
            raise ValueError(
                "Parent ATR thresholds must be strictly descending and positive."
            )
        if not (
            self.sweep_high_min > self.sweep_a_min > self.sweep_b_min > Decimal("0")
        ):
            raise ValueError(
                "Sweep ATR thresholds must be strictly descending and positive."
            )

        if self.minimum_premium_grade not in {"A+", "A", "B", "C"}:
            raise ValueError(
                "minimum_premium_grade must be one of 'A+', 'A', 'B', or 'C'."
            )
        if not self.default_plan.strip():
            raise ValueError("default_plan must not be empty.")
        plan_names = [plan.plan_name for plan in self.plans]
        if len(set(plan_names)) != len(plan_names):
            raise ValueError("ATR plan names must be unique.")
        if self.default_plan not in plan_names:
            raise ValueError("default_plan must reference a configured ATR plan.")
        if "standard" not in plan_names or "premium" not in plan_names:
            raise ValueError("ATR configuration must define standard and premium plans.")
        if self.missing_data_policy != "UNKNOWN":
            raise ValueError(
                "missing_data_policy must be 'UNKNOWN' in this phase."
            )

        for name, value in (
            ("max_parent_atr_ratio", self.max_parent_atr_ratio),
            ("max_sweep_atr_ratio", self.max_sweep_atr_ratio),
            ("volatility_regime_min_ratio", self.volatility_regime_min_ratio),
            ("volatility_regime_max_ratio", self.volatility_regime_max_ratio),
        ):
            if value is not None and value <= Decimal("0"):
                raise ValueError(f"{name} must be positive when configured.")

        if (
            self.volatility_regime_min_ratio is not None
            and self.volatility_regime_max_ratio is not None
            and self.volatility_regime_min_ratio > self.volatility_regime_max_ratio
        ):
            raise ValueError(
                "volatility_regime_min_ratio must not exceed its maximum."
            )
        if self.advanced_regime_filter_enabled and (
            self.volatility_regime_min_ratio is None
            or self.volatility_regime_max_ratio is None
        ):
            raise ValueError(
                "Advanced volatility filtering requires both regime bounds."
            )

    @property
    def snapshot_id(self) -> str:
        """Return a stable identifier for audit and Signal Passport records."""
        values = {
            "enabled": self.enabled,
            "shadow_mode": self.shadow_mode,
            "version": self.version,
            "method": self.method,
            "atr_period": self.atr_period,
            "parent_high_min": str(self.parent_high_min),
            "parent_a_min": str(self.parent_a_min),
            "parent_b_min": str(self.parent_b_min),
            "sweep_high_min": str(self.sweep_high_min),
            "sweep_a_min": str(self.sweep_a_min),
            "sweep_b_min": str(self.sweep_b_min),
            "minimum_premium_grade": self.minimum_premium_grade,
            "missing_data_policy": self.missing_data_policy,
            "advanced_regime_filter_enabled": self.advanced_regime_filter_enabled,
            "max_parent_atr_ratio": str(self.max_parent_atr_ratio),
            "max_sweep_atr_ratio": str(self.max_sweep_atr_ratio),
            "volatility_regime_min_ratio": str(self.volatility_regime_min_ratio),
            "volatility_regime_max_ratio": str(self.volatility_regime_max_ratio),
            "default_plan": self.default_plan,
            "plans": [
                {
                    "plan_name": plan.plan_name,
                    "atr_quality_filter": plan.atr_quality_filter,
                    "minimum_grade": plan.minimum_grade,
                }
                for plan in self.plans
            ],
        }
        raw = json.dumps(values, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]

    @property
    def mode(self) -> str:
        """Return the validated operational mode for logs and audit views."""
        if not self.enabled:
            return "OFF"
        return "SHADOW" if self.shadow_mode else "ENFORCE"

    def get_plan(self, plan_name: Optional[str]) -> Optional[ATRPlanEntitlement]:
        """Resolve a plan without granting access to unknown plan names."""
        requested = str(plan_name or "").strip().lower()
        for plan in self.plans:
            if plan.plan_name.lower() == requested:
                return plan
        return None


@dataclass(frozen=True)
class ATRQualityResult:
    """Immutable output of the ATR quality overlay."""

    status: str
    grade: Optional[str]
    atr: Optional[Decimal]
    atr_period: int
    method: str
    parent_range: Optional[Decimal]
    parent_atr_ratio: Optional[Decimal]
    sweep_depth: Optional[Decimal]
    sweep_atr_ratio: Optional[Decimal]
    configuration_snapshot_id: str
    explanation: str

    def __post_init__(self) -> None:
        if self.status not in {"VALID", "UNKNOWN", "NOT_APPLICABLE"}:
            raise ValueError(f"Unsupported ATR quality status: {self.status!r}")
        if self.grade is not None and self.grade not in {"A+", "A", "B", "C"}:
            raise ValueError(f"Unsupported ATR quality grade: {self.grade!r}")
        if self.status != "VALID" and self.grade is not None:
            raise ValueError("Only VALID ATR quality results may have a grade.")


def evaluate_atr_quality(
    detector_output: DetectorOutput,
    atr: Optional[Decimal],
    config: ATRQualityConfig,
) -> ATRQualityResult:
    """Classify a detected CRT using the approved proposed matrix.

    A non-detected CRT is not an ATR-quality candidate. Missing or invalid ATR
    data returns UNKNOWN and must not pass premium routing.
    """
    if not config.enabled:
        return ATRQualityResult(
            status="NOT_APPLICABLE",
            grade=None,
            atr=atr,
            atr_period=config.atr_period,
            method=config.method,
            parent_range=None,
            parent_atr_ratio=None,
            sweep_depth=None,
            sweep_atr_ratio=None,
            configuration_snapshot_id=config.snapshot_id,
            explanation="ATR quality overlay is disabled in configuration.",
        )

    if not detector_output.detected:
        return ATRQualityResult(
            status="NOT_APPLICABLE",
            grade=None,
            atr=atr,
            atr_period=config.atr_period,
            method=config.method,
            parent_range=None,
            parent_atr_ratio=None,
            sweep_depth=None,
            sweep_atr_ratio=None,
            configuration_snapshot_id=config.snapshot_id,
            explanation="ATR quality is not applicable because CRT was not detected.",
        )

    metadata = detector_output.metadata
    if metadata is None:
        return _unknown(config, "Detected CRT has no quality metadata.", atr)
    if atr is None or atr <= Decimal("0"):
        return _unknown(config, "ATR is missing or not positive.", atr)

    parent_range = metadata.parent_candle.high - metadata.parent_candle.low
    if parent_range <= Decimal("0"):
        return _unknown(config, "Parent candle range is not positive.", atr)

    if detector_output.classification == "bullish_crt":
        sweep_depth = max(
            Decimal("0"), metadata.parent_candle.low - metadata.sweep_candle.low
        )
    elif detector_output.classification == "bearish_crt":
        sweep_depth = max(
            Decimal("0"), metadata.sweep_candle.high - metadata.parent_candle.high
        )
    else:
        return _unknown(
            config,
            f"Unsupported detector classification: {detector_output.classification!r}.",
            atr,
        )

    parent_ratio = parent_range / atr
    sweep_ratio = sweep_depth / atr
    grade = _classify_grade(parent_ratio, sweep_ratio, config)

    return ATRQualityResult(
        status="VALID",
        grade=grade,
        atr=atr,
        atr_period=config.atr_period,
        method=config.method,
        parent_range=parent_range,
        parent_atr_ratio=parent_ratio,
        sweep_depth=sweep_depth,
        sweep_atr_ratio=sweep_ratio,
        configuration_snapshot_id=config.snapshot_id,
        explanation=(
            f"Parent range is {parent_ratio:.4f} ATR and sweep depth is "
            f"{sweep_ratio:.4f} ATR; quality grade {grade}."
        ),
    )


def _classify_grade(
    parent_ratio: Decimal,
    sweep_ratio: Decimal,
    config: ATRQualityConfig,
) -> str:
    """Apply the complete parent/sweep quality matrix without gaps."""
    parent_high = parent_ratio >= config.parent_high_min
    parent_a = parent_ratio >= config.parent_a_min
    parent_b = parent_ratio >= config.parent_b_min

    sweep_high = sweep_ratio >= config.sweep_high_min
    sweep_a = sweep_ratio >= config.sweep_a_min
    sweep_b = sweep_ratio >= config.sweep_b_min

    if parent_high and sweep_high:
        return "A+"
    if (
        (parent_high and (sweep_a or sweep_b))
        or (sweep_high and parent_b)
        or (parent_a and sweep_a)
    ):
        return "A"
    if (parent_a and sweep_b) or (parent_b and (sweep_a or sweep_b)):
        return "B"
    return "C"


def _unknown(
    config: ATRQualityConfig,
    explanation: str,
    atr: Optional[Decimal],
) -> ATRQualityResult:
    return ATRQualityResult(
        status="UNKNOWN",
        grade=None,
        atr=atr,
        atr_period=config.atr_period,
        method=config.method,
        parent_range=None,
        parent_atr_ratio=None,
        sweep_depth=None,
        sweep_atr_ratio=None,
        configuration_snapshot_id=config.snapshot_id,
        explanation=explanation,
    )
