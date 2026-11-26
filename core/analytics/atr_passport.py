"""Serialization helpers for ATR quality fields stored in the Signal Passport."""

from __future__ import annotations

from typing import Any, Optional

from core.confidence.atr_quality import ATRQualityResult


ATR_PASSPORT_FIELDS = (
    "atr_method",
    "atr_period",
    "atr_value",
    "parent_range",
    "parent_atr_ratio",
    "sweep_depth",
    "sweep_atr_ratio",
    "atr_grade",
    "atr_configuration_snapshot_id",
    "atr_evaluation_status",
    "atr_explanation",
)


def atr_quality_fields(result: Optional[ATRQualityResult]) -> dict[str, Any]:
    """Convert an immutable ATR result into stable journal columns."""
    if result is None:
        return {
            "atr_method": "",
            "atr_period": None,
            "atr_value": None,
            "parent_range": None,
            "parent_atr_ratio": None,
            "sweep_depth": None,
            "sweep_atr_ratio": None,
            "atr_grade": None,
            "atr_configuration_snapshot_id": "",
            "atr_evaluation_status": "NOT_APPLICABLE",
            "atr_explanation": "ATR quality was not evaluated for this signal.",
        }

    return {
        "atr_method": result.method,
        "atr_period": result.atr_period,
        "atr_value": str(result.atr) if result.atr is not None else None,
        "parent_range": str(result.parent_range) if result.parent_range is not None else None,
        "parent_atr_ratio": str(result.parent_atr_ratio) if result.parent_atr_ratio is not None else None,
        "sweep_depth": str(result.sweep_depth) if result.sweep_depth is not None else None,
        "sweep_atr_ratio": str(result.sweep_atr_ratio) if result.sweep_atr_ratio is not None else None,
        "atr_grade": result.grade,
        "atr_configuration_snapshot_id": result.configuration_snapshot_id,
        "atr_evaluation_status": result.status,
        "atr_explanation": result.explanation,
    }
