"""Validated configuration for the ATLAX reliability watchdog."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


def _positive_int(value: Any, field_name: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{field_name} must be an integer")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be an integer") from exc
    if parsed <= 0:
        raise ValueError(f"{field_name} must be greater than zero")
    return parsed


@dataclass(frozen=True)
class WatchdogConfig:
    """Operational thresholds; this config never changes trading policy."""

    enabled: bool
    heartbeat_interval_seconds: int
    failure_threshold: int
    notification_cooldown_seconds: int
    notification_ttl_seconds: int
    market_data_stale_after_seconds: int
    telegram_stale_after_seconds: int
    config_path: str = "config/atlax.yaml"
    repeat_notifications: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.enabled, bool):
            raise ValueError("watchdog.enabled must be boolean")
        if not isinstance(self.repeat_notifications, bool):
            raise ValueError("watchdog.repeat_notifications must be boolean")
        for field_name in (
            "heartbeat_interval_seconds",
            "failure_threshold",
            "notification_cooldown_seconds",
            "notification_ttl_seconds",
            "market_data_stale_after_seconds",
            "telegram_stale_after_seconds",
        ):
            _positive_int(getattr(self, field_name), f"watchdog.{field_name}")
        if not str(self.config_path).strip():
            raise ValueError("watchdog.config_path must not be empty")


def load_watchdog_config(config_path: str | Path) -> WatchdogConfig:
    """Load the complete watchdog section; missing values fail closed."""
    path = Path(config_path)
    with path.open("r", encoding="utf-8") as handle:
        raw = yaml.safe_load(handle) or {}
    section = raw.get("watchdog")
    if not isinstance(section, dict):
        raise ValueError("Configuration must contain a 'watchdog' mapping")
    required = (
        "enabled",
        "heartbeat_interval_seconds",
        "failure_threshold",
        "notification_cooldown_seconds",
        "notification_ttl_seconds",
        "market_data_stale_after_seconds",
        "telegram_stale_after_seconds",
    )
    missing = [name for name in required if name not in section]
    if missing:
        raise ValueError(
            "Missing required watchdog fields: " + ", ".join(missing)
        )
    if not isinstance(section["enabled"], bool):
        raise ValueError("watchdog.enabled must be boolean")
    if not isinstance(section.get("repeat_notifications", False), bool):
        raise ValueError("watchdog.repeat_notifications must be boolean")
    return WatchdogConfig(
        enabled=section["enabled"],
        heartbeat_interval_seconds=_positive_int(
            section["heartbeat_interval_seconds"],
            "watchdog.heartbeat_interval_seconds",
        ),
        failure_threshold=_positive_int(
            section["failure_threshold"],
            "watchdog.failure_threshold",
        ),
        notification_cooldown_seconds=_positive_int(
            section["notification_cooldown_seconds"],
            "watchdog.notification_cooldown_seconds",
        ),
        notification_ttl_seconds=_positive_int(
            section["notification_ttl_seconds"],
            "watchdog.notification_ttl_seconds",
        ),
        market_data_stale_after_seconds=_positive_int(
            section["market_data_stale_after_seconds"],
            "watchdog.market_data_stale_after_seconds",
        ),
        telegram_stale_after_seconds=_positive_int(
            section["telegram_stale_after_seconds"],
            "watchdog.telegram_stale_after_seconds",
        ),
        config_path=str(section.get("config_path", path)),
        repeat_notifications=section.get("repeat_notifications", False),
    )


__all__ = ["WatchdogConfig", "load_watchdog_config"]
