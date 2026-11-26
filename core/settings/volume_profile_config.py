"""
ATLAX Volume Profile Configuration Schema and Loader.

Defines VolumeProfileConfig loaded from the ``volume_profile:`` section of
config/atlax.yaml. All Volume Profile parameters must flow through this
config — never hardcoded in calculation logic.

Authority:
    docs/40_VOLUME_PROFILE_CONFLUENCE_PLAN.md §8 (Proposed Configuration Schema)
    docs/decisions/ADR-017.md (approved values)
    docs/13_CONFIGURATION.md (Settings Engine specification)

Approved defaults (ADR-017):
    volume_source:          MT5_TICK_VOLUME (real_volume preferred when available)
    value_area_percent:     0.70
    allocation_method:      RANGE_UNIFORM
    source_timeframe:       M15 for forex, M5 for gold
    bin_size_ticks:         10 for forex, 20 for gold
    enabled:                False (shadow_mode: True)

Design Decisions:
    - Fail-closed validation: any invalid value raises ValueError immediately.
    - Instrument-class defaults are required; symbol-level overrides are optional.
    - A live confidence factor weight of 0 is rejected when confidence_factor_enabled
      is True, enforcing the ADR-017 constraint that a non-zero documented weight
      must be approved before scoring can change.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, Optional

import yaml

from core.settings.timeframe_policy import is_retired_timeframe

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Approved volume sources (ADR-017)
# ---------------------------------------------------------------------------
_APPROVED_VOLUME_SOURCES = {"MT5_TICK_VOLUME", "REAL_VOLUME"}

# Approved allocation methods (Doc 40 §5.2)
_APPROVED_ALLOCATION_METHODS = {"RANGE_UNIFORM"}

# Approved profile periods (Doc 40 §3)
_APPROVED_PROFILE_PERIODS = {"PREVIOUS_DAY", "PREVIOUS_WEEK"}

# Approved instrument classes (Doc 40 §3)
_APPROVED_INSTRUMENT_CLASSES = {"forex_non_jpy", "forex_jpy", "gold"}

# Approved source timeframes for volume profile source candles
_APPROVED_SOURCE_TIMEFRAMES = {"M5", "M15"}


def instrument_class_for_symbol(symbol: str) -> str:
    """Resolve the approved profile class from a broker symbol name.

    This classification selects only data-source defaults (source timeframe
    and bin width); it does not define CRT or trade behavior.
    """
    normalized = str(symbol or "").strip().upper()
    if any(token in normalized for token in ("XAU", "GOLD", "XAG", "SILVER")):
        return "gold"
    if "JPY" in normalized:
        return "forex_jpy"
    return "forex_non_jpy"


def timeframe_minutes(timeframe: str) -> int:
    """Return minutes for the V1 source timeframes."""
    values = {"M5": 5, "M15": 15}
    try:
        return values[str(timeframe).strip().upper()]
    except KeyError as exc:
        raise ValueError(f"Unsupported Volume Profile source timeframe: {timeframe!r}") from exc


# ---------------------------------------------------------------------------
# Configuration dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class VolumeProfileConfig:
    """
    Full configuration for the Volume Profile Confluence engine.

    Attributes:
        enabled: Master toggle. When False the engine is not called.
        shadow_mode: When True calculations are logged but do not alter
            confidence, delivery, routing, or execution.
        version: Schema version string for audit and reproducibility.
        volume_source: Approved primary volume source. One of
            ``MT5_TICK_VOLUME`` or ``REAL_VOLUME``.
        real_volume_fallback: When True and real_volume is available, the
            engine uses it instead of tick_volume.
        profile_periods: Tuple of approved period labels to calculate.
        source_timeframe_by_instrument_class: Source timeframe string per
            instrument class. Required keys: ``forex_non_jpy``,
            ``forex_jpy``, ``gold``.
        source_timeframe_symbol_overrides: Optional per-symbol source
            timeframe overrides (empty by default).
        value_area_percent: Fraction of total volume the Value Area must
            contain. Must be in (0, 1). Approved value: 0.70.
        bin_size_ticks_by_instrument_class: Bin width in symbol ticks per
            instrument class. Required keys: ``forex_non_jpy``,
            ``forex_jpy``, ``gold``.
        bin_size_ticks_symbol_overrides: Optional per-symbol bin size
            overrides (empty by default).
        allocation_method: Volume allocation algorithm. Must be
            ``RANGE_UNIFORM`` in V1.
        minimum_source_bars: Minimum number of source bars required to
            build a profile. Profile returns UNKNOWN if fewer bars present.
        minimum_valid_volume_bars: Minimum bars with usable volume. Profile
            returns UNKNOWN if fewer bars have non-zero volume.
        use_current_period: Must remain False in V1. Including the
            incomplete current period introduces lookahead.
        confidence_factor_enabled: When True the Volume Profile match
            contributes to the Confidence Engine score. Requires a non-zero
            ``confidence_factor_weight`` and explicit Phase 6 approval.
        confidence_factor_weight: Numeric weight added when
            ``confidence_factor_enabled`` is True. Must be > 0 if the
            factor is enabled.
        require_for_premium_confluence: When True Premium Confluence
            subscribers additionally require a positive Volume Profile match.
            Phase 7 approval required before enabling.
    """

    enabled: bool = False
    shadow_mode: bool = True
    version: str = "0.1.0"
    volume_source: str = "MT5_TICK_VOLUME"
    real_volume_fallback: bool = True
    profile_periods: tuple[str, ...] = ("PREVIOUS_DAY", "PREVIOUS_WEEK")
    source_timeframe_by_instrument_class: Dict[str, str] = field(
        default_factory=lambda: {
            "forex_non_jpy": "M15",
            "forex_jpy": "M15",
            "gold": "M5",
        }
    )
    source_timeframe_symbol_overrides: Dict[str, str] = field(
        default_factory=dict
    )
    value_area_percent: float = 0.70
    bin_size_ticks_by_instrument_class: Dict[str, int] = field(
        default_factory=lambda: {
            "forex_non_jpy": 10,
            "forex_jpy": 10,
            "gold": 20,
        }
    )
    bin_size_ticks_symbol_overrides: Dict[str, int] = field(
        default_factory=dict
    )
    allocation_method: str = "RANGE_UNIFORM"
    minimum_source_bars: int = 50
    minimum_valid_volume_bars: int = 40
    use_current_period: bool = False
    confidence_factor_enabled: bool = False
    confidence_factor_weight: float = 0.0
    require_for_premium_confluence: bool = False

    def __post_init__(self) -> None:
        """Validate all fields. Raises ValueError on any invalid value."""

        # Volume source
        if self.volume_source not in _APPROVED_VOLUME_SOURCES:
            raise ValueError(
                f"volume_profile.volume_source must be one of "
                f"{sorted(_APPROVED_VOLUME_SOURCES)}, got {self.volume_source!r}"
            )

        # Profile periods
        for period in self.profile_periods:
            if period not in _APPROVED_PROFILE_PERIODS:
                raise ValueError(
                    f"volume_profile.profile_periods contains unknown period "
                    f"{period!r}. Approved: {sorted(_APPROVED_PROFILE_PERIODS)}"
                )
        if not self.profile_periods:
            raise ValueError(
                "volume_profile.profile_periods must contain at least one period"
            )

        # Source timeframes by instrument class
        for cls in _APPROVED_INSTRUMENT_CLASSES:
            tf = self.source_timeframe_by_instrument_class.get(cls)
            if not tf:
                raise ValueError(
                    f"volume_profile.source_timeframe_by_instrument_class "
                    f"is missing required class {cls!r}"
                )
            if tf not in _APPROVED_SOURCE_TIMEFRAMES:
                raise ValueError(
                    f"volume_profile.source_timeframe_by_instrument_class[{cls!r}] "
                    f"must be one of {sorted(_APPROVED_SOURCE_TIMEFRAMES)}, got {tf!r}"
                )
            if is_retired_timeframe(tf):
                raise ValueError(
                    f"volume_profile.source_timeframe_by_instrument_class[{cls!r}] "
                    f"uses retired timeframe {tf!r}"
                )

        # Symbol-level source timeframe overrides
        for sym, tf in self.source_timeframe_symbol_overrides.items():
            if tf not in _APPROVED_SOURCE_TIMEFRAMES:
                raise ValueError(
                    f"volume_profile.source_timeframe_symbol_overrides[{sym!r}] "
                    f"must be one of {sorted(_APPROVED_SOURCE_TIMEFRAMES)}, got {tf!r}"
                )
            if is_retired_timeframe(tf):
                raise ValueError(
                    f"volume_profile.source_timeframe_symbol_overrides[{sym!r}] "
                    f"uses retired timeframe {tf!r}"
                )

        # Value area percent
        if not (0.0 < self.value_area_percent < 1.0):
            raise ValueError(
                f"volume_profile.value_area_percent must be in (0, 1), "
                f"got {self.value_area_percent}"
            )

        # Bin sizes by instrument class
        for cls in _APPROVED_INSTRUMENT_CLASSES:
            ticks = self.bin_size_ticks_by_instrument_class.get(cls)
            if ticks is None:
                raise ValueError(
                    f"volume_profile.bin_size_ticks_by_instrument_class "
                    f"is missing required class {cls!r}"
                )
            if ticks <= 0:
                raise ValueError(
                    f"volume_profile.bin_size_ticks_by_instrument_class[{cls!r}] "
                    f"must be > 0, got {ticks}"
                )

        # Symbol-level bin size overrides
        for sym, ticks in self.bin_size_ticks_symbol_overrides.items():
            if ticks <= 0:
                raise ValueError(
                    f"volume_profile.bin_size_ticks_symbol_overrides[{sym!r}] "
                    f"must be > 0, got {ticks}"
                )

        # Allocation method
        if self.allocation_method not in _APPROVED_ALLOCATION_METHODS:
            raise ValueError(
                f"volume_profile.allocation_method must be one of "
                f"{sorted(_APPROVED_ALLOCATION_METHODS)}, got {self.allocation_method!r}"
            )

        # Minimum bars
        if self.minimum_source_bars < 1:
            raise ValueError(
                f"volume_profile.minimum_source_bars must be >= 1, "
                f"got {self.minimum_source_bars}"
            )
        if self.minimum_valid_volume_bars < 1:
            raise ValueError(
                f"volume_profile.minimum_valid_volume_bars must be >= 1, "
                f"got {self.minimum_valid_volume_bars}"
            )
        if self.minimum_valid_volume_bars > self.minimum_source_bars:
            raise ValueError(
                f"volume_profile.minimum_valid_volume_bars ({self.minimum_valid_volume_bars}) "
                f"cannot exceed minimum_source_bars ({self.minimum_source_bars})"
            )

        # Current period lookahead guard
        if self.use_current_period:
            raise ValueError(
                "volume_profile.use_current_period must be False in V1. "
                "Including the incomplete current period introduces lookahead "
                "(docs/40_VOLUME_PROFILE_CONFLUENCE_PLAN.md §3)."
            )

        # Confidence factor: weight must be non-zero when enabled
        if self.confidence_factor_enabled and self.confidence_factor_weight <= 0:
            raise ValueError(
                "volume_profile.confidence_factor_weight must be > 0 when "
                "confidence_factor_enabled is True. A non-zero approved weight "
                "is required before confidence scoring changes (ADR-017 Phase 6)."
            )


# ---------------------------------------------------------------------------
# YAML loader
# ---------------------------------------------------------------------------


def load_volume_profile_config(config_path: str) -> VolumeProfileConfig:
    """
    Load and validate VolumeProfileConfig from a YAML file.

    Reads the ``volume_profile:`` section. If the section is absent, returns
    the safe default config (disabled, shadow_mode=True) and logs a warning.

    Fail-closed: raises ValueError for any invalid field value.

    Authority: docs/13_CONFIGURATION.md, docs/40_VOLUME_PROFILE_CONFLUENCE_PLAN.md §8

    Args:
        config_path: Path to the YAML config file.

    Returns:
        A validated, immutable VolumeProfileConfig.
    """
    with open(config_path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    if not raw or "volume_profile" not in raw:
        logger.warning(
            '{"event":"volume_profile_config_missing","detail":"section absent,'
            ' using safe defaults (disabled, shadow_mode=true)"}'
        )
        return VolumeProfileConfig()

    sec: Any = raw["volume_profile"]
    if not isinstance(sec, dict):
        raise ValueError(
            "volume_profile configuration section must be a mapping."
        )

    try:
        # Instrument-class maps — merge defaults then apply YAML overrides
        default_tf_map = {"forex_non_jpy": "M15", "forex_jpy": "M15", "gold": "M5"}
        tf_raw = sec.get("source_timeframe_by_instrument_class", {})
        tf_map = {**default_tf_map, **{str(k): str(v) for k, v in tf_raw.items()}}

        default_bin_map = {"forex_non_jpy": 10, "forex_jpy": 10, "gold": 20}
        bin_raw = sec.get("bin_size_ticks_by_instrument_class", {})
        bin_map = {**default_bin_map, **{str(k): int(v) for k, v in bin_raw.items()}}

        tf_overrides_raw = sec.get("source_timeframe_symbol_overrides", {}) or {}
        bin_overrides_raw = sec.get("bin_size_ticks_symbol_overrides", {}) or {}

        periods_raw = sec.get("profile_periods", ["PREVIOUS_DAY", "PREVIOUS_WEEK"])

        config = VolumeProfileConfig(
            enabled=bool(sec.get("enabled", False)),
            shadow_mode=bool(sec.get("shadow_mode", True)),
            version=str(sec.get("version", "0.1.0")),
            volume_source=str(sec.get("volume_source", "MT5_TICK_VOLUME")),
            real_volume_fallback=bool(sec.get("real_volume_fallback", True)),
            profile_periods=tuple(str(p) for p in periods_raw),
            source_timeframe_by_instrument_class=tf_map,
            source_timeframe_symbol_overrides={
                str(k): str(v) for k, v in tf_overrides_raw.items()
            },
            value_area_percent=float(sec.get("value_area_percent", 0.70)),
            bin_size_ticks_by_instrument_class=bin_map,
            bin_size_ticks_symbol_overrides={
                str(k): int(v) for k, v in bin_overrides_raw.items()
            },
            allocation_method=str(sec.get("allocation_method", "RANGE_UNIFORM")),
            minimum_source_bars=int(sec.get("minimum_source_bars", 50)),
            minimum_valid_volume_bars=int(sec.get("minimum_valid_volume_bars", 40)),
            use_current_period=bool(sec.get("use_current_period", False)),
            confidence_factor_enabled=bool(sec.get("confidence_factor_enabled", False)),
            confidence_factor_weight=float(sec.get("confidence_factor_weight", 0.0)),
            require_for_premium_confluence=bool(
                sec.get("require_for_premium_confluence", False)
            ),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(
            f"Volume profile configuration validation failed: {exc}"
        ) from exc

    logger.info(
        '{"event":"volume_profile_config_loaded","enabled":%s,"shadow_mode":%s,'
        '"version":"%s","volume_source":"%s"}',
        str(config.enabled).lower(),
        str(config.shadow_mode).lower(),
        config.version,
        config.volume_source,
    )
    return config
