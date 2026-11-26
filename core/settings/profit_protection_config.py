"""Validated configuration for the ATLAX profit-protection lifecycle."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class ProfitProtectionConfig:
    """Validated MFE, profit-lock, and momentum-protection settings."""

    enabled: bool
    shadow_mode: bool
    activation_r: float
    minimum_lock_r: float
    maximum_giveback_r: float
    confirmation_bars: int
    momentum_loss_enabled: bool
    momentum_shadow_mode: bool
    momentum_source_timeframe: str
    minimum_current_profit_r: float
    momentum_action: str
    close_remaining_on_momentum_loss: bool
    include_transaction_cost_buffer: bool
    trailing_stop_enabled: bool = False
    trailing_activation_r: float = 0.25
    trailing_distance_r: float = 0.20
    trailing_step_r: float = 0.05
    target_proximity_fraction: float = 0.80

    _ALLOWED_MOMENTUM_ACTIONS = frozenset(
        {"TIGHTEN_STOP", "PARTIAL_CLOSE", "FULL_CLOSE"}
    )

    def __post_init__(self) -> None:
        errors: list[str] = []
        if not isinstance(self.enabled, bool):
            errors.append("enabled must be a boolean")
        if not isinstance(self.shadow_mode, bool):
            errors.append("shadow_mode must be a boolean")
        if self.activation_r < 0:
            errors.append(f"activation_r must be >= 0, got {self.activation_r}")
        if self.minimum_lock_r < 0:
            errors.append(f"minimum_lock_r must be >= 0, got {self.minimum_lock_r}")
        if self.maximum_giveback_r <= 0:
            errors.append(
                "maximum_giveback_r must be > 0, "
                f"got {self.maximum_giveback_r}"
            )
        if self.confirmation_bars < 1:
            errors.append(
                f"confirmation_bars must be >= 1, got {self.confirmation_bars}"
            )
        if not isinstance(self.momentum_loss_enabled, bool):
            errors.append("momentum_loss_enabled must be a boolean")
        if not isinstance(self.momentum_shadow_mode, bool):
            errors.append("momentum_shadow_mode must be a boolean")
        if (
            not isinstance(self.momentum_source_timeframe, str)
            or not self.momentum_source_timeframe.strip()
        ):
            errors.append("momentum_source_timeframe must be a non-empty string")
        if self.minimum_current_profit_r < 0:
            errors.append(
                "minimum_current_profit_r must be >= 0, "
                f"got {self.minimum_current_profit_r}"
            )
        if (
            not isinstance(self.momentum_action, str)
            or self.momentum_action not in self._ALLOWED_MOMENTUM_ACTIONS
        ):
            errors.append(
                "momentum_action must be one of "
                f"{sorted(self._ALLOWED_MOMENTUM_ACTIONS)}, got {self.momentum_action!r}"
            )
        if not isinstance(self.close_remaining_on_momentum_loss, bool):
            errors.append("close_remaining_on_momentum_loss must be a boolean")
        if not isinstance(self.include_transaction_cost_buffer, bool):
            errors.append("include_transaction_cost_buffer must be a boolean")
        if not isinstance(self.trailing_stop_enabled, bool):
            errors.append("trailing_stop_enabled must be a boolean")
        if self.trailing_activation_r < 0:
            errors.append(
                "trailing_activation_r must be >= 0, "
                f"got {self.trailing_activation_r}"
            )
        if self.trailing_distance_r <= 0:
            errors.append(
                "trailing_distance_r must be > 0, "
                f"got {self.trailing_distance_r}"
            )
        if self.trailing_step_r <= 0:
            errors.append(
                "trailing_step_r must be > 0, "
                f"got {self.trailing_step_r}"
            )
        if not 0 <= self.target_proximity_fraction <= 1:
            errors.append(
                "target_proximity_fraction must be in the range [0, 1], "
                f"got {self.target_proximity_fraction}"
            )
        if errors:
            raise ValueError(
                "Profit protection configuration validation failed:\n  "
                + "\n  ".join(errors)
            )


def _require(values: dict[str, Any], name: str) -> Any:
    if name not in values:
        raise ValueError(f"Missing required profit_protection config field: {name}")
    return values[name]


def _require_bool(values: dict[str, Any], name: str) -> bool:
    value = _require(values, name)
    if not isinstance(value, bool):
        raise ValueError(f"profit_protection.{name} must be a boolean")
    return value


def load_profit_protection_config(
    config_path: str | Path,
) -> ProfitProtectionConfig:
    """Load and fail-closed validate the top-level config section."""

    path = Path(config_path)
    if not path.exists():
        raise FileNotFoundError(f"Configuration file not found: {path}")
    with path.open("r", encoding="utf-8") as handle:
        raw = yaml.safe_load(handle) or {}

    values = raw.get("profit_protection")
    if not isinstance(values, dict):
        raise ValueError("Configuration must contain a 'profit_protection' mapping.")

    try:
        return ProfitProtectionConfig(
            enabled=_require_bool(values, "enabled"),
            shadow_mode=_require_bool(values, "shadow_mode"),
            activation_r=float(_require(values, "activation_r")),
            minimum_lock_r=float(_require(values, "minimum_lock_r")),
            maximum_giveback_r=float(_require(values, "maximum_giveback_r")),
            confirmation_bars=int(_require(values, "confirmation_bars")),
            momentum_loss_enabled=_require_bool(values, "momentum_loss_enabled"),
            momentum_shadow_mode=_require_bool(values, "momentum_shadow_mode"),
            momentum_source_timeframe=str(
                _require(values, "momentum_source_timeframe")
            ).strip().upper(),
            minimum_current_profit_r=float(
                _require(values, "minimum_current_profit_r")
            ),
            momentum_action=str(_require(values, "momentum_action")).strip().upper(),
            close_remaining_on_momentum_loss=_require_bool(
                values, "close_remaining_on_momentum_loss"
            ),
            include_transaction_cost_buffer=_require_bool(
                values, "include_transaction_cost_buffer"
            ),
            trailing_stop_enabled=_require_bool(values, "trailing_stop_enabled"),
            trailing_activation_r=float(_require(values, "trailing_activation_r")),
            trailing_distance_r=float(_require(values, "trailing_distance_r")),
            trailing_step_r=float(_require(values, "trailing_step_r")),
            target_proximity_fraction=float(_require(values, "target_proximity_fraction")),
        )
    except (TypeError, ValueError) as exc:
        if isinstance(exc, ValueError) and str(exc).startswith(
            "Profit protection configuration"
        ):
            raise
        raise ValueError(f"Profit protection configuration validation failed: {exc}") from exc
