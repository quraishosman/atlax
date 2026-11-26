"""
ATLAX Risk Engine Configuration Schema and Loader.

Defines RiskConfig and ProfileRiskConfig dataclasses loaded from
the 'risk:' section of config/atlax.yaml.

All risk thresholds, lot-size parameters, and exposure limits must
flow through this config. Nothing may be hardcoded in the engine.

Authority: docs/10_RISK_ENGINE.md
           docs/13_CONFIGURATION.md

Fail-closed contract:
    - Any missing required field raises ValueError immediately.
    - Any out-of-range value raises ValueError immediately.
    - The engine must refuse to operate with an invalid RiskConfig.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Optional

import yaml


@dataclass(frozen=True)
class ProfileRiskConfig:
    """
    Risk parameters for a single trader profile.

    Authority: docs/10_RISK_ENGINE.md (profile-specific limits)

    Attributes:
        profile_name:         Must match a StrategyConfig profile name.
        risk_per_trade_pct:   Percentage of account equity risked per trade.
                              e.g. 1.0 = 1%. Range: (0, 10].
        max_open_trades:      Maximum open positions allowed for this profile.
        max_trades_per_day:   Maximum new trades opened in a calendar day.
        max_correlated_pairs: Maximum open positions sharing the same base or
                              quote currency (e.g., EURUSD + GBPUSD both EUR-tied
                              pairs count toward the EUR correlation limit).
    """
    profile_name: str
    risk_per_trade_pct: float
    max_open_trades: int
    max_trades_per_day: int
    max_correlated_pairs: int

    def __post_init__(self) -> None:
        valid_profiles = ("scalper", "day_trader", "swing_trader")
        if self.profile_name not in valid_profiles:
            raise ValueError(
                f"ProfileRiskConfig.profile_name must be one of {valid_profiles}, "
                f"got {self.profile_name!r}"
            )
        if not (0 < self.risk_per_trade_pct <= 10.0):
            raise ValueError(
                f"risk_per_trade_pct must be in range (0, 10], "
                f"got {self.risk_per_trade_pct} for profile {self.profile_name!r}"
            )
        if self.max_open_trades < 1:
            raise ValueError(
                f"max_open_trades must be >= 1, got {self.max_open_trades}"
            )
        if self.max_trades_per_day < 1:
            raise ValueError(
                f"max_trades_per_day must be >= 1, got {self.max_trades_per_day}"
            )
        if self.max_correlated_pairs < 1:
            raise ValueError(
                f"max_correlated_pairs must be >= 1, got {self.max_correlated_pairs}"
            )


@dataclass(frozen=True)
class AdaptiveLotSizingConfig:
    """Validated fixed-volume ladder for non-excluded instruments.

    The selected volume is always a target, never an authorization to exceed
    the broker-derived risk-safe volume.
    """

    enabled: bool
    excluded_symbol_prefixes: tuple[str, ...]
    normal_lot: float
    caution_daily_loss_pct: float
    caution_lot: float
    recovery_daily_loss_pct: float
    recovery_lot: float

    def __post_init__(self) -> None:
        if not isinstance(self.enabled, bool):
            raise ValueError("adaptive_lot_sizing.enabled must be a boolean")
        if not self.excluded_symbol_prefixes or any(
            not isinstance(prefix, str) or not prefix.strip()
            for prefix in self.excluded_symbol_prefixes
        ):
            raise ValueError(
                "adaptive_lot_sizing.excluded_symbol_prefixes must contain "
                "non-empty symbol prefixes"
            )
        if not (0 < self.caution_daily_loss_pct < self.recovery_daily_loss_pct):
            raise ValueError(
                "adaptive lot daily-loss thresholds must satisfy "
                "0 < caution_daily_loss_pct < recovery_daily_loss_pct"
            )
        if min(self.normal_lot, self.caution_lot, self.recovery_lot) <= 0:
            raise ValueError("adaptive lot targets must be positive")
        if not self.recovery_lot <= self.caution_lot <= self.normal_lot:
            raise ValueError(
                "adaptive lot targets must not increase after daily losses"
            )
        if not all(
            math.isfinite(value)
            for value in (
                self.normal_lot,
                self.caution_daily_loss_pct,
                self.caution_lot,
                self.recovery_daily_loss_pct,
                self.recovery_lot,
            )
        ):
            raise ValueError("adaptive lot values must be finite")


@dataclass(frozen=True)
class RiskConfig:
    """
    Global risk configuration for the ATLAX Risk Engine.

    Authority: docs/10_RISK_ENGINE.md

    Attributes:
        enabled:                 Master toggle. If False, the Risk Engine
                                 rejects all candidates (fail-closed on disable).
        max_daily_loss_pct:      Maximum allowable net loss for the trading day
                                 as a percentage of account balance.
                                 e.g. 3.0 = stop trading if -3% daily loss hit.
        max_weekly_loss_pct:     Maximum allowable net loss for the trading week.
        pip_value_per_lot:       Value of 1 pip per standard lot in deposit
                                 currency. Used for lot-size calculation.
                                 e.g. EURUSD = 10.0 USD, XAUUSD = 10.0 USD.
                                 This is a simplified global value; a future
                                 version may allow per-symbol overrides.
        contract_size:           Standard lot contract size (units of base currency).
                                 Typically 100000 for forex, 100 for gold.
        min_lot:                 Broker minimum lot size.
        max_lot:                 Broker maximum lot size.
        lot_step:                Broker lot rounding step (e.g. 0.01).
        profiles:                Per-profile risk parameters. Must contain
                                 an entry for every active strategy profile.
    """
    enabled: bool
    max_daily_loss_pct: float
    max_weekly_loss_pct: float
    pip_value_per_lot: float
    contract_size: float
    min_lot: float
    max_lot: float
    lot_step: float
    profiles: tuple[ProfileRiskConfig, ...]
    adaptive_lot_sizing: Optional[AdaptiveLotSizingConfig] = None

    def __post_init__(self) -> None:
        if not (0 < self.max_daily_loss_pct <= 50.0):
            raise ValueError(
                f"max_daily_loss_pct must be in range (0, 50], "
                f"got {self.max_daily_loss_pct}"
            )
        if not (0 < self.max_weekly_loss_pct <= 50.0):
            raise ValueError(
                f"max_weekly_loss_pct must be in range (0, 50], "
                f"got {self.max_weekly_loss_pct}"
            )
        if self.max_weekly_loss_pct < self.max_daily_loss_pct:
            raise ValueError(
                f"max_weekly_loss_pct ({self.max_weekly_loss_pct}) must be >= "
                f"max_daily_loss_pct ({self.max_daily_loss_pct})"
            )
        if self.pip_value_per_lot <= 0:
            raise ValueError(
                f"pip_value_per_lot must be > 0, got {self.pip_value_per_lot}"
            )
        if self.contract_size <= 0:
            raise ValueError(
                f"contract_size must be > 0, got {self.contract_size}"
            )
        if not (0 < self.min_lot <= self.max_lot):
            raise ValueError(
                f"min_lot ({self.min_lot}) must be > 0 and <= max_lot ({self.max_lot})"
            )
        if self.lot_step <= 0:
            raise ValueError(
                f"lot_step must be > 0, got {self.lot_step}"
            )
        if not self.profiles:
            raise ValueError("RiskConfig must define at least one profile.")
        if self.adaptive_lot_sizing is not None and (
            self.adaptive_lot_sizing.recovery_daily_loss_pct
            > self.max_daily_loss_pct
        ):
            raise ValueError(
                "adaptive recovery_daily_loss_pct must not exceed max_daily_loss_pct"
            )

    def get_profile(self, name: str) -> Optional[ProfileRiskConfig]:
        """Return a ProfileRiskConfig by profile name, or None if not found."""
        for p in self.profiles:
            if p.profile_name == name:
                return p
        return None


def load_risk_config(config_path: str) -> RiskConfig:
    """
    Load and validate RiskConfig from a YAML file.

    Fail-closed: raises ValueError for any missing or invalid field.
    Raises FileNotFoundError if the config file does not exist.

    Authority: docs/13_CONFIGURATION.md
               docs/10_RISK_ENGINE.md

    Args:
        config_path: Path to the YAML config file (e.g., "config/atlax.yaml").

    Returns:
        A validated, immutable RiskConfig instance.
    """
    with open(config_path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    risk_raw = raw.get("risk", {})

    if not risk_raw:
        raise ValueError(
            f"Risk configuration ('risk:' section) is missing from "
            f"{config_path}. Cannot start without risk config. "
            f"Authority: docs/10_RISK_ENGINE.md"
        )

    profiles_raw = risk_raw.get("profiles", [])
    if not profiles_raw:
        raise ValueError("risk.profiles must define at least one profile.")

    profiles = []
    for p in profiles_raw:
        try:
            profiles.append(ProfileRiskConfig(
                profile_name=str(p["profile_name"]),
                risk_per_trade_pct=float(p["risk_per_trade_pct"]),
                max_open_trades=int(p["max_open_trades"]),
                max_trades_per_day=int(p["max_trades_per_day"]),
                max_correlated_pairs=int(p["max_correlated_pairs"]),
            ))
        except (KeyError, TypeError, ValueError) as e:
            raise ValueError(f"Invalid risk profile config: {e}") from e

    adaptive_raw = risk_raw.get("adaptive_lot_sizing")
    if not isinstance(adaptive_raw, dict):
        raise ValueError("risk.adaptive_lot_sizing must be a mapping.")
    excluded_prefixes = adaptive_raw.get("excluded_symbol_prefixes")
    if not isinstance(excluded_prefixes, (list, tuple)):
        raise ValueError(
            "risk.adaptive_lot_sizing.excluded_symbol_prefixes must be a list."
        )
    try:
        adaptive_lot_sizing = AdaptiveLotSizingConfig(
            enabled=adaptive_raw["enabled"],
            excluded_symbol_prefixes=tuple(
                str(prefix).strip().upper()
                for prefix in excluded_prefixes
            ),
            normal_lot=float(adaptive_raw["normal_lot"]),
            caution_daily_loss_pct=float(adaptive_raw["caution_daily_loss_pct"]),
            caution_lot=float(adaptive_raw["caution_lot"]),
            recovery_daily_loss_pct=float(adaptive_raw["recovery_daily_loss_pct"]),
            recovery_lot=float(adaptive_raw["recovery_lot"]),
        )
    except (KeyError, TypeError, ValueError) as e:
        raise ValueError(f"Invalid adaptive lot sizing config: {e}") from e

    try:
        config = RiskConfig(
            enabled=bool(risk_raw.get("enabled", True)),
            max_daily_loss_pct=float(risk_raw["max_daily_loss_pct"]),
            max_weekly_loss_pct=float(risk_raw["max_weekly_loss_pct"]),
            pip_value_per_lot=float(risk_raw["pip_value_per_lot"]),
            contract_size=float(risk_raw["contract_size"]),
            min_lot=float(risk_raw["min_lot"]),
            max_lot=float(risk_raw["max_lot"]),
            lot_step=float(risk_raw["lot_step"]),
            profiles=tuple(profiles),
            adaptive_lot_sizing=adaptive_lot_sizing,
        )
    except (KeyError, TypeError, ValueError) as e:
        raise ValueError(f"Risk configuration validation failed: {e}") from e

    return config
