"""
ATLAX Alert Engine Configuration Schema and Loader.

Defines AlertConfig loaded from config/atlax.yaml.
All alert routing, deduplication, and threshold settings flow through here.

Authority: docs/03_ARCHITECTURE.md (Alert Deduplication, Profile-Aware Routing)
           docs/13_CONFIGURATION.md (all settings must be configurable)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import yaml


@dataclass(frozen=True)
class TelegramConfig:
    """
    Telegram bot delivery configuration.

    Authority: docs/03_ARCHITECTURE.md (alert delivery channel)

    Attributes:
        enabled: Master toggle for Telegram alerts.
        bot_token: Telegram bot API token. Never logged or exposed.
        chat_id: Target chat or channel ID.
        parse_mode: Telegram message parse mode ("HTML" or "Markdown").
        timeout_seconds: HTTP request timeout for Telegram API calls.
        retry_attempts: How many times to retry a failed send before giving up.
    """
    enabled: bool
    bot_token: str
    chat_id: str
    parse_mode: str
    timeout_seconds: float
    retry_attempts: int

    def __post_init__(self) -> None:
        if self.enabled:
            if not self.bot_token or self.bot_token.strip() == "":
                raise ValueError(
                    "Telegram is enabled but bot_token is empty. "
                    "Set alerts.telegram.bot_token in atlax.yaml."
                )
            if not self.chat_id or self.chat_id.strip() == "":
                raise ValueError(
                    "Telegram is enabled but chat_id is empty. "
                    "Set alerts.telegram.chat_id in atlax.yaml."
                )
        if self.parse_mode not in ("HTML", "Markdown"):
            raise ValueError(
                f"parse_mode must be 'HTML' or 'Markdown', got {self.parse_mode!r}"
            )


@dataclass(frozen=True)
class AlertConfig:
    """
    Configuration for the Alert Engine.

    Loaded from the 'alerts:' section of config/atlax.yaml.
    Fail-closed: any invalid value raises ValueError immediately.

    Authority: docs/03_ARCHITECTURE.md (Alert Deduplication)
               docs/13_CONFIGURATION.md

    Attributes:
        enabled: Master toggle for the alert engine.
        log_channel_enabled: Always-on structured JSON log channel.
        telegram: Telegram channel configuration.
        deduplication_window_seconds: Minimum seconds between alerts for the
            same (symbol, timeframe, direction) combination. Prevents spam.
            Must be > 0. Set to 0 to disable deduplication.
        suppress_below_threshold: If True, suppress alerts where the
            confidence score is below the profile's min_confidence_threshold.
            If False, all trade candidates produce alerts regardless of score.
    """
    enabled: bool
    log_channel_enabled: bool
    telegram: TelegramConfig
    deduplication_window_seconds: int
    suppress_below_threshold: bool

    def __post_init__(self) -> None:
        if self.deduplication_window_seconds < 0:
            raise ValueError(
                f"deduplication_window_seconds must be >= 0, "
                f"got {self.deduplication_window_seconds}"
            )


def load_alert_config(config_path: str) -> AlertConfig:
    """
    Load and validate AlertConfig from a YAML file.

    Fail-closed: raises ValueError for any invalid configuration.
    Telegram credentials are read from YAML; for production, use
    environment variable overrides documented in docs/16_SECURITY.md.

    Authority: docs/13_CONFIGURATION.md

    Args:
        config_path: Path to the YAML config file.

    Returns:
        A validated, immutable AlertConfig instance.
    """
    import os

    with open(config_path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    alerts_raw = raw.get("alerts", {})
    if not alerts_raw:
        raise ValueError(
            f"Alert configuration ('alerts:' section) is missing "
            f"from {config_path}."
        )

    tg_raw = alerts_raw.get("telegram", {})

    # Allow env-var override for secrets (docs/16_SECURITY.md)
    bot_token = os.environ.get("ATLAX_TELEGRAM_BOT_TOKEN",
                               str(tg_raw.get("bot_token", "")))
    chat_id = os.environ.get("ATLAX_TELEGRAM_CHAT_ID",
                             str(tg_raw.get("chat_id", "")))

    try:
        telegram = TelegramConfig(
            enabled=bool(tg_raw.get("enabled", False)),
            bot_token=bot_token,
            chat_id=chat_id,
            parse_mode=str(tg_raw.get("parse_mode", "HTML")),
            timeout_seconds=float(tg_raw.get("timeout_seconds", 10.0)),
            retry_attempts=int(tg_raw.get("retry_attempts", 2)),
        )

        config = AlertConfig(
            enabled=bool(alerts_raw.get("enabled", True)),
            log_channel_enabled=bool(alerts_raw.get("log_channel_enabled", True)),
            telegram=telegram,
            deduplication_window_seconds=int(
                alerts_raw.get("deduplication_window_seconds", 300)
            ),
            suppress_below_threshold=bool(
                alerts_raw.get("suppress_below_threshold", True)
            ),
        )
    except (KeyError, TypeError, ValueError) as e:
        raise ValueError(f"Alert configuration validation failed: {e}") from e

    return config
