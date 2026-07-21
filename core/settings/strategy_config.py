"""
ATLAX Strategy Engine Configuration Schema and Loader.

Defines the StrategyConfig dataclass loaded from config/atlax.yaml.
All strategy behavior parameters must flow through this config.

Authority: docs/13_CONFIGURATION.md, docs/08_STRATEGY_ENGINE.md
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import yaml


@dataclass(frozen=True)
class ProfileConfig:
    """
    Configuration for a single trader profile.

    Profiles define which timeframes are monitored and what
    confluence requirements apply.

    Authority: docs/03_ARCHITECTURE.md (Profile Model)
    """
    name: str
    enabled: bool
    allowed_timeframes: tuple[str, ...]
    require_session_alignment: bool
    require_htf_trend_alignment: bool
    min_confidence_threshold: float

    def __post_init__(self) -> None:
        valid_profiles = ("scalper", "day_trader", "swing_trader")
        if self.name not in valid_profiles:
            raise ValueError(
                f"Profile name must be one of {valid_profiles}, got {self.name!r}"
            )
        if not (0.0 <= self.min_confidence_threshold <= 100.0):
            raise ValueError(
                f"min_confidence_threshold must be between 0.0 and 100.0, "
                f"got {self.min_confidence_threshold}"
            )


@dataclass(frozen=True)
class StrategyConfig:
    """
    Configuration for the CRT Strategy Engine.

    Loaded from the 'strategy:' section of config/atlax.yaml.
    Fail-closed: any invalid field raises ValueError immediately.

    Authority: docs/08_STRATEGY_ENGINE.md, docs/13_CONFIGURATION.md

    Attributes:
        enabled: Master toggle for the strategy engine.
        strategy_name: Name of this strategy instance.
        news_filter_enabled: Whether to block candidates during high-impact news.
        spread_filter_enabled: Whether to check spread before producing candidates.
        max_spread_pips: Maximum allowed spread in pips (if spread filter enabled).
        profiles: Tuple of active profile configurations.
    """
    enabled: bool
    strategy_name: str
    news_filter_enabled: bool
    spread_filter_enabled: bool
    max_spread_pips: float
    profiles: tuple[ProfileConfig, ...]

    def __post_init__(self) -> None:
        if self.max_spread_pips < 0:
            raise ValueError(
                f"max_spread_pips must be >= 0, got {self.max_spread_pips}"
            )
        if not self.profiles:
            raise ValueError("At least one profile must be configured.")

    @property
    def active_profiles(self) -> tuple[ProfileConfig, ...]:
        """Returns only enabled profiles."""
        return tuple(p for p in self.profiles if p.enabled)

    def get_profile(self, name: str) -> Optional[ProfileConfig]:
        """Return a profile by name, or None if not found."""
        for p in self.profiles:
            if p.name == name:
                return p
        return None


def load_strategy_config(config_path: str) -> StrategyConfig:
    """
    Load and validate StrategyConfig from a YAML file.

    Fail-closed: raises ValueError for any invalid configuration.
    Raises FileNotFoundError if the config file does not exist.

    Authority: docs/13_CONFIGURATION.md
        "Invalid configuration must fail closed."

    Args:
        config_path: Path to the YAML config file (e.g., "config/atlax.yaml").

    Returns:
        A validated, immutable StrategyConfig instance.
    """
    with open(config_path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    strategy_raw = raw.get("strategy", {})

    if not strategy_raw:
        raise ValueError(
            "Strategy configuration ('strategy:' section) is missing from "
            f"{config_path}. Cannot start without strategy config."
        )

    # Parse profiles
    profiles_raw = strategy_raw.get("profiles", [])
    if not profiles_raw:
        raise ValueError("strategy.profiles must define at least one profile.")

    profiles = []
    for p in profiles_raw:
        try:
            profiles.append(ProfileConfig(
                name=p["name"],
                enabled=bool(p.get("enabled", True)),
                allowed_timeframes=tuple(p.get("allowed_timeframes", [])),
                require_session_alignment=bool(p.get("require_session_alignment", False)),
                require_htf_trend_alignment=bool(p.get("require_htf_trend_alignment", False)),
                min_confidence_threshold=float(p.get("min_confidence_threshold", 60.0)),
            ))
        except (KeyError, TypeError, ValueError) as e:
            raise ValueError(f"Invalid profile config: {e}") from e

    try:
        config = StrategyConfig(
            enabled=bool(strategy_raw.get("enabled", True)),
            strategy_name=str(strategy_raw.get("strategy_name", "CRTStrategy")),
            news_filter_enabled=bool(strategy_raw.get("news_filter_enabled", False)),
            spread_filter_enabled=bool(strategy_raw.get("spread_filter_enabled", False)),
            max_spread_pips=float(strategy_raw.get("max_spread_pips", 3.0)),
            profiles=tuple(profiles),
        )
    except (KeyError, TypeError, ValueError) as e:
        raise ValueError(f"Strategy configuration validation failed: {e}") from e

    return config
