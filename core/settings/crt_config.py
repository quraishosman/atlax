"""
ATLAX CRT Configuration Schema and Loader.

Defines the CRTConfig dataclass and the YAML loader that populates it
from config/atlax.yaml. All CRT-specific thresholds, toggles, and
session windows live here — never hardcoded in detection logic.

Authority Documents:
    - docs/13_CONFIGURATION.md (Settings Engine specification)
    - docs/rulebooks/CRT_RULEBOOK.md, CRT-DATA-003 (allowed timeframes)
    - docs/rulebooks/CRT_RULEBOOK.md, CRT-PARENT-004 (ATR-based min range)
    - docs/rulebooks/CRT_RULEBOOK.md, CRT-BULL-001/CRT-BEAR-001 (configurable confirmation)
    - docs/rulebooks/CRT_RULEBOOK.md, CRT-SWEEP-INVALID-003 (gap tolerance)
    - docs/rulebooks/CRT_RULEBOOK.md, CRT-MTF-001 (timeframe pairings)
    - docs/rulebooks/CRT_RULEBOOK.md, CRT-SESSION-001 (kill zones)

Design Decisions:
    - Decimal for pip/price values: consistent with Candle model.
    - Validation on construction: invalid config fails closed immediately
      with a clear error, per docs/13_CONFIGURATION.md.
    - KillZone uses datetime.time (UTC): simple, timezone-safe comparison.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import time
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Configuration Data Models
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class KillZone:
    """
    A high-volatility trading session window.

    Authority: CRT-SESSION-001
        Kill zones are the time windows where CRT setups have the
        highest probability: London Open and New York AM open.

    Attributes:
        name: Human-readable name (e.g., "London Open").
        utc_start: Session start time in UTC.
        utc_end: Session end time in UTC.
    """

    name: str
    utc_start: time
    utc_end: time

    def __post_init__(self) -> None:
        """Validate kill zone invariants."""
        if not self.name or not self.name.strip():
            raise ValueError("KillZone.name must not be empty.")

    def contains(self, check_time: time) -> bool:
        """
        Return True if check_time falls within this kill zone.

        Handles overnight windows (e.g., utc_start=22:00, utc_end=02:00)
        by splitting the comparison across midnight.
        """
        if self.utc_start <= self.utc_end:
            # Normal window: e.g., 02:00 — 05:00
            return self.utc_start <= check_time <= self.utc_end
        else:
            # Overnight window: e.g., 22:00 — 02:00
            return check_time >= self.utc_start or check_time <= self.utc_end


@dataclass(frozen=True)
class CRTConfig:
    """
    Complete CRT detector configuration schema.

    Every field in this class maps to one or more approved CRT rules.
    No field may be invented without rulebook authority.

    Authority: docs/13_CONFIGURATION.md, docs/rulebooks/CRT_RULEBOOK.md

    Attributes:
        enabled: Master toggle for CRT detection.
        min_parent_range_atr_multiple: Minimum parent range as a multiple
            of ATR. CRT-PARENT-004. Example: 0.5 means parent range must
            be at least 0.5x the ATR.
        require_confirmation_candle: If True, the 3rd candle (confirmation)
            must close above sweep.high (bull) or below sweep.low (bear).
            If False, detection fires on sweep candle close alone.
            CRT-BULL-001, CRT-BEAR-001.
        max_gap_tolerance_pips: Maximum allowed gap between sweep candle
            open and the parent range boundary, in price units.
            CRT-SWEEP-INVALID-003.
        price_comparison_tolerance: Small tolerance for price comparisons
            to handle broker rounding quirks, in price units.
        pip_size: The pip size for the instrument class (e.g., 0.0001
            for 4-decimal forex pairs). Used for sweep_distance_pips.
            CRT-QUALITY-001.
        kill_zones: List of configured kill zone session windows.
            CRT-SESSION-001.
        allowed_timeframes: List of valid timeframe identifiers.
            CRT-DATA-003.
        timeframe_pairings: Mapping of HTF range timeframe to LTF
            entry timeframe. CRT-MTF-001.
    """

    enabled: bool
    min_parent_range_atr_multiple: float
    require_confirmation_candle: bool
    max_gap_tolerance_pips: Decimal
    price_comparison_tolerance: Decimal
    pip_size: Decimal
    kill_zones: tuple[KillZone, ...]
    allowed_timeframes: tuple[str, ...]
    timeframe_pairings: dict[str, str]

    def __post_init__(self) -> None:
        """
        Validate configuration invariants on construction.

        Authority: docs/13_CONFIGURATION.md
            "Invalid configuration must fail closed, keep the last known
            valid settings when available, and log the reason."
        """
        errors: list[str] = []

        if self.min_parent_range_atr_multiple <= 0:
            errors.append(
                f"min_parent_range_atr_multiple must be > 0, "
                f"got {self.min_parent_range_atr_multiple}"
            )

        if self.max_gap_tolerance_pips < Decimal("0"):
            errors.append(
                f"max_gap_tolerance_pips must be >= 0, "
                f"got {self.max_gap_tolerance_pips}"
            )

        if self.price_comparison_tolerance < Decimal("0"):
            errors.append(
                f"price_comparison_tolerance must be >= 0, "
                f"got {self.price_comparison_tolerance}"
            )

        if self.pip_size <= Decimal("0"):
            errors.append(
                f"pip_size must be > 0, got {self.pip_size}"
            )

        if not self.allowed_timeframes:
            errors.append("allowed_timeframes must not be empty.")

        # Validate timeframe pairings reference allowed timeframes
        all_tf = set(self.allowed_timeframes)
        for htf, ltf in self.timeframe_pairings.items():
            if htf not in all_tf:
                errors.append(
                    f"timeframe_pairings key {htf!r} is not in "
                    f"allowed_timeframes."
                )
            if ltf not in all_tf:
                errors.append(
                    f"timeframe_pairings value {ltf!r} (for {htf!r}) "
                    f"is not in allowed_timeframes."
                )

        if errors:
            msg = "CRT configuration validation failed:\n  " + "\n  ".join(errors)
            logger.error(msg)
            raise ValueError(msg)


# ---------------------------------------------------------------------------
# YAML Loader
# ---------------------------------------------------------------------------


def _parse_time(value: str) -> time:
    """
    Parse a time string like '02:00' or '14:30' into a datetime.time.

    Raises ValueError if the format is invalid.
    """
    parts = value.strip().split(":")
    if len(parts) != 2:
        raise ValueError(
            f"Invalid time format {value!r}. Expected 'HH:MM'."
        )
    try:
        return time(int(parts[0]), int(parts[1]))
    except (ValueError, TypeError) as exc:
        raise ValueError(
            f"Invalid time value {value!r}: {exc}"
        ) from exc


def _parse_decimal(value: Any, field_name: str) -> Decimal:
    """
    Safely convert a YAML value to Decimal.

    Raises ValueError if conversion fails.
    """
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(
            f"Cannot convert {field_name}={value!r} to Decimal: {exc}"
        ) from exc


def load_crt_config(config_path: str | Path) -> CRTConfig:
    """
    Load CRT configuration from a YAML file.

    The file must contain a top-level 'crt' key with the required
    fields. Missing or invalid fields cause an immediate failure
    (fail closed, per docs/13_CONFIGURATION.md).

    Args:
        config_path: Absolute or relative path to the YAML config file.

    Returns:
        A validated, frozen CRTConfig instance.

    Raises:
        FileNotFoundError: If the config file does not exist.
        ValueError: If any required field is missing or invalid.
        yaml.YAMLError: If the file is not valid YAML.
    """
    config_path = Path(config_path)

    if not config_path.exists():
        raise FileNotFoundError(
            f"Configuration file not found: {config_path}"
        )

    with open(config_path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    if not isinstance(raw, dict) or "crt" not in raw:
        raise ValueError(
            f"Configuration file {config_path} must contain a "
            f"top-level 'crt' key."
        )

    crt = raw["crt"]

    # --- Required scalar fields ---
    required_fields = [
        "enabled", "min_parent_range_atr_multiple",
        "require_confirmation_candle", "max_gap_tolerance_pips",
        "price_comparison_tolerance_pips", "pip_size",
        "allowed_timeframes", "timeframe_pairings", "kill_zones",
    ]
    missing = [f for f in required_fields if f not in crt]
    if missing:
        raise ValueError(
            f"Missing required CRT config fields: {missing}"
        )

    # --- Parse kill zones ---
    kill_zones: list[KillZone] = []
    for i, kz_raw in enumerate(crt["kill_zones"]):
        if not isinstance(kz_raw, dict):
            raise ValueError(
                f"kill_zones[{i}] must be a mapping, got {type(kz_raw).__name__}"
            )
        for kz_field in ("name", "utc_start", "utc_end"):
            if kz_field not in kz_raw:
                raise ValueError(
                    f"kill_zones[{i}] missing required field {kz_field!r}"
                )
        kill_zones.append(KillZone(
            name=str(kz_raw["name"]),
            utc_start=_parse_time(str(kz_raw["utc_start"])),
            utc_end=_parse_time(str(kz_raw["utc_end"])),
        ))

    # --- Parse timeframe pairings ---
    pairings_raw = crt["timeframe_pairings"]
    if not isinstance(pairings_raw, dict):
        raise ValueError(
            f"timeframe_pairings must be a mapping, "
            f"got {type(pairings_raw).__name__}"
        )
    timeframe_pairings = {
        str(k): str(v) for k, v in pairings_raw.items()
    }

    # --- Build and validate ---
    return CRTConfig(
        enabled=bool(crt["enabled"]),
        min_parent_range_atr_multiple=float(
            crt["min_parent_range_atr_multiple"]
        ),
        require_confirmation_candle=bool(
            crt["require_confirmation_candle"]
        ),
        max_gap_tolerance_pips=_parse_decimal(
            crt["max_gap_tolerance_pips"], "max_gap_tolerance_pips"
        ),
        price_comparison_tolerance=_parse_decimal(
            crt["price_comparison_tolerance_pips"],
            "price_comparison_tolerance_pips",
        ),
        pip_size=_parse_decimal(crt["pip_size"], "pip_size"),
        kill_zones=tuple(kill_zones),
        allowed_timeframes=tuple(str(tf) for tf in crt["allowed_timeframes"]),
        timeframe_pairings=timeframe_pairings,
    )
