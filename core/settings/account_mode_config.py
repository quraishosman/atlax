"""Validated configuration for the account-mode runtime adapter."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Optional

import yaml

from core.settings.prop_firm_config import PropFirmRecoveryConfig


def _optional_decimal(value: Any, field_name: str) -> Optional[Decimal]:
    if value is None or value == "":
        return None
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be a finite decimal or null") from exc
    if not result.is_finite() or result <= 0 or result > 1:
        raise ValueError(f"{field_name} must be in the range (0, 1]")
    return result


@dataclass(frozen=True)
class AccountModeConfig:
    """Operational inputs required to construct a live Prop-Firm context."""

    enabled: bool = False
    auto_enforce_when_bound: bool = False
    context_max_age_seconds: int = 30
    max_profile_age_hours: float = 168.0
    max_open_risk_fraction: Optional[Decimal] = None
    new_trade_buffer_fraction: Optional[Decimal] = None
    cost_reserve: Decimal = Decimal("0")
    transaction_cost_reserve: Decimal = Decimal("0")
    slippage_reserve: Decimal = Decimal("0")
    terminal_instance_id: Optional[str] = None
    recovery_config: Optional[PropFirmRecoveryConfig] = None

    def __post_init__(self) -> None:
        if not isinstance(self.enabled, bool):
            raise ValueError("account_mode.enabled must be boolean")
        if not isinstance(self.auto_enforce_when_bound, bool):
            raise ValueError("account_mode.auto_enforce_when_bound must be boolean")
        if self.context_max_age_seconds <= 0:
            raise ValueError("account_mode.context_max_age_seconds must be positive")
        if self.max_profile_age_hours <= 0:
            raise ValueError("account_mode.max_profile_age_hours must be positive")
        for name in ("cost_reserve", "transaction_cost_reserve", "slippage_reserve"):
            value = getattr(self, name)
            if not isinstance(value, Decimal) or not value.is_finite() or value < 0:
                raise ValueError(f"{name} must be a non-negative finite decimal")
        if self.enabled and (
            self.max_open_risk_fraction is None
            or self.new_trade_buffer_fraction is None
        ):
            raise ValueError(
                "Enabled account_mode requires max_open_risk_fraction and "
                "new_trade_buffer_fraction"
            )


def load_account_mode_config(config_path: str | Path) -> AccountModeConfig:
    """Load account-mode settings; absent settings safely mean Normal mode."""

    path = Path(config_path)
    with path.open("r", encoding="utf-8") as handle:
        raw = yaml.safe_load(handle) or {}
    section = raw.get("account_mode") or {}
    if not isinstance(section, dict):
        raise ValueError("account_mode must be a mapping")
    recovery_config = None
    recovery = section.get("recovery")
    if recovery is None:
        prop_firm_section = raw.get("prop_firm_mode") or {}
        if not isinstance(prop_firm_section, dict):
            raise ValueError("prop_firm_mode must be a mapping")
        recovery = prop_firm_section.get("recovery")
    if recovery is not None:
        if not isinstance(recovery, dict):
            raise ValueError("Prop-Firm recovery configuration must be a mapping")
        recovery_values = dict(recovery)
        # The live configuration previously used a *_fraction spelling for
        # this absolute-currency cushion. Accept it only as an explicit alias
        # so the validated model remains the single source of truth.
        if (
            "restore_profit_cushion" not in recovery_values
            and "restore_profit_cushion_fraction" in recovery_values
        ):
            recovery_values["restore_profit_cushion"] = recovery_values.pop(
                "restore_profit_cushion_fraction"
            )
        recovery_config = PropFirmRecoveryConfig.from_mapping(recovery_values)
    return AccountModeConfig(
        enabled=bool(section.get("enabled", False)),
        auto_enforce_when_bound=bool(section.get("auto_enforce_when_bound", False)),
        context_max_age_seconds=int(section.get("context_max_age_seconds", 30)),
        max_profile_age_hours=float(section.get("max_profile_age_hours", 168)),
        max_open_risk_fraction=_optional_decimal(
            section.get("max_open_risk_fraction"),
            "account_mode.max_open_risk_fraction",
        ),
        new_trade_buffer_fraction=_optional_decimal(
            section.get("new_trade_buffer_fraction"),
            "account_mode.new_trade_buffer_fraction",
        ),
        cost_reserve=Decimal(str(section.get("cost_reserve", "0"))),
        transaction_cost_reserve=Decimal(str(section.get("transaction_cost_reserve", "0"))),
        slippage_reserve=Decimal(str(section.get("slippage_reserve", "0"))),
        terminal_instance_id=(
            str(section["terminal_instance_id"]).strip()
            if section.get("terminal_instance_id")
            else None
        ),
        recovery_config=recovery_config,
    )
