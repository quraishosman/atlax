"""
ATLAX Confidence Engine Configuration Schema and Loader.

Defines the ConfidenceConfig dataclass loaded from config/atlax.yaml.
All scoring weights must be configurable, documented, and sum to 100.

Authority: docs/09_CONFIDENCE_ENGINE.md, docs/13_CONFIGURATION.md

Proposed Default Weights (documented here as configuration, not trading rules):
    session_alignment      15.0  — Kill-zone timing is a strong quality signal
    sweep_distance         15.0  — How far price raided liquidity
    close_location         20.0  — Strongest single signal: how deep the recovery
    sweep_wick_ratio       10.0  — Aggressiveness of the liquidity grab
    htf_zone               15.0  — Parent at HTF structure is high confluence
    htf_trend_alignment    10.0  — Direction aligns with higher-timeframe bias
    fvg_present             5.0  — Fair Value Gap after sweep = added confluence
    msb_on_ltf             10.0  — Lower-timeframe MSB confirms the reversal
    ─────────────────────────────
    Total                 100.0
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

import yaml


@dataclass(frozen=True)
class ConfidenceConfig:
    """
    Configuration for the Confidence Engine scoring model.

    All weights must sum to exactly 100.0.
    Fail-closed: any invalid value raises ValueError immediately.

    Authority: docs/09_CONFIDENCE_ENGINE.md
        "All scoring weights must be configurable and documented."

    Attributes:
        version: Scoring model version string (e.g., "1.0.0").
        session_alignment_weight: Kill-zone timing score weight.
        sweep_distance_weight: Sweep pip-distance score weight.
        close_location_weight: Close-back position score weight.
        sweep_wick_ratio_weight: Sweep wick aggressiveness weight.
        htf_zone_weight: Parent-at-HTF-zone score weight.
        htf_trend_weight: HTF trend alignment score weight.
        fvg_weight: FVG-present-after-sweep score weight.
        msb_ltf_weight: LTF market-structure-break score weight.

        sweep_distance_ideal_min_pips: Minimum ideal sweep distance (full score).
        sweep_distance_ideal_max_pips: Maximum ideal sweep distance (full score).
        close_location_bull_ideal_min: Ideal minimum close_location_ratio for bullish.
        close_location_bear_ideal_max: Ideal maximum close_location_ratio for bearish.
        sweep_wick_ideal_min: Ideal minimum wick ratio (below = weak sweep).
        sweep_wick_ideal_max: Ideal maximum wick ratio (above = overextension).
    """

    version: str

    # Weights (must sum to 100.0)
    session_alignment_weight: float
    sweep_distance_weight: float
    close_location_weight: float
    sweep_wick_ratio_weight: float
    htf_zone_weight: float
    htf_trend_weight: float
    fvg_weight: float
    msb_ltf_weight: float

    # Scoring parameters for continuous factors
    sweep_distance_ideal_min_pips: float
    sweep_distance_ideal_max_pips: float
    close_location_bull_ideal_min: float   # bullish: CLR should be >= this
    close_location_bear_ideal_max: float   # bearish: CLR should be <= this
    sweep_wick_ideal_min: float
    sweep_wick_ideal_max: float

    def __post_init__(self) -> None:
        total = (
            self.session_alignment_weight
            + self.sweep_distance_weight
            + self.close_location_weight
            + self.sweep_wick_ratio_weight
            + self.htf_zone_weight
            + self.htf_trend_weight
            + self.fvg_weight
            + self.msb_ltf_weight
        )
        if abs(total - 100.0) > 0.01:
            raise ValueError(
                f"Confidence weights must sum to 100.0, got {total:.4f}. "
                f"Review the 'confidence:' section in atlax.yaml."
            )
        if self.sweep_distance_ideal_min_pips >= self.sweep_distance_ideal_max_pips:
            raise ValueError(
                "sweep_distance_ideal_min_pips must be < sweep_distance_ideal_max_pips"
            )
        if self.sweep_wick_ideal_min >= self.sweep_wick_ideal_max:
            raise ValueError(
                "sweep_wick_ideal_min must be < sweep_wick_ideal_max"
            )

    @property
    def snapshot_id(self) -> str:
        """
        Stable hash of the weight configuration.
        Used by ConfidenceScore.config_snapshot_id for audit traceability.
        """
        weights = {
            "version": self.version,
            "session_alignment": self.session_alignment_weight,
            "sweep_distance": self.sweep_distance_weight,
            "close_location": self.close_location_weight,
            "sweep_wick_ratio": self.sweep_wick_ratio_weight,
            "htf_zone": self.htf_zone_weight,
            "htf_trend": self.htf_trend_weight,
            "fvg": self.fvg_weight,
            "msb_ltf": self.msb_ltf_weight,
        }
        raw = json.dumps(weights, sort_keys=True)
        return hashlib.sha256(raw.encode()).hexdigest()[:12]


def load_confidence_config(config_path: str) -> ConfidenceConfig:
    """
    Load and validate ConfidenceConfig from a YAML file.

    Fail-closed: raises ValueError for any invalid configuration.

    Authority: docs/13_CONFIGURATION.md

    Args:
        config_path: Path to the YAML config file.

    Returns:
        A validated, immutable ConfidenceConfig instance.
    """
    with open(config_path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    conf_raw = raw.get("confidence", {})
    if not conf_raw:
        raise ValueError(
            f"Confidence configuration ('confidence:' section) is missing "
            f"from {config_path}."
        )

    weights = conf_raw.get("weights", {})
    params = conf_raw.get("scoring_params", {})

    try:
        config = ConfidenceConfig(
            version=str(conf_raw.get("version", "1.0.0")),
            session_alignment_weight=float(weights.get("session_alignment", 15.0)),
            sweep_distance_weight=float(weights.get("sweep_distance", 15.0)),
            close_location_weight=float(weights.get("close_location", 20.0)),
            sweep_wick_ratio_weight=float(weights.get("sweep_wick_ratio", 10.0)),
            htf_zone_weight=float(weights.get("htf_zone", 15.0)),
            htf_trend_weight=float(weights.get("htf_trend", 10.0)),
            fvg_weight=float(weights.get("fvg", 5.0)),
            msb_ltf_weight=float(weights.get("msb_ltf", 10.0)),
            sweep_distance_ideal_min_pips=float(params.get("sweep_distance_ideal_min_pips", 3.0)),
            sweep_distance_ideal_max_pips=float(params.get("sweep_distance_ideal_max_pips", 30.0)),
            close_location_bull_ideal_min=float(params.get("close_location_bull_ideal_min", 0.40)),
            close_location_bear_ideal_max=float(params.get("close_location_bear_ideal_max", 0.60)),
            sweep_wick_ideal_min=float(params.get("sweep_wick_ideal_min", 0.05)),
            sweep_wick_ideal_max=float(params.get("sweep_wick_ideal_max", 0.50)),
        )
    except (KeyError, TypeError, ValueError) as e:
        raise ValueError(f"Confidence configuration validation failed: {e}") from e

    return config
