"""Validated Settings Engine configuration for the V1 key-level evaluator."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class KeyLevelConfig:
    """Configuration for deterministic previous-period and swing levels."""

    enabled: bool
    shadow_mode: bool
    version: str
    tolerance_atr_multiplier: Decimal
    minimum_tolerance_pips: Decimal
    swing_left_bars: int
    swing_right_bars: int
    maximum_swing_levels: int
    source_timeframes: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.version.strip():
            raise ValueError("key_level_evaluator.version must not be empty.")
        if self.shadow_mode and not self.enabled:
            raise ValueError("key_level_evaluator.shadow_mode requires enabled=true.")
        if self.tolerance_atr_multiplier <= Decimal("0"):
            raise ValueError("tolerance_atr_multiplier must be positive.")
        if self.minimum_tolerance_pips < Decimal("0"):
            raise ValueError("minimum_tolerance_pips must not be negative.")
        if self.swing_left_bars < 1 or self.swing_right_bars < 1:
            raise ValueError("Swing confirmation windows must be at least 1 bar.")
        if self.maximum_swing_levels < 1:
            raise ValueError("maximum_swing_levels must be at least 1.")
        required = {"D1", "W1", "H4"}
        if not required.issubset(set(self.source_timeframes)):
            raise ValueError(
                "source_timeframes must include D1, W1, and H4 for V1."
            )

    @property
    def snapshot_id(self) -> str:
        values = {
            "enabled": self.enabled,
            "shadow_mode": self.shadow_mode,
            "version": self.version,
            "tolerance_atr_multiplier": str(self.tolerance_atr_multiplier),
            "minimum_tolerance_pips": str(self.minimum_tolerance_pips),
            "swing_left_bars": self.swing_left_bars,
            "swing_right_bars": self.swing_right_bars,
            "maximum_swing_levels": self.maximum_swing_levels,
            "source_timeframes": list(self.source_timeframes),
        }
        encoded = json.dumps(values, sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(encoded).hexdigest()[:16]


def _decimal(value: Any, field_name: str) -> Decimal:
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f"Invalid {field_name}: {value!r}") from exc


def _boolean(value: Any, field_name: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{field_name} must be a boolean.")
    return value


def load_key_level_config(config_path: str | Path) -> KeyLevelConfig:
    """Load and fail-closed validate the key-level evaluator settings."""
    path = Path(config_path)
    if not path.exists():
        raise FileNotFoundError(f"Configuration file not found: {path}")
    with path.open("r", encoding="utf-8") as handle:
        raw = yaml.safe_load(handle) or {}
    values = raw.get("key_level_evaluator")
    if not isinstance(values, dict):
        raise ValueError("Configuration must contain key_level_evaluator mapping.")
    try:
        sources = values.get("source_timeframes", ["D1", "W1", "H4"])
        if not isinstance(sources, list) or not all(isinstance(item, str) for item in sources):
            raise ValueError("source_timeframes must be a list of strings.")
        return KeyLevelConfig(
            enabled=_boolean(values["enabled"], "enabled"),
            shadow_mode=_boolean(values.get("shadow_mode", False), "shadow_mode"),
            version=str(values["version"]),
            tolerance_atr_multiplier=_decimal(
                values["tolerance_atr_multiplier"],
                "tolerance_atr_multiplier",
            ),
            minimum_tolerance_pips=_decimal(
                values["minimum_tolerance_pips"],
                "minimum_tolerance_pips",
            ),
            swing_left_bars=int(values["swing_left_bars"]),
            swing_right_bars=int(values["swing_right_bars"]),
            maximum_swing_levels=int(values["maximum_swing_levels"]),
            source_timeframes=tuple(dict.fromkeys(sources)),
        )
    except (KeyError, TypeError, ValueError, InvalidOperation) as exc:
        raise ValueError(f"Key-level configuration validation failed: {exc}") from exc
