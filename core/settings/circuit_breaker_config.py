"""Validated configuration for the new-entry circuit breaker."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


def _bool(value: Any, field_name: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{field_name} must be boolean")
    return value


@dataclass(frozen=True)
class CircuitBreakerConfig:
    """Explicit safety gates applied before a new entry is approved."""

    enabled: bool
    block_on_mt5_unavailable: bool
    block_on_mt5_trading_disabled: bool
    block_on_prop_firm_unknown: bool
    block_on_loss_limits: bool
    block_on_storage_failure: bool
    notify_admin_on_transition: bool
    block_on_reconciliation_uncertain: bool = True

    def __post_init__(self) -> None:
        for field_name in (
            "enabled",
            "block_on_mt5_unavailable",
            "block_on_mt5_trading_disabled",
            "block_on_prop_firm_unknown",
            "block_on_loss_limits",
            "block_on_storage_failure",
            "notify_admin_on_transition",
            "block_on_reconciliation_uncertain",
        ):
            if not isinstance(getattr(self, field_name), bool):
                raise ValueError(f"circuit_breaker.{field_name} must be boolean")


def load_circuit_breaker_config(config_path: str | Path) -> CircuitBreakerConfig:
    """Load the circuit-breaker section; missing configuration fails closed."""

    path = Path(config_path)
    with path.open("r", encoding="utf-8") as handle:
        raw = yaml.safe_load(handle) or {}
    section = raw.get("circuit_breaker")
    if not isinstance(section, dict):
        raise ValueError(
            "Configuration must contain a 'circuit_breaker' mapping"
        )
    required = (
        "enabled",
        "block_on_mt5_unavailable",
        "block_on_mt5_trading_disabled",
        "block_on_prop_firm_unknown",
        "block_on_loss_limits",
        "block_on_storage_failure",
        "notify_admin_on_transition",
        "block_on_reconciliation_uncertain",
    )
    missing = [name for name in required if name not in section]
    if missing:
        raise ValueError(
            "Missing required circuit_breaker fields: " + ", ".join(missing)
        )
    return CircuitBreakerConfig(
        enabled=_bool(section["enabled"], "circuit_breaker.enabled"),
        block_on_mt5_unavailable=_bool(
            section["block_on_mt5_unavailable"],
            "circuit_breaker.block_on_mt5_unavailable",
        ),
        block_on_mt5_trading_disabled=_bool(
            section["block_on_mt5_trading_disabled"],
            "circuit_breaker.block_on_mt5_trading_disabled",
        ),
        block_on_prop_firm_unknown=_bool(
            section["block_on_prop_firm_unknown"],
            "circuit_breaker.block_on_prop_firm_unknown",
        ),
        block_on_loss_limits=_bool(
            section["block_on_loss_limits"],
            "circuit_breaker.block_on_loss_limits",
        ),
        block_on_storage_failure=_bool(
            section["block_on_storage_failure"],
            "circuit_breaker.block_on_storage_failure",
        ),
        notify_admin_on_transition=_bool(
            section["notify_admin_on_transition"],
            "circuit_breaker.notify_admin_on_transition",
        ),
        block_on_reconciliation_uncertain=_bool(
            section["block_on_reconciliation_uncertain"],
            "circuit_breaker.block_on_reconciliation_uncertain",
        ),
    )
