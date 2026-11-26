"""Validated FVG detector and watcher configuration."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class FVGConfig:
    """Configuration for optional LTF FVG confluence and entry watches."""

    enabled: bool
    execution_gate_enabled: bool
    min_gap_pips: float
    ltf_candle_count: int
    ltf_atr_period: int
    watch_max_age_seconds: int

    def __post_init__(self) -> None:
        errors: list[str] = []
        if not isinstance(self.enabled, bool):
            errors.append("enabled must be a boolean")
        if not isinstance(self.execution_gate_enabled, bool):
            errors.append("execution_gate_enabled must be a boolean")
        if self.min_gap_pips <= 0:
            errors.append(f"min_gap_pips must be > 0, got {self.min_gap_pips}")
        if self.ltf_candle_count < 3:
            errors.append(
                f"ltf_candle_count must be >= 3, got {self.ltf_candle_count}"
            )
        if self.ltf_atr_period < 1:
            errors.append(f"ltf_atr_period must be >= 1, got {self.ltf_atr_period}")
        if self.watch_max_age_seconds < 1:
            errors.append(
                "watch_max_age_seconds must be >= 1, "
                f"got {self.watch_max_age_seconds}"
            )
        if errors:
            raise ValueError("FVG configuration validation failed:\n  " + "\n  ".join(errors))


def _require(values: dict[str, Any], name: str) -> Any:
    if name not in values:
        raise ValueError(f"Missing required FVG config field: {name}")
    return values[name]


def load_fvg_config(config_path: str | Path) -> FVGConfig:
    """Load and fail-closed validate the top-level ``fvg`` section."""

    path = Path(config_path)
    if not path.exists():
        raise FileNotFoundError(f"Configuration file not found: {path}")
    with path.open("r", encoding="utf-8") as handle:
        raw = yaml.safe_load(handle) or {}

    values = raw.get("fvg")
    if not isinstance(values, dict):
        raise ValueError("Configuration must contain an 'fvg' mapping.")

    enabled = _require(values, "enabled")
    if not isinstance(enabled, bool):
        raise ValueError("fvg.enabled must be a boolean")

    try:
        return FVGConfig(
            enabled=enabled,
            execution_gate_enabled=_require(values, "execution_gate_enabled"),
            min_gap_pips=float(_require(values, "min_gap_pips")),
            ltf_candle_count=int(_require(values, "ltf_candle_count")),
            ltf_atr_period=int(_require(values, "ltf_atr_period")),
            watch_max_age_seconds=int(_require(values, "watch_max_age_seconds")),
        )
    except (TypeError, ValueError) as exc:
        if isinstance(exc, ValueError) and str(exc).startswith("FVG configuration"):
            raise
        raise ValueError(f"FVG configuration validation failed: {exc}") from exc
