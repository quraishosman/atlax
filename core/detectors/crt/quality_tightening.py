"""Counterfactual CRT quality tightening evaluation.

This module measures the proposed noise-reduction policy without changing
the approved CRT detector while the policy is in shadow mode. It is pure and
deterministic so the same candle sequence can be replayed for validation.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Optional

from core.detectors.crt import rules
from core.models.candle import Candle
from core.settings.crt_config import CRTQualityTighteningConfig


@dataclass(frozen=True)
class CRTQualityTighteningResult:
    """One counterfactual evaluation of a directional CRT candidate."""

    enabled: bool
    shadow_mode: bool
    version: str
    status: str
    direction: Optional[str]
    reasons: tuple[str, ...]
    parent_atr_ratio: Optional[Decimal]
    parent_body_ratio: Optional[Decimal]
    sweep_depth_atr_ratio: Optional[Decimal]
    sweep_retracement_pct: Optional[Decimal]

    @property
    def would_suppress(self) -> bool:
        """Whether this valid evaluation fails at least one proposal rule."""
        return self.status == "VALID" and bool(self.reasons)


def evaluate_quality_tightening(
    parent: Candle,
    sweep: Candle,
    atr: Optional[Decimal],
    direction: str,
    config: CRTQualityTighteningConfig,
) -> CRTQualityTighteningResult:
    """Evaluate the proposed four-rule quality policy.

    Formulas:

    ``P = parent.range / ATR``
    ``B = abs(parent.close - parent.open) / parent.range``
    ``S = external_sweep_depth / ATR``

    For bullish candidates external depth is ``parent.low - sweep.low``.
    For bearish candidates it is ``sweep.high - parent.high``. Exact minimum
    thresholds pass; only values below a configured minimum fail.
    """

    if not config.enabled:
        return CRTQualityTighteningResult(
            enabled=False,
            shadow_mode=config.shadow_mode,
            version=config.version,
            status="NOT_APPLICABLE",
            direction=direction,
            reasons=(),
            parent_atr_ratio=None,
            parent_body_ratio=None,
            sweep_depth_atr_ratio=None,
            sweep_retracement_pct=None,
        )

    if direction not in ("bullish", "bearish"):
        raise ValueError(f"direction must be 'bullish' or 'bearish', got {direction!r}")

    if atr is None or atr <= Decimal("0"):
        return CRTQualityTighteningResult(
            enabled=True,
            shadow_mode=config.shadow_mode,
            version=config.version,
            status="UNKNOWN",
            direction=direction,
            reasons=("missing_or_invalid_atr",),
            parent_atr_ratio=None,
            parent_body_ratio=None,
            sweep_depth_atr_ratio=None,
            sweep_retracement_pct=None,
        )

    parent_range = parent.range
    if parent_range <= Decimal("0"):
        return CRTQualityTighteningResult(
            enabled=True,
            shadow_mode=config.shadow_mode,
            version=config.version,
            status="UNKNOWN",
            direction=direction,
            reasons=("invalid_parent_range",),
            parent_atr_ratio=Decimal("0"),
            parent_body_ratio=None,
            sweep_depth_atr_ratio=None,
            sweep_retracement_pct=None,
        )

    parent_atr_ratio = parent_range / atr
    parent_body_ratio = abs(parent.close - parent.open) / parent_range
    if direction == "bullish":
        sweep_depth = max(Decimal("0"), parent.low - sweep.low)
        sweep_retracement_pct = (sweep.high - parent.low) / parent_range
    else:
        sweep_depth = max(Decimal("0"), sweep.high - parent.high)
        sweep_retracement_pct = (parent.high - sweep.low) / parent_range
    sweep_depth_atr_ratio = sweep_depth / atr

    reasons: list[str] = []
    if parent_atr_ratio < Decimal(str(config.min_parent_range_atr_multiple)):
        reasons.append("parent_range_below_threshold")
    if parent_body_ratio < Decimal(str(config.min_parent_body_ratio)):
        reasons.append("parent_body_below_threshold")
    if sweep_depth_atr_ratio < Decimal(str(config.min_sweep_depth_atr_multiple)):
        reasons.append("sweep_depth_below_threshold")
    if not rules.sweep_retracement_within_limit(
        parent,
        sweep,
        config.max_sweep_retracement_pct,
        direction,
    ):
        reasons.append("sweep_retracement_above_threshold")

    return CRTQualityTighteningResult(
        enabled=True,
        shadow_mode=config.shadow_mode,
        version=config.version,
        status="VALID",
        direction=direction,
        reasons=tuple(reasons),
        parent_atr_ratio=parent_atr_ratio,
        parent_body_ratio=parent_body_ratio,
        sweep_depth_atr_ratio=sweep_depth_atr_ratio,
        sweep_retracement_pct=sweep_retracement_pct,
    )

