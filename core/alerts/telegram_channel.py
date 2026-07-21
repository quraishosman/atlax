"""
ATLAX Telegram Alert Channel.

Delivers alerts as formatted Telegram messages to a configured
bot and chat ID. Gracefully degrades if Telegram is unreachable —
delivery failure is logged but never raises an exception.

Authority: docs/03_ARCHITECTURE.md (alert delivery, graceful degradation)
           docs/16_SECURITY.md (bot_token must not be logged)

Message Format: HTML
    Uses Telegram's HTML parse mode for bold, italic, and monospace
    formatting. Falls back gracefully if formatting fails.
"""

from __future__ import annotations

import json
import logging
import time
from decimal import Decimal

from core.alerts.base import BaseAlertChannel
from core.models.alert_event import AlertEvent
from core.settings.alert_config import TelegramConfig

logger = logging.getLogger("atlax.alerts.telegram")


def _fmt(value: object, decimals: int = 5) -> str:
    """Format a Decimal or float for display."""
    if isinstance(value, Decimal):
        return f"{float(value):.{decimals}f}"
    if isinstance(value, float):
        return f"{value:.{decimals}f}"
    return str(value)


def _direction_emoji(direction: str) -> str:
    return "🟢 BUY" if direction == "BUY" else "🔴 SELL"


def _profile_emoji(profile: str) -> str:
    return {"scalper": "⚡", "day_trader": "📊", "swing_trader": "🌊"}.get(profile, "📌")


def _build_message(alert: AlertEvent) -> str:
    """
    Build a formatted Telegram HTML message for an AlertEvent.

    Keeps the message concise and scannable for a trader on mobile.
    """
    dir_label = _direction_emoji(alert.direction)
    prof_label = _profile_emoji(alert.profile)
    score_bar = _score_bar(alert.confidence_score)
    missing_note = ""
    if alert.missing_factors:
        missing_note = (
            f"\n⚠️ <i>Missing: {', '.join(alert.missing_factors)}</i>"
        )

    return (
        f"🔔 <b>ATLAX CRT ALERT</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"<b>{alert.symbol}</b> {alert.timeframe} | {prof_label} {alert.profile.replace('_', ' ').title()}\n"
        f"<b>Direction:</b> {dir_label}\n"
        f"<b>Confidence:</b> {alert.confidence_score:.1f}/100 {score_bar}\n"
        f"{missing_note}\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"📐 <b>Levels</b>\n"
        f"  CRH: <code>{_fmt(alert.crt_high)}</code>\n"
        f"  CRL: <code>{_fmt(alert.crt_low)}</code>\n"
        f"\n"
        f"  Entry Zone:  <code>{_fmt(alert.entry_zone_low)}</code> – <code>{_fmt(alert.entry_zone_high)}</code>\n"
        f"  Invalidation: <code>{_fmt(alert.invalidation_level)}</code> ❌\n"
        f"\n"
        f"  T1 (Midpoint): <code>{_fmt(alert.midpoint_target)}</code> 🎯\n"
        f"  T2 (Extreme):  <code>{_fmt(alert.opposite_extreme_target)}</code> 🎯\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"🆔 <code>{alert.candidate_id[:16]}...</code>\n"
        f"🕐 <code>{alert.created_at.strftime('%Y-%m-%d %H:%M UTC')}</code>"
    )


def _score_bar(score: float) -> str:
    """Visual score bar using filled/empty blocks."""
    filled = round(score / 10)
    return "█" * filled + "░" * (10 - filled)


class TelegramAlertChannel(BaseAlertChannel):
    """
    Telegram bot alert channel.

    Sends formatted HTML messages to a Telegram chat or channel.
    Retries on transient failures. Fails gracefully — never raises.

    Credentials are never logged (docs/16_SECURITY.md).
    """

    _TELEGRAM_API_BASE = "https://api.telegram.org/bot"

    def __init__(self, config: TelegramConfig) -> None:
        self._config = config

    @property
    def name(self) -> str:
        return "telegram"

    @property
    def is_enabled(self) -> bool:
        return self._config.enabled

    def send(self, alert: AlertEvent) -> bool:
        """
        Send an alert to Telegram.

        Retries up to config.retry_attempts times on failure.
        Returns True on success, False on all failure modes.
        Never raises.

        Args:
            alert: The AlertEvent to deliver.

        Returns:
            True if the message was accepted by Telegram, False otherwise.
        """
        if not self._config.enabled:
            return False

        try:
            import urllib.request
            import urllib.parse
            import urllib.error
        except ImportError:
            logger.error(
                '{"event":"telegram_send_error","reason":"urllib not available",'
                '"alert_id":"%s"}', alert.alert_id
            )
            return False

        message = _build_message(alert)
        url = (
            f"{self._TELEGRAM_API_BASE}"
            f"{self._config.bot_token}"  # token not logged
            f"/sendMessage"
        )
        data = urllib.parse.urlencode({
            "chat_id": self._config.chat_id,
            "text": message,
            "parse_mode": self._config.parse_mode,
        }).encode("utf-8")

        for attempt in range(1, self._config.retry_attempts + 1):
            try:
                req = urllib.request.Request(
                    url, data=data, method="POST",
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                )
                with urllib.request.urlopen(
                    req, timeout=self._config.timeout_seconds
                ) as resp:
                    body = json.loads(resp.read().decode("utf-8"))
                    if body.get("ok"):
                        logger.info(
                            '{"event":"telegram_sent","alert_id":"%s",'
                            '"symbol":"%s","timeframe":"%s","attempt":%d}',
                            alert.alert_id, alert.symbol, alert.timeframe, attempt,
                        )
                        return True
                    else:
                        logger.warning(
                            '{"event":"telegram_rejected","alert_id":"%s",'
                            '"description":"%s","attempt":%d}',
                            alert.alert_id,
                            body.get("description", "unknown"),
                            attempt,
                        )
                        return False  # API rejection — no point retrying

            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    '{"event":"telegram_send_failed","alert_id":"%s",'
                    '"attempt":%d,"error":"%s"}',
                    alert.alert_id, attempt, str(exc)[:120],
                )
                if attempt < self._config.retry_attempts:
                    time.sleep(1.0 * attempt)

        logger.error(
            '{"event":"telegram_all_retries_failed","alert_id":"%s",'
            '"symbol":"%s","timeframe":"%s"}',
            alert.alert_id, alert.symbol, alert.timeframe,
        )
        return False
