"""Validated configuration for the optional ATLAX Prop-Firm Mode."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import yaml


def _decimal(value: Any, field_name: str) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be a finite decimal") from exc
    if not result.is_finite():
        raise ValueError(f"{field_name} must be a finite decimal")
    return result


@dataclass(frozen=True)
class PropFirmRecoveryConfig:
    """All recovery thresholds and multipliers are explicit and validated."""

    enabled: bool
    caution_drawdown_fraction: Decimal
    recovery_drawdown_fraction: Decimal
    caution_risk_multiplier: Decimal
    recovery_risk_multiplier: Decimal
    minimum_risk_multiplier: Decimal
    consecutive_loss_trigger: int
    restore_after_drawdown_recovered: bool
    restore_profit_cushion: Decimal
    stabilization_closed_trades: int
    require_manual_resume_after_pause: bool = True

    def __post_init__(self) -> None:
        if not isinstance(self.enabled, bool):
            raise ValueError("enabled must be a boolean")
        for field_name in (
            "caution_drawdown_fraction",
            "recovery_drawdown_fraction",
            "caution_risk_multiplier",
            "recovery_risk_multiplier",
            "minimum_risk_multiplier",
            "restore_profit_cushion",
        ):
            value = getattr(self, field_name)
            if not isinstance(value, Decimal) or not value.is_finite():
                raise ValueError(f"{field_name} must be a finite Decimal")
        for field_name in (
            "caution_drawdown_fraction",
            "recovery_drawdown_fraction",
        ):
            value = getattr(self, field_name)
            if not (Decimal("0") < value <= Decimal("1")):
                raise ValueError(f"{field_name} must be in the range (0, 1]")
        if self.recovery_drawdown_fraction < self.caution_drawdown_fraction:
            raise ValueError(
                "recovery_drawdown_fraction must be >= caution_drawdown_fraction"
            )
        for field_name in (
            "caution_risk_multiplier",
            "recovery_risk_multiplier",
            "minimum_risk_multiplier",
        ):
            value = getattr(self, field_name)
            if not (Decimal("0") < value <= Decimal("1")):
                raise ValueError(f"{field_name} must be in the range (0, 1]")
        if not (
            self.minimum_risk_multiplier
            <= self.recovery_risk_multiplier
            <= self.caution_risk_multiplier
            <= Decimal("1")
        ):
            raise ValueError(
                "risk multipliers must satisfy minimum <= recovery <= caution <= 1"
            )
        if not isinstance(self.consecutive_loss_trigger, int) or self.consecutive_loss_trigger < 1:
            raise ValueError("consecutive_loss_trigger must be a positive integer")
        if self.restore_profit_cushion < 0:
            raise ValueError("restore_profit_cushion must not be negative")
        if not isinstance(self.stabilization_closed_trades, int) or self.stabilization_closed_trades < 0:
            raise ValueError(
                "stabilization_closed_trades must be a non-negative integer"
            )
        if not isinstance(self.restore_after_drawdown_recovered, bool):
            raise ValueError("restore_after_drawdown_recovered must be a boolean")
        if not isinstance(self.require_manual_resume_after_pause, bool):
            raise ValueError("require_manual_resume_after_pause must be a boolean")

    @classmethod
    def from_mapping(cls, values: dict[str, Any]) -> "PropFirmRecoveryConfig":
        required = (
            "enabled",
            "caution_drawdown_fraction",
            "recovery_drawdown_fraction",
            "caution_risk_multiplier",
            "recovery_risk_multiplier",
            "minimum_risk_multiplier",
            "consecutive_loss_trigger",
            "restore_after_drawdown_recovered",
            "restore_profit_cushion",
            "stabilization_closed_trades",
        )
        missing = [name for name in required if name not in values]
        if missing:
            raise ValueError(
                "Missing required prop_firm_mode.recovery fields: "
                + ", ".join(missing)
            )
        return cls(
            enabled=values["enabled"],
            caution_drawdown_fraction=_decimal(
                values["caution_drawdown_fraction"],
                "caution_drawdown_fraction",
            ),
            recovery_drawdown_fraction=_decimal(
                values["recovery_drawdown_fraction"],
                "recovery_drawdown_fraction",
            ),
            caution_risk_multiplier=_decimal(
                values["caution_risk_multiplier"],
                "caution_risk_multiplier",
            ),
            recovery_risk_multiplier=_decimal(
                values["recovery_risk_multiplier"],
                "recovery_risk_multiplier",
            ),
            minimum_risk_multiplier=_decimal(
                values["minimum_risk_multiplier"],
                "minimum_risk_multiplier",
            ),
            consecutive_loss_trigger=int(values["consecutive_loss_trigger"]),
            restore_after_drawdown_recovered=values[
                "restore_after_drawdown_recovered"
            ],
            restore_profit_cushion=_decimal(
                values["restore_profit_cushion"],
                "restore_profit_cushion",
            ),
            stabilization_closed_trades=int(values["stabilization_closed_trades"]),
            require_manual_resume_after_pause=bool(
                values.get("require_manual_resume_after_pause", True)
            ),
        )


def load_prop_firm_recovery_config(
    config_path: str | Path,
) -> PropFirmRecoveryConfig:
    """Load the explicit recovery section and fail closed when incomplete."""

    path = Path(config_path)
    if not path.exists():
        raise FileNotFoundError(f"Configuration file not found: {path}")
    with path.open("r", encoding="utf-8") as handle:
        raw = yaml.safe_load(handle) or {}
    prop_firm = raw.get("prop_firm_mode")
    if not isinstance(prop_firm, dict):
        raise ValueError("Configuration must contain a 'prop_firm_mode' mapping")
    recovery = prop_firm.get("recovery")
    if not isinstance(recovery, dict):
        raise ValueError(
            "Configuration must contain a 'prop_firm_mode.recovery' mapping"
        )
    return PropFirmRecoveryConfig.from_mapping(recovery)
