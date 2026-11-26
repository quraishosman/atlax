"""Configuration for closed-candle higher-timeframe momentum context."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass(frozen=True)
class HTFMomentumConfig:
    """Validated, configuration-driven H1 context policy."""

    enabled: bool
    shadow_mode: bool
    controller_timeframe: str
    child_timeframes: tuple[str, ...]
    lookback_candles: int
    strong_run_candles: int
    aligned_adjustment: float
    opposing_adjustment: float
    sideways_adjustment: float
    unknown_adjustment: float
    run_bonus_per_extra_candle: float
    max_run_bonus: float

    def __post_init__(self) -> None:
        if not self.controller_timeframe:
            raise ValueError("htf momentum controller_timeframe must not be empty")
        if not self.child_timeframes:
            raise ValueError("htf momentum child_timeframes must not be empty")
        if self.controller_timeframe in self.child_timeframes:
            raise ValueError("htf momentum controller cannot also be a child")
        if self.lookback_candles < self.strong_run_candles or self.lookback_candles < 1:
            raise ValueError("htf momentum lookback must cover the strong run")
        if self.strong_run_candles < 2:
            raise ValueError("htf momentum strong_run_candles must be at least 2")
        if self.max_run_bonus < 0 or self.run_bonus_per_extra_candle < 0:
            raise ValueError("htf momentum run bonuses must not be negative")

    @property
    def snapshot_id(self) -> str:
        payload = {
            "enabled": self.enabled,
            "shadow_mode": self.shadow_mode,
            "controller_timeframe": self.controller_timeframe,
            "child_timeframes": self.child_timeframes,
            "lookback_candles": self.lookback_candles,
            "strong_run_candles": self.strong_run_candles,
            "aligned_adjustment": self.aligned_adjustment,
            "opposing_adjustment": self.opposing_adjustment,
            "sideways_adjustment": self.sideways_adjustment,
            "unknown_adjustment": self.unknown_adjustment,
            "run_bonus_per_extra_candle": self.run_bonus_per_extra_candle,
            "max_run_bonus": self.max_run_bonus,
        }
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:12]


def load_htf_momentum_config(config_path: str | Path) -> HTFMomentumConfig:
    """Load the optional confidence.htf_momentum section fail-closed."""
    path = Path(config_path)
    with path.open("r", encoding="utf-8") as handle:
        raw = yaml.safe_load(handle)
    if not isinstance(raw, dict):
        raise ValueError(f"Configuration file {path} must contain a mapping")

    section = (raw.get("confidence") or {}).get("htf_momentum") or {}
    if not isinstance(section, dict):
        raise ValueError("confidence.htf_momentum must be a mapping")
    child_timeframes = tuple(
        str(value).strip().upper()
        for value in (section.get("child_timeframes") or ("M5", "M15"))
        if str(value).strip()
    )
    return HTFMomentumConfig(
        enabled=bool(section.get("enabled", False)),
        shadow_mode=bool(section.get("shadow_mode", True)),
        controller_timeframe=str(section.get("controller_timeframe", "H1")).strip().upper(),
        child_timeframes=child_timeframes,
        lookback_candles=int(section.get("lookback_candles", 6)),
        strong_run_candles=int(section.get("strong_run_candles", 2)),
        aligned_adjustment=float(section.get("aligned_adjustment", 0.0)),
        opposing_adjustment=float(section.get("opposing_adjustment", 0.0)),
        sideways_adjustment=float(section.get("sideways_adjustment", 0.0)),
        unknown_adjustment=float(section.get("unknown_adjustment", 0.0)),
        run_bonus_per_extra_candle=float(section.get("run_bonus_per_extra_candle", 0.0)),
        max_run_bonus=float(section.get("max_run_bonus", 0.0)),
    )
