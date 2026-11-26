"""
ATLAX Execution Engine Configuration Schema and Loader.

Authority: docs/11_EXECUTION_ENGINE.md
           docs/13_CONFIGURATION.md
           docs/16_SECURITY.md (credentials via env vars only)

Fail-closed contract:
    - Missing required fields raise ValueError immediately.
    - Credentials must be supplied via environment variables.
      Any credential found hardcoded in YAML must be rejected.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

import yaml


@dataclass(frozen=True)
class ExecutionConfig:
    """
    Configuration for the Execution Engine.

    Authority: docs/11_EXECUTION_ENGINE.md
               docs/13_CONFIGURATION.md
               docs/16_SECURITY.md

    Attributes:
        enabled:             Master toggle. If False, all execution is refused.
        require_alert_eligibility: Require a successful Telegram alert route
                             before an MT5 entry request may be created.
        magic_number:        MT5 magic number that identifies ATLAX orders.
                             Must be unique to ATLAX (not shared with manual trades).
        deviation_points:    Maximum price slippage allowed in MT5 points.
        order_filling_mode:  MT5 ORDER_FILLING constant name.
                     Allowed: "FOK" (Fill or Kill), "IOC" (Immediate or Cancel),
                     or "RETURN" (Return).
        use_market_price:    If True, send market orders (price=0 + ask/bid).
                             If False, send limit orders at entry_zone midpoint.
        max_retries:         Number of retry attempts on transient MT5 errors.
        retry_delay_seconds: Seconds to wait between retries.
        comment_prefix:      Prefix for MT5 order comments (sanitized candidate ref).
                             Must not contain credentials. Max 31 chars (MT5 limit).
    """
    enabled:                  bool
    require_alert_eligibility: bool
    magic_number:             int
    deviation_points:         int
    order_filling_mode:       str
    use_market_price:         bool
    max_retries:              int
    retry_delay_seconds:      float
    comment_prefix:           str
    partial_profit_enabled:   bool
    partial_profit_ratio:     float
    move_sl_to_breakeven:     bool

    _ALLOWED_FILLING_MODES = frozenset({"FOK", "IOC", "RETURN"})

    def __post_init__(self) -> None:
        if self.magic_number <= 0:
            raise ValueError(
                f"execution.magic_number must be a positive integer, "
                f"got {self.magic_number}"
            )
        if self.deviation_points < 0:
            raise ValueError(
                f"execution.deviation_points must be >= 0, "
                f"got {self.deviation_points}"
            )
        if self.order_filling_mode not in self._ALLOWED_FILLING_MODES:
            raise ValueError(
                f"execution.order_filling_mode must be one of "
                f"{sorted(self._ALLOWED_FILLING_MODES)}, "
                f"got {self.order_filling_mode!r}"
            )
        if self.max_retries < 0:
            raise ValueError(
                f"execution.max_retries must be >= 0, got {self.max_retries}"
            )
        if self.retry_delay_seconds < 0:
            raise ValueError(
                f"execution.retry_delay_seconds must be >= 0, "
                f"got {self.retry_delay_seconds}"
            )
        if len(self.comment_prefix) > 20:
            raise ValueError(
                f"execution.comment_prefix must be <= 20 chars "
                f"(MT5 comment max is 31, we reserve 11 for candidate ID suffix), "
                f"got {len(self.comment_prefix)} chars"
            )


def load_execution_config(config_path: str) -> ExecutionConfig:
    """
    Load and validate ExecutionConfig from a YAML file.

    Fail-closed: raises ValueError for any missing or invalid field.

    Authority: docs/13_CONFIGURATION.md, docs/11_EXECUTION_ENGINE.md

    Args:
        config_path: Path to the YAML config file.

    Returns:
        A validated, immutable ExecutionConfig instance.
    """
    with open(config_path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    exec_raw = raw.get("execution", {})
    if not exec_raw:
        raise ValueError(
            f"Execution configuration ('execution:' section) is missing from "
            f"{config_path}. Cannot start without execution config. "
            f"Authority: docs/11_EXECUTION_ENGINE.md"
        )

    try:
        config = ExecutionConfig(
            enabled=bool(exec_raw.get("enabled", True)),
            require_alert_eligibility=bool(
                exec_raw.get("require_alert_eligibility", True)
            ),
            magic_number=int(exec_raw["magic_number"]),
            deviation_points=int(exec_raw.get("deviation_points", 20)),
            order_filling_mode=str(exec_raw.get("order_filling_mode", "FOK")),
            use_market_price=bool(exec_raw.get("use_market_price", True)),
            max_retries=int(exec_raw.get("max_retries", 2)),
            retry_delay_seconds=float(exec_raw.get("retry_delay_seconds", 1.0)),
            comment_prefix=str(exec_raw.get("comment_prefix", "ATLAX")),
            partial_profit_enabled=bool(exec_raw.get("partial_profit_enabled", True)),
            partial_profit_ratio=float(exec_raw.get("partial_profit_ratio", 0.5)),
            move_sl_to_breakeven=bool(exec_raw.get("move_sl_to_breakeven", True)),
        )
    except (KeyError, TypeError, ValueError) as e:
        raise ValueError(f"Execution configuration validation failed: {e}") from e

    return config
