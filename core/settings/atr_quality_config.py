"""Settings Engine loader for the proposed ATR quality overlay."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Optional

import yaml

from core.confidence.atr_quality import ATRPlanEntitlement, ATRQualityConfig


def _decimal(value: Any, field_name: str) -> Decimal:
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f"Invalid {field_name}: {value!r}") from exc


def _optional_decimal(value: Any, field_name: str) -> Optional[Decimal]:
    if value is None:
        return None
    return _decimal(value, field_name)


def _boolean(value: Any, field_name: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{field_name} must be a boolean.")
    return value


def load_atr_quality_config(config_path: str | Path) -> ATRQualityConfig:
    """Load and fail-closed validate the ATR quality configuration."""
    config_path = Path(config_path)
    if not config_path.exists():
        raise FileNotFoundError(f"Configuration file not found: {config_path}")

    with config_path.open("r", encoding="utf-8") as handle:
        raw = yaml.safe_load(handle)

    if not isinstance(raw, dict) or not isinstance(raw.get("atr_quality_overlay"), dict):
        raise ValueError(
            "Configuration must contain an 'atr_quality_overlay' mapping."
        )

    overlay = raw["atr_quality_overlay"]
    required = (
        "enabled",
        "version",
        "method",
        "atr_period",
        "minimum_premium_grade",
        "missing_data_policy",
        "parent_a_plus_min_atr_ratio",
        "parent_a_min_atr_ratio",
        "parent_b_min_atr_ratio",
        "sweep_a_plus_min_atr_ratio",
        "sweep_a_min_atr_ratio",
        "sweep_b_min_atr_ratio",
        "advanced_regime_filter_enabled",
        "default_plan",
        "plans",
    )
    missing = [key for key in required if key not in overlay]
    if missing:
        raise ValueError(
            f"Missing required ATR quality config fields: {missing}"
        )

    try:
        plans_raw = overlay["plans"]
        if not isinstance(plans_raw, dict) or not plans_raw:
            raise ValueError("plans must be a non-empty mapping.")
        plans = tuple(
            ATRPlanEntitlement(
                plan_name=str(plan_name),
                atr_quality_filter=_boolean(
                    values.get("atr_quality_filter"),
                    f"plans.{plan_name}.atr_quality_filter",
                ),
                minimum_grade=(
                    None
                    if values.get("minimum_grade") is None
                    else str(values.get("minimum_grade"))
                ),
            )
            for plan_name, values in plans_raw.items()
            if isinstance(values, dict)
        )
        if len(plans) != len(plans_raw):
            raise ValueError("Each ATR plan must be a mapping.")

        return ATRQualityConfig(
            enabled=_boolean(overlay["enabled"], "enabled"),
            shadow_mode=_boolean(
                overlay.get("shadow_mode", False), "shadow_mode"
            ),
            version=str(overlay["version"]),
            method=str(overlay["method"]),
            atr_period=int(overlay["atr_period"]),
            parent_high_min=_decimal(
                overlay["parent_a_plus_min_atr_ratio"],
                "parent_a_plus_min_atr_ratio",
            ),
            parent_a_min=_decimal(
                overlay["parent_a_min_atr_ratio"],
                "parent_a_min_atr_ratio",
            ),
            parent_b_min=_decimal(
                overlay["parent_b_min_atr_ratio"],
                "parent_b_min_atr_ratio",
            ),
            sweep_high_min=_decimal(
                overlay["sweep_a_plus_min_atr_ratio"],
                "sweep_a_plus_min_atr_ratio",
            ),
            sweep_a_min=_decimal(
                overlay["sweep_a_min_atr_ratio"],
                "sweep_a_min_atr_ratio",
            ),
            sweep_b_min=_decimal(
                overlay["sweep_b_min_atr_ratio"],
                "sweep_b_min_atr_ratio",
            ),
            minimum_premium_grade=str(overlay["minimum_premium_grade"]),
            missing_data_policy=str(overlay["missing_data_policy"]),
            advanced_regime_filter_enabled=_boolean(
                overlay["advanced_regime_filter_enabled"],
                "advanced_regime_filter_enabled",
            ),
            max_parent_atr_ratio=_optional_decimal(
                overlay.get("max_parent_atr_ratio"), "max_parent_atr_ratio"
            ),
            max_sweep_atr_ratio=_optional_decimal(
                overlay.get("max_sweep_atr_ratio"), "max_sweep_atr_ratio"
            ),
            volatility_regime_min_ratio=_optional_decimal(
                overlay.get("volatility_regime_min_ratio"),
                "volatility_regime_min_ratio",
            ),
            volatility_regime_max_ratio=_optional_decimal(
                overlay.get("volatility_regime_max_ratio"),
                "volatility_regime_max_ratio",
            ),
            default_plan=str(overlay["default_plan"]),
            plans=plans,
        )
    except (TypeError, ValueError, InvalidOperation) as exc:
        raise ValueError(
            f"ATR quality configuration validation failed: {exc}"
        ) from exc
