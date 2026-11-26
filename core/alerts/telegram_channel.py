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
import math
import time
from datetime import datetime, timezone
from decimal import Decimal
from html import escape
from typing import Optional

from core.alerts.base import BaseAlertChannel
from core.alerts.i18n import localize_outbound, localize_text, normalize_language
from core.models.alert_event import AlertEvent
from core.settings.alert_config import TelegramConfig
from core.confidence.atr_quality import ATRQualityConfig
from core.alerts.subscription_entitlements import (
    atr_delivery_decision,
    has_premium_confluence_access,
    has_volume_profile_confluence_access,
    subscriber_plan,
)

logger = logging.getLogger("atlax.alerts.telegram")


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


def _has_volume_profile_confluence(alert: AlertEvent) -> bool:
    """Require a positive matched-level result for Premium VP routing."""
    if alert.volume_profile_confluence is not True:
        return False
    profiles = alert.volume_profiles or (
        (alert.volume_profile,) if alert.volume_profile is not None else ()
    )
    return any(
        str(getattr(profile.status, "value", profile.status)) == "VALID"
        for profile in profiles
    ) and bool(alert.volume_profile_matches)


def _build_message(alert: AlertEvent) -> str:
    """
    Build a clean, concise Telegram HTML message for an AlertEvent.
    Dynamic: symbol, timeframe, direction, levels and setup ID are never hardcoded.
    """
    is_buy = alert.direction == "BUY"
    dir_label = "🟢 BUY SETUP" if is_buy else "🔴 SELL SETUP"

    sl   = alert.invalidation_level
    tp1  = alert.midpoint_target
    tp2  = alert.opposite_extreme_target

    # Calculate R:R ratio dynamically
    try:
        entry = alert.entry_zone_low if is_buy else alert.entry_zone_high
        risk   = abs(float(entry) - float(sl))
        reward = abs(float(tp2) - float(entry))
        rr_ratio = f"1 : {reward / risk:.1f}" if risk > 0 else "N/A"
    except Exception:
        rr_ratio = "N/A"

    # Confluence strength bar
    score  = alert.confidence_score
    filled = round(score / 10)
    bar    = "█" * filled + "░" * (10 - filled)
    pct    = int(score)

    # Short setup ID (last 4 chars uppercased)
    setup_id = alert.candidate_id[-4:].upper()

    detected_at = alert.created_at.strftime("%Y-%m-%d %H:%M UTC")

    # Determine decimal places based on price magnitude
    try:
        ref_price = float(tp2)
        decimals = 3 if ref_price > 100 else 5
    except Exception:
        decimals = 5

    # --- VP Confluence display ---
    # Display the actual matched level metadata carried by the alert.
    vp_line = ""
    try:
        matches = tuple(alert.volume_profile_matches or ())
        if matches:
            match = matches[0]
            level_type = str(match.get("level_type", "Volume Profile"))
            matched_label = level_type.replace("_", " ").title()
            display_price = match.get("price", "—")
            vp_line = (
                f"\U0001f4ca VP MATCH  {escape(matched_label)} "
                f"<code>{_fmt(display_price, decimals)}</code>\n"
            )
        elif alert.volume_profiles or alert.volume_profile is not None:
            vp_line = "\U0001f4ca VP CONTEXT  \u2014 No level match\n"
    except Exception:
        pass

    return (
        f"\U0001f3af SETUP DETECTED\n"
        f"\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\n"
        f"\U0001f48e <b>{alert.symbol}</b> \u00b7 {alert.timeframe}\n"
        f"{dir_label}\n"
        f"\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\n"
        f"\U0001f4d0 <b>TRADE TARGETS</b>\n"
        f"\U0001f6d1 Stop      <code>{_fmt(sl, decimals)}</code>\n"
        f"\U0001f3c6 TP1       <code>{_fmt(tp1, decimals)}</code>\n"
        f"\U0001f3c6 TP2       <code>{_fmt(tp2, decimals)}</code>\n"
        f"\u2696\ufe0f R:R       {rr_ratio}\n"
        f"\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\n"
        f"\U0001f525 <b>CONFLUENCE</b>\n"
        f"Strength    {bar} {pct}%\n"
        f"{vp_line}"
        f"\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\n"
        f"\n"
        f"\u26a1\ufe0f ALERT 2 fires when price enters setup zone\n"
        f"\U0001f4cd Detected  <code>{detected_at}</code>\n"
        f"\U0001f516 Setup #{setup_id}"
    )


def _score_bar(score: float) -> str:
    """Visual score bar using filled/empty blocks."""
    filled = round(score / 10)
    return "\u2588" * filled + "\u2591" * (10 - filled)


def _subscriber_route_message(
    message: str,
    subscriber: dict,
    *,
    parent_at_htf_zone: Optional[bool] = None,
) -> tuple[str, str]:
    """Add the public plan marker to a message after routing eligibility passes."""
    route = subscriber_plan(subscriber)
    if route == "premium":
        plan_label = "\U00002b50 <b>PREMIUM SIGNAL</b>"
    else:
        plan_label = "\U0001f4e1 <b>STANDARD SIGNAL</b>"
    context_label = {
        True: "\U0001f3db\ufe0f <b>HTF KEY-LEVEL</b>",
        False: "\U0001f4cd <b>OPEN-STRUCTURE</b>",
    }.get(parent_at_htf_zone, "\u2754 <b>KEY-LEVEL UNKNOWN</b>")
    return f"{plan_label}\n{context_label}\n\n{message}", route


class TelegramAlertChannel(BaseAlertChannel):
    """
    Telegram bot alert channel.

    Sends formatted HTML messages to a Telegram chat or channel.
    Retries on transient failures. Fails gracefully — never raises.

    Credentials are never logged (docs/16_SECURITY.md).
    """

    _TELEGRAM_API_BASE = "https://api.telegram.org/bot"

    def __init__(
        self,
        config: TelegramConfig,
        subscriber_db=None,
        runtime_store=None,
        monitoring=None,
        google_sheets_webhook_url: str = "",
    ) -> None:
        self._config = config
        self._last_update_id = 0
        self._last_poll_failed = False
        self._last_poll_at: Optional[str] = None
        self._last_poll_success_at: Optional[str] = None
        self._last_delivery_at: Optional[str] = None
        self._last_delivery_failed_at: Optional[str] = None
        self._subscriber_db = subscriber_db
        self._runtime_store = runtime_store
        self._monitoring = monitoring
        self._google_sheets_webhook_url = google_sheets_webhook_url
        self.last_delivery_records: list[dict[str, object]] = []
        self.last_delivery_routes: set[str] = set()
        self.last_delivery_status = "not_attempted"

    @property
    def name(self) -> str:
        return "telegram"

    @property
    def is_enabled(self) -> bool:
        return self._config.enabled

    def _language_for_chat(self, chat_id: str) -> str:
        """Resolve the subscriber language without affecting admin output."""
        db = self._subscriber_db
        getter = getattr(db, "get_language", None)
        if callable(getter):
            try:
                return normalize_language(getter(chat_id))
            except Exception:  # noqa: BLE001
                return "en"
        return "en"

    def send(self, alert: AlertEvent) -> Optional[int]:
        """
        Send an alert to Telegram.

        Retries up to config.retry_attempts times on failure.
        Returns True on success, False on all failure modes.
        Never raises.

        Args:
            alert: The AlertEvent to deliver.

        Returns:
            The message_id (int) if the message was accepted by Telegram, None otherwise.
        """
        if not self._config.enabled:
            return None

        try:
            import urllib.request
            import urllib.parse
            import urllib.error
        except ImportError:
            logger.error(
                '{"event":"telegram_send_error","reason":"urllib not available",'
                '"alert_id":"%s"}', alert.alert_id
            )
            return None

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
                        self._last_delivery_at = datetime.now(timezone.utc).isoformat()
                        logger.info(
                            '{"event":"telegram_sent","alert_id":"%s",'
                            '"symbol":"%s","timeframe":"%s","attempt":%d,"message_id":%d}',
                            alert.alert_id, alert.symbol, alert.timeframe, attempt, body["result"]["message_id"]
                        )
                        return body["result"]["message_id"]
                    else:
                        logger.warning(
                            '{"event":"telegram_rejected","alert_id":"%s",'
                            '"description":"%s","attempt":%d}',
                            alert.alert_id,
                            body.get("description", "unknown"),
                            attempt,
                        )
                        return None  # API rejection — no point retrying

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
        self._last_delivery_failed_at = datetime.now(timezone.utc).isoformat()
        return None

    def broadcast_to_subscribers(
        self,
        alert: AlertEvent,
        confidence_score: Optional[float] = None,
        profile_min_confidence: Optional[float] = None,
        atr_quality_config: Optional[ATRQualityConfig] = None,
        quality_tightening: Optional[dict] = None,
    ) -> list[int]:
        """Broadcast an alert to eligible active subscribers.

        A missing subscriber override means "Profile Default". The Alert
        Engine supplies that configured profile threshold after its own gate;
        an explicitly invalid value still fails closed.
        """
        if not self._config.enabled:
            self.last_delivery_status = "suppressed"
            return []

        # Lazy-load subscriber DB
        if self._subscriber_db is None:
            from core.alerts.subscriber_db import SubscriberDB
            self._subscriber_db = SubscriberDB()

        self.last_delivery_records = []
        self.last_delivery_routes = set()
        self.last_delivery_status = "not_attempted"

        subscribers = self._subscriber_db.get_subscribers_for_timeframe(
            alert.timeframe,
            symbol=alert.symbol,
        )
        delivered_ids = []
        eligible_count = 0

        try:
            score = float(alert.confidence_score if confidence_score is None else confidence_score)
        except (TypeError, ValueError):
            score = math.nan

        if not math.isfinite(score):
            logger.warning(
                '{"event":"subscriber_broadcast_failed","alert_id":"%s",'
                '"reason":"invalid_confidence_score"}',
                alert.alert_id,
            )
            return []
        
        message = _build_message(alert)
        url = f"{self._TELEGRAM_API_BASE}{self._config.bot_token}/sendMessage"
        
        import urllib.request
        import urllib.parse
        
        for sub in subscribers:
            chat_id = str(sub.get("chat_id", ""))
            settings = sub.get("settings") if isinstance(sub.get("settings"), dict) else {}
            key_level_only = settings.get("key_level_only") is True
            confluence_only = settings.get("confluence_only") is True
            if confluence_only and not has_premium_confluence_access(sub, atr_quality_config):
                logger.info(
                    '{"event":"subscriber_delivery_suppressed",'
                    '"alert_id":"%s","chat_id":"%s",'
                    '"reason":"premium_confluence_entitlement",'
                    '"subscription_plan":"%s"}',
                    alert.alert_id,
                    chat_id[:80],
                    str(subscriber_plan(sub))[:40],
                )
                if self._monitoring is not None:
                    self._monitoring.observe_subscriber_suppressed(
                        "premium_confluence_entitlement"
                    )
                continue
            key_level_required = key_level_only or confluence_only
            if key_level_required and alert.parent_at_htf_zone is not True:
                suppression_reason = "confluence_key_level" if confluence_only else "key_level_only"
                logger.info(
                    '{"event":"subscriber_delivery_suppressed",'
                    '"alert_id":"%s","chat_id":"%s",'
                    '"reason":"%s","key_level_only":true,'
                    '"parent_at_htf_zone":"%s","subscription_plan":"%s"}',
                    alert.alert_id,
                    chat_id[:80],
                    suppression_reason,
                    str(alert.parent_at_htf_zone).upper(),
                    str(subscriber_plan(sub))[:40],
                )
                if self._monitoring is not None:
                    self._monitoring.observe_subscriber_suppressed(suppression_reason)
                continue
            high_quality_required = settings.get("high_quality_only") is True or confluence_only
            if high_quality_required:
                quality = alert.quality_tightening
                if (
                    quality is None
                    or quality.status != "VALID"
                    or quality.would_suppress
                ):
                    suppression_reason = "confluence_quality" if confluence_only else "high_quality_only"
                    logger.info(
                        '{"event":"subscriber_delivery_suppressed",'
                        '"alert_id":"%s","chat_id":"%s",'
                        '"reason":"%s",'
                        '"quality_status":"%s","would_suppress":%s}',
                        alert.alert_id,
                        chat_id[:80],
                        suppression_reason,
                        quality.status if quality else "UNKNOWN",
                        str(quality.would_suppress if quality else True).lower(),
                    )
                    if self._monitoring is not None:
                        self._monitoring.observe_subscriber_suppressed(suppression_reason)
                    continue
            # Volume Profile confluence preference (Doc 40 Phase 7).
            # Independent from key_level_only, high_quality_only, confluence_only.
            # Fail-closed: UNKNOWN or missing VP suppresses delivery.
            vp_confluence_required = settings.get("volume_profile_confluence_only") is True
            if vp_confluence_required:
                if not has_volume_profile_confluence_access(sub, atr_quality_config):
                    logger.info(
                        '{"event":"subscriber_delivery_suppressed",'
                        '"alert_id":"%s","chat_id":"%s",'
                        '"reason":"vp_confluence_entitlement",'
                        '"subscription_plan":"%s"}',
                        alert.alert_id,
                        chat_id[:80],
                        str(subscriber_plan(sub))[:40],
                    )
                    if self._monitoring is not None:
                        self._monitoring.observe_subscriber_suppressed(
                            "vp_confluence_entitlement"
                        )
                    continue
                if not _has_volume_profile_confluence(alert):
                    logger.info(
                        '{"event":"subscriber_delivery_suppressed",'
                        '"alert_id":"%s","chat_id":"%s",'
                        '"reason":"vp_confluence_not_matched",'
                        '"vp_confluence":%s}',
                        alert.alert_id,
                        chat_id[:80],
                        str(alert.volume_profile_confluence).lower(),
                    )
                    if self._monitoring is not None:
                        self._monitoring.observe_subscriber_suppressed(
                            "vp_confluence_not_matched"
                        )
                    continue
            threshold_raw = settings.get("min_confidence")
            using_profile_default = threshold_raw is None
            if using_profile_default:
                threshold_raw = profile_min_confidence
            try:
                threshold = float(threshold_raw)
            except (TypeError, ValueError):
                threshold = math.nan

            if not math.isfinite(threshold):
                logger.info(
                    '{"event":"subscriber_delivery_suppressed","alert_id":"%s",'
                    '"chat_id":"%s","reason":"invalid_min_confidence",'
                    '"using_profile_default":%s}',
                    alert.alert_id,
                    chat_id[:80],
                    str(using_profile_default).lower(),
                )
                if self._monitoring is not None:
                    self._monitoring.observe_subscriber_suppressed("invalid_min_confidence")
                continue

            if score < threshold:
                logger.info(
                    '{"event":"subscriber_delivery_suppressed","alert_id":"%s",'
                    '"chat_id":"%s","reason":"below_subscriber_confidence",'
                    '"confidence":%.2f,"minimum":%.2f}',
                    alert.alert_id,
                    chat_id[:80],
                    score,
                    threshold,
                )
                if self._monitoring is not None:
                    self._monitoring.observe_subscriber_suppressed("below_subscriber_confidence")
                continue

            atr_allowed, atr_reason = atr_delivery_decision(
                sub,
                alert.atr_quality,
                atr_quality_config,
            )
            if not atr_allowed:
                logger.info(
                    '{"event":"subscriber_delivery_suppressed",'
                    '"alert_id":"%s","chat_id":"%s",'
                    '"reason":"%s","subscription_plan":"%s",'
                    '"atr_status":"%s","atr_grade":"%s"}',
                    alert.alert_id,
                    chat_id[:80],
                    atr_reason,
                    str(sub.get("subscription_plan", "unknown"))[:40],
                    alert.atr_quality.status if alert.atr_quality else "UNKNOWN",
                    alert.atr_quality.grade if alert.atr_quality else "UNKNOWN",
                )
                if self._monitoring is not None:
                    self._monitoring.observe_subscriber_suppressed(atr_reason)
                continue

            if (
                atr_quality_config is not None
                and atr_quality_config.shadow_mode
                and str(sub.get("subscription_plan", "")).strip().lower() == "premium"
            ):
                logger.info(
                    '{"event":"subscriber_atr_shadow_decision",'
                    '"alert_id":"%s","chat_id":"%s",'
                    '"atr_status":"%s","atr_grade":"%s",'
                    '"decision":"%s"}',
                    alert.alert_id,
                    chat_id[:80],
                    alert.atr_quality.status if alert.atr_quality else "UNKNOWN",
                    alert.atr_quality.grade if alert.atr_quality else "UNKNOWN",
                    atr_reason,
                )

            eligible_count += 1
            try:
                subscriber_message, delivery_route = _subscriber_route_message(
                    message,
                    sub,
                    parent_at_htf_zone=alert.parent_at_htf_zone,
                )
                subscriber_message = localize_text(
                    subscriber_message,
                    self._language_for_chat(chat_id),
                )
                data = urllib.parse.urlencode({
                    "chat_id": chat_id,
                    "text": subscriber_message,
                    "parse_mode": self._config.parse_mode,
                }).encode("utf-8")
                req = urllib.request.Request(
                    url, data=data, method="POST",
                    headers={"Content-Type": "application/x-www-form-urlencoded"}
                )
                with urllib.request.urlopen(req, timeout=self._config.timeout_seconds) as resp:
                    body = json.loads(resp.read().decode("utf-8"))
                    if body.get("ok"):
                        self._last_delivery_at = datetime.now(timezone.utc).isoformat()
                        message_id = body["result"]["message_id"]
                        route = subscriber_plan(sub)
                        delivered_ids.append(message_id)
                        self.last_delivery_records.append({
                            "chat_id": chat_id,
                            "message_id": message_id,
                            "subscription_plan": route,
                        })
                        self.last_delivery_routes.add(route)
                        if self._monitoring is not None:
                            self._monitoring.observe_subscriber_delivered()
                        logger.info(
                            '{"event":"subscriber_telegram_sent","alert_id":"%s",'
                            '"chat_id":"%s","symbol":"%s","timeframe":"%s",'
                            '"subscription_plan":"%s","delivery_route":"%s",'
                            '"message_id":%d}',
                            alert.alert_id,
                            chat_id[:80],
                            alert.symbol,
                            alert.timeframe,
                            str(subscriber_plan(sub))[:40],
                            delivery_route,
                            body["result"]["message_id"],
                        )
                    else:
                        logger.warning(
                            '{"event":"subscriber_telegram_rejected","alert_id":"%s",'
                            '"chat_id":"%s","description":"%s"}',
                            alert.alert_id,
                            chat_id[:80],
                            str(body.get("description", "unknown"))[:160],
                        )
            except Exception as e:
                self._last_delivery_failed_at = datetime.now(timezone.utc).isoformat()
                logger.warning(
                    '{"event":"subscriber_telegram_failed","alert_id":"%s",'
                    '"chat_id":"%s","error":"%s"}',
                    alert.alert_id,
                    chat_id[:80],
                    str(e)[:160],
                )

        if self.last_delivery_routes:
            try:
                from core.analytics.google_sheets_reporting import report_signal_delivery
                for route in sorted(self.last_delivery_routes):
                    route_count = sum(
                        1
                        for record in self.last_delivery_records
                        if record.get("subscription_plan") == route
                    )
                    report_signal_delivery(
                        alert,
                        route,
                        route_count,
                        webhook_url=self._google_sheets_webhook_url,
                    )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    '{"event":"google_sheets_signal_delivery_failed",'
                    '"alert_id":"%s","error":"%s"}',
                    alert.alert_id,
                    str(exc)[:160],
                )
        
        logger.info(
            '{"event":"broadcast_done","alert_id":"%s","timeframe":"%s",'
            '"delivered":%d,"eligible_candidates":%d,"total_subs":%d}',
            alert.alert_id,
            alert.timeframe,
            len(delivered_ids),
            eligible_count,
            len(subscribers),
        )
        self.last_delivery_status = (
            "delivered"
            if delivered_ids
            else "retryable"
            if eligible_count > 0
            else "suppressed"
        )
        return delivered_ids

    @staticmethod
    def _fvg_message(
        *,
        title: str,
        symbol: str,
        htf_timeframe: str,
        ltf_timeframe: str,
        direction: str,
        fvg_high: str,
        fvg_low: str,
        fvg_midpoint: Optional[str] = None,
        invalidation_level: Optional[str] = None,
        midpoint_target: Optional[str] = None,
        opposite_extreme_target: Optional[str] = None,
        confidence_score: Optional[float] = None,
        candidate_id: str = "",
    ) -> str:
        direction_label = "\U0001f7e2 BUY" if str(direction).lower() == "bullish" else "\U0001f534 SELL"
        score_text = "n/a"
        if confidence_score is not None:
            try:
                score_text = f"{float(confidence_score):.1f}%"
            except (TypeError, ValueError):
                pass
        lines = [
            f"{title}",
            "━━━━━━━━━━━━━━━━━━━━",
            f"💎 <b>{escape(str(symbol))}</b> · {escape(str(htf_timeframe))} → {escape(str(ltf_timeframe))}",
            f"{direction_label} · <b>FVG</b>",
            "━━━━━━━━━━━━━━━━━━━━",
            "📍 <b>ENTRY ZONE</b>",
            f"Upper: <code>{escape(str(fvg_high))}</code>",
            f"Lower: <code>{escape(str(fvg_low))}</code>",
        ]
        if fvg_midpoint is not None:
            lines.append(f"🎯 Midpoint: <code>{escape(str(fvg_midpoint))}</code>")
        if invalidation_level is not None:
            lines.extend([
                "━━━━━━━━━━━━━━━━━━━━",
                f"🛑 Stop: <code>{escape(str(invalidation_level))}</code>",
                f"🎯 TP1: <code>{escape(str(midpoint_target))}</code>",
                f"🏆 TP2: <code>{escape(str(opposite_extreme_target))}</code>",
            ])
        lines.extend([
            "━━━━━━━━━━━━━━━━━━━━",
            f"🔥 Confidence: <b>{score_text}</b>",
            "🔖 Setup #" + escape(str(candidate_id)[-4:].upper()),
        ])
        return "\n".join(lines)

    def _broadcast_fvg_to_subscribers(
        self,
        *,
        message: str,
        symbol: str,
        timeframe: str,
        confidence_score: float,
        profile_min_confidence: Optional[float],
        parent_at_htf_zone: Optional[bool],
        quality_status: str,
        quality_would_suppress: bool,
        alert_kind: str,
        atr_quality_config: Optional[ATRQualityConfig] = None,
        quality_tightening: Optional[dict] = None,
    ) -> list[int]:
        """Route FVG messages with the same subscriber gates as normal alerts."""
        if self._subscriber_db is None:
            from core.alerts.subscriber_db import SubscriberDB
            self._subscriber_db = SubscriberDB()

        self.last_delivery_records = []
        self.last_delivery_routes = set()
        try:
            subscribers = self._subscriber_db.get_subscribers_for_timeframe(
                timeframe,
                symbol=symbol,
            )
            score = float(confidence_score)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                '{"event":"fvg_subscriber_lookup_failed","kind":"%s",'
                '"symbol":"%s","error":"%s"}',
                alert_kind,
                symbol,
                str(exc)[:160],
            )
            return []
        if not math.isfinite(score):
            return []

        delivered_ids: list[int] = []
        url = f"{self._TELEGRAM_API_BASE}{self._config.bot_token}/sendMessage"
        import urllib.parse
        import urllib.request

        for subscriber in subscribers:
            chat_id = str(subscriber.get("chat_id", ""))
            settings = subscriber.get("settings") if isinstance(subscriber.get("settings"), dict) else {}
            confluence_only = settings.get("confluence_only") is True
            if confluence_only and not has_premium_confluence_access(
                subscriber,
                atr_quality_config,
            ):
                logger.info(
                    '{"event":"fvg_subscriber_suppressed",'
                    '"kind":"%s","chat_id":"%s",'
                    '"reason":"premium_confluence_entitlement",'
                    '"subscription_plan":"%s"}',
                    alert_kind,
                    chat_id[:80],
                    str(subscriber_plan(subscriber))[:40],
                )
                if self._monitoring is not None:
                    self._monitoring.observe_subscriber_suppressed(
                        "premium_confluence_entitlement"
                    )
                continue
            key_level_required = settings.get("key_level_only") is True or confluence_only
            if key_level_required and parent_at_htf_zone is not True:
                suppression_reason = "confluence_key_level" if confluence_only else "key_level_only"
                logger.info(
                    '{"event":"fvg_subscriber_suppressed","kind":"%s",'
                    '"chat_id":"%s","reason":"%s"}',
                    alert_kind,
                    chat_id[:80],
                    suppression_reason,
                )
                if self._monitoring is not None:
                    self._monitoring.observe_subscriber_suppressed(suppression_reason)
                continue
            high_quality_required = settings.get("high_quality_only") is True or confluence_only
            if high_quality_required and (
                quality_status != "VALID" or quality_would_suppress
            ):
                suppression_reason = "confluence_quality" if confluence_only else "high_quality_only"
                logger.info(
                    '{"event":"fvg_subscriber_suppressed","kind":"%s",'
                    '"chat_id":"%s","reason":"%s",'
                    '"quality_status":"%s","would_suppress":%s}',
                    alert_kind,
                    chat_id[:80],
                    suppression_reason,
                    quality_status,
                    str(quality_would_suppress).lower(),
                )
                if self._monitoring is not None:
                    self._monitoring.observe_subscriber_suppressed(suppression_reason)
                continue
            # VP Confluence preference in FVG flow (Doc 40 Phase 7).
            vp_confluence_required = settings.get("volume_profile_confluence_only") is True
            if vp_confluence_required:
                if not has_volume_profile_confluence_access(
                    subscriber, atr_quality_config,
                ):
                    logger.info(
                        '{"event":"fvg_subscriber_suppressed","kind":"%s",'
                        '"chat_id":"%s","reason":"vp_confluence_entitlement"}',
                        alert_kind,
                        chat_id[:80],
                    )
                    if self._monitoring is not None:
                        self._monitoring.observe_subscriber_suppressed(
                            "vp_confluence_entitlement"
                        )
                    continue
                # FVG flow doesn't carry the AlertEvent.volume_profile
                # directly; fail-closed by defaulting to suppressed.
                logger.info(
                    '{"event":"fvg_subscriber_suppressed","kind":"%s",'
                    '"chat_id":"%s","reason":"vp_confluence_not_available_in_fvg"}',
                    alert_kind,
                    chat_id[:80],
                )
                if self._monitoring is not None:
                    self._monitoring.observe_subscriber_suppressed(
                        "vp_confluence_not_available_in_fvg"
                    )
                continue
            # An explicit numeric preference overrides the profile floor. A
            # missing or null saved preference inherits that floor, matching
            # the ordinary AlertEngine subscriber route.
            threshold_raw = settings.get("min_confidence")
            using_profile_default = threshold_raw is None
            if using_profile_default:
                threshold_raw = profile_min_confidence
            try:
                threshold = float(threshold_raw)
            except (TypeError, ValueError):
                threshold = math.nan
            if not math.isfinite(threshold):
                logger.info(
                    '{"event":"fvg_subscriber_suppressed","kind":"%s",'
                    '"chat_id":"%s","reason":"invalid_min_confidence",'
                    '"using_profile_default":%s}',
                    alert_kind,
                    chat_id[:80],
                    str(using_profile_default).lower(),
                )
                if self._monitoring is not None:
                    self._monitoring.observe_subscriber_suppressed(
                        "invalid_min_confidence"
                    )
                continue
            if score < threshold:
                logger.info(
                    '{"event":"fvg_subscriber_suppressed","kind":"%s",'
                    '"chat_id":"%s","reason":"below_confidence",'
                    '"confidence":%.2f,"minimum":"%s"}',
                    alert_kind,
                    chat_id[:80],
                    score,
                    str(threshold_raw)[:40],
                )
                if self._monitoring is not None:
                    self._monitoring.observe_subscriber_suppressed("below_subscriber_confidence")
                continue

            subscriber_message, delivery_route = _subscriber_route_message(
                message,
                subscriber,
                parent_at_htf_zone=parent_at_htf_zone,
            )
            subscriber_message = localize_text(
                subscriber_message,
                self._language_for_chat(chat_id),
            )
            try:
                data = urllib.parse.urlencode({
                    "chat_id": chat_id,
                    "text": subscriber_message,
                    "parse_mode": self._config.parse_mode,
                }).encode("utf-8")
                req = urllib.request.Request(
                    url,
                    data=data,
                    method="POST",
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                )
                with urllib.request.urlopen(req, timeout=self._config.timeout_seconds) as resp:
                    body = json.loads(resp.read().decode("utf-8"))
                if body.get("ok"):
                    self._last_delivery_at = datetime.now(timezone.utc).isoformat()
                    message_id = body["result"]["message_id"]
                    delivered_ids.append(message_id)
                    self.last_delivery_records.append({
                        "chat_id": chat_id,
                        "message_id": message_id,
                        "subscription_plan": subscriber_plan(subscriber),
                    })
                    self.last_delivery_routes.add(subscriber_plan(subscriber))
                    if self._monitoring is not None:
                        self._monitoring.observe_subscriber_delivered()
                    logger.info(
                        '{"event":"fvg_subscriber_sent","kind":"%s",'
                        '"chat_id":"%s","delivery_route":"%s","message_id":%d}',
                        alert_kind,
                        chat_id[:80],
                        delivery_route,
                        message_id,
                    )
            except Exception as exc:  # noqa: BLE001
                self._last_delivery_failed_at = datetime.now(timezone.utc).isoformat()
                logger.warning(
                    '{"event":"fvg_subscriber_failed","kind":"%s",'
                    '"chat_id":"%s","error":"%s"}',
                    alert_kind,
                    chat_id[:80],
                    str(exc)[:160],
                )
        return delivered_ids

    def broadcast_fvg_watch(
        self,
        *,
        symbol: str,
        htf_timeframe: str,
        ltf_timeframe: str,
        direction: str,
        fvg_high: str,
        fvg_low: str,
        fvg_midpoint: str,
        invalidation_level: str,
        midpoint_target: str,
        opposite_extreme_target: str,
        confidence_score: float,
        profile: str,
        candidate_id: str,
        parent_at_htf_zone: Optional[bool] = None,
        profile_min_confidence: Optional[float] = None,
        quality_status: str = "UNKNOWN",
        quality_would_suppress: bool = True,
        atr_quality_config: Optional[ATRQualityConfig] = None,
        quality_tightening: Optional[dict] = None,
    ) -> list[int]:
        """Send the first FVG alert and register the delayed-entry context."""
        message = self._fvg_message(
            title="⏳ <b>FVG ENTRY WATCH</b>",
            symbol=symbol,
            htf_timeframe=htf_timeframe,
            ltf_timeframe=ltf_timeframe,
            direction=direction,
            fvg_high=fvg_high,
            fvg_low=fvg_low,
            fvg_midpoint=fvg_midpoint,
            invalidation_level=invalidation_level,
            midpoint_target=midpoint_target,
            opposite_extreme_target=opposite_extreme_target,
            confidence_score=confidence_score,
            candidate_id=candidate_id,
        ) + "\n\n⚡️ Alert 2 fires when price enters this zone."
        if self._config.enabled:
            self.send_reply(self._config.chat_id, message)
        delivered = self._broadcast_fvg_to_subscribers(
            message=message,
            symbol=symbol,
            timeframe=htf_timeframe,
            confidence_score=confidence_score,
            profile_min_confidence=profile_min_confidence,
            parent_at_htf_zone=parent_at_htf_zone,
            quality_status=quality_status,
            quality_would_suppress=quality_would_suppress,
            alert_kind="watch",
            atr_quality_config=atr_quality_config,
        )
        logger.info(
            '{"event":"fvg_watch_broadcast_done","candidate_id":"%s",'
            '"profile":"%s","delivered":%d}',
            candidate_id,
            profile,
            len(delivered),
        )
        return delivered

    def broadcast_fvg_entry(
        self,
        *,
        symbol: str,
        htf_timeframe: str,
        ltf_timeframe: str,
        direction: str,
        fvg_high: str,
        fvg_low: str,
        invalidation_level: str,
        midpoint_target: str,
        opposite_extreme_target: str,
        confidence_score: float,
        profile: str,
        candidate_id: str,
        parent_at_htf_zone: Optional[bool] = None,
        profile_min_confidence: Optional[float] = None,
        quality_status: str = "UNKNOWN",
        quality_would_suppress: bool = True,
        atr_quality_config: Optional[ATRQualityConfig] = None,
        quality_tightening: Optional[dict] = None,
    ) -> list[int]:
        """Send the second FVG alert when live price enters the zone."""
        message = self._fvg_message(
            title="🎯 <b>FVG ENTRY TRIGGERED</b>",
            symbol=symbol,
            htf_timeframe=htf_timeframe,
            ltf_timeframe=ltf_timeframe,
            direction=direction,
            fvg_high=fvg_high,
            fvg_low=fvg_low,
            invalidation_level=invalidation_level,
            midpoint_target=midpoint_target,
            opposite_extreme_target=opposite_extreme_target,
            confidence_score=confidence_score,
            candidate_id=candidate_id,
        ) + "\n\n✅ Price has entered the planned setup zone."
        if self._config.enabled:
            self.send_reply(self._config.chat_id, message)
        delivered = self._broadcast_fvg_to_subscribers(
            message=message,
            symbol=symbol,
            timeframe=htf_timeframe,
            confidence_score=confidence_score,
            profile_min_confidence=profile_min_confidence,
            parent_at_htf_zone=parent_at_htf_zone,
            quality_status=quality_status,
            quality_would_suppress=quality_would_suppress,
            alert_kind="entry",
            atr_quality_config=atr_quality_config,
        )
        logger.info(
            '{"event":"fvg_entry_broadcast_done","candidate_id":"%s",'
            '"profile":"%s","delivered":%d}',
            candidate_id,
            profile,
            len(delivered),
        )
        return delivered

    def has_explicit_subscriber_override(
        self,
        *,
        timeframe: str,
        symbol: str,
        profile_min_confidence: float,
    ) -> bool:
        """Return whether an active route may intentionally use a lower floor."""
        if not self._config.enabled:
            return False
        if self._subscriber_db is None:
            from core.alerts.subscriber_db import SubscriberDB
            self._subscriber_db = SubscriberDB()
        try:
            subscribers = self._subscriber_db.get_subscribers_for_timeframe(
                timeframe,
                symbol=symbol,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                '{"event":"subscriber_override_lookup_failed",'
                '"timeframe":"%s","symbol":"%s","error":"%s"}',
                timeframe,
                symbol,
                str(exc)[:120],
            )
            return False

        try:
            profile_floor = float(profile_min_confidence)
        except (TypeError, ValueError):
            return False
        for subscriber in subscribers:
            settings = subscriber.get("settings") if isinstance(subscriber, dict) else None
            if not isinstance(settings, dict) or "min_confidence" not in settings:
                continue
            try:
                threshold = float(settings.get("min_confidence"))
            except (TypeError, ValueError):
                continue
            if math.isfinite(threshold) and 0.0 <= threshold <= 100.0 and threshold < profile_floor:
                return True
        return False

    def send_reply(self, chat_id: str, text: str, message_id: int = None) -> bool:
        """Send a reply to an existing message, or a standalone message if message_id is None."""
        text = localize_text(text, self._language_for_chat(chat_id))
        payload = {"chat_id": chat_id, "text": text, "parse_mode": self._config.parse_mode}
        if message_id is not None:
            payload["reply_to_message_id"] = message_id
        body = self._api_request("sendMessage", payload)
        return bool(body and body.get("ok"))

    def send_one_time_prompt(
        self,
        chat_id: str,
        text: str,
        *,
        reply_to_message_id: int | None = None,
    ) -> Optional[int]:
        """Send a bot prompt whose message ID can be removed after a reply.

        This intentionally returns only the Telegram message ID, never the
        input or response text, so callers can clean up one-time UI without
        retaining chat content.
        """
        text = localize_text(text, self._language_for_chat(chat_id))
        payload = {"chat_id": chat_id, "text": text, "parse_mode": self._config.parse_mode}
        if reply_to_message_id is not None:
            payload["reply_to_message_id"] = reply_to_message_id
        body = self._api_request("sendMessage", payload)
        return body.get("result", {}).get("message_id") if body and body.get("ok") else None

    def delete_message(self, chat_id: str, message_id: int | None) -> bool:
        """Remove a bot-owned, no-longer-needed chat message without logging it."""
        if isinstance(message_id, bool) or not isinstance(message_id, int) or message_id <= 0:
            return False
        body = self._api_request(
            "deleteMessage",
            {"chat_id": str(chat_id), "message_id": message_id},
        )
        return bool(body and body.get("ok"))

    def send_signal_replay(self, chat_id: str, image_bytes: bytes, caption: str) -> bool:
        """Deliver a cached/static replay image through Telegram's photo API."""
        if not isinstance(image_bytes, bytes) or not image_bytes:
            logger.warning(
                '{"event":"telegram_replay_delivery_failed","reason":"empty_image"}'
            )
            return False
        return self.send_photo(
            chat_id,
            image_bytes,
            caption,
            filename="atlax-signal-replay.png",
        )

    def send_photo(
        self,
        chat_id: str,
        image_bytes: bytes,
        caption: str,
        *,
        filename: str = "atlax.png",
    ) -> bool:
        """Send a photo using a bounded multipart request without logging secrets."""
        if not self._config.enabled:
            return False
        if not isinstance(image_bytes, bytes) or not image_bytes:
            return False
        if len(str(caption or "")) > 1024:
            logger.warning(
                '{"event":"telegram_replay_delivery_failed","reason":"caption_too_long"}'
            )
            return False

        import urllib.error
        import urllib.request
        import uuid

        boundary = f"----ATLAX{uuid.uuid4().hex}"
        boundary_bytes = boundary.encode("ascii")
        parts: list[bytes] = []

        def add_field(name: str, value: str) -> None:
            parts.extend(
                (
                    b"--" + boundary_bytes + b"\r\n",
                    f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode("utf-8"),
                    str(value).encode("utf-8"),
                    b"\r\n",
                )
            )

        add_field("chat_id", str(chat_id))
        add_field("parse_mode", str(self._config.parse_mode))
        add_field("caption", str(caption))
        parts.extend(
            (
                b"--" + boundary_bytes + b"\r\n",
                (
                    f'Content-Disposition: form-data; name="photo"; '
                    f'filename="{filename}"\r\n'
                ).encode("utf-8"),
                b"Content-Type: image/png\r\n\r\n",
                image_bytes,
                b"\r\n--" + boundary_bytes + b"--\r\n",
            )
        )
        body = b"".join(parts)
        url = f"{self._TELEGRAM_API_BASE}{self._config.bot_token}/sendPhoto"
        try:
            request = urllib.request.Request(
                url,
                data=body,
                method="POST",
                headers={
                    "Content-Type": f"multipart/form-data; boundary={boundary}",
                    "Content-Length": str(len(body)),
                },
            )
            with urllib.request.urlopen(request, timeout=self._config.timeout_seconds) as response:
                result = json.loads(response.read().decode("utf-8"))
            if result.get("ok"):
                logger.info(
                    '{"event":"telegram_replay_sent","chat_id":"%s","message_id":%s}',
                    str(chat_id)[:80],
                    result.get("result", {}).get("message_id"),
                )
                return True
            logger.warning(
                '{"event":"telegram_replay_rejected","chat_id":"%s","description":"%s"}',
                str(chat_id)[:80],
                str(result.get("description", "unknown"))[:160],
            )
            return False
        except urllib.error.HTTPError as exc:
            logger.warning(
                '{"event":"telegram_replay_delivery_failed","chat_id":"%s","status":%s}',
                str(chat_id)[:80],
                getattr(exc, "code", "unknown"),
            )
            return False
        except Exception as exc:  # noqa: BLE001 - replay must not affect live alerts
            logger.warning(
                '{"event":"telegram_replay_delivery_failed","chat_id":"%s","error":"%s"}',
                str(chat_id)[:80],
                str(exc)[:160],
            )
            return False

    def _api_request(self, method: str, payload: dict) -> Optional[dict]:
        """Call Telegram without logging credentials or message contents."""
        import urllib.parse
        import urllib.error
        import urllib.request

        url = f"{self._TELEGRAM_API_BASE}{self._config.bot_token}/{method}"
        data = urllib.parse.urlencode(payload).encode("utf-8")
        try:
            req = urllib.request.Request(
                url,
                data=data,
                method="POST",
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            with urllib.request.urlopen(req, timeout=self._config.timeout_seconds) as resp:
                body = json.loads(resp.read().decode("utf-8"))
                if body.get("ok"):
                    self._last_delivery_at = datetime.now(timezone.utc).isoformat()
                else:
                    self._last_delivery_failed_at = datetime.now(timezone.utc).isoformat()
                return body
        except urllib.error.HTTPError as exc:
            # Telegram returns useful JSON descriptions for HTTP failures, but
            # urllib raises before the normal response path can read them.
            detail = str(exc)
            try:
                raw_error = exc.read().decode("utf-8", errors="replace")
                error_body = json.loads(raw_error) if raw_error else {}
                detail = str(error_body.get("description") or detail)
            except Exception:  # noqa: BLE001
                pass
            is_edit_noop = (
                method == "editMessageText"
                and "message is not modified" in detail.lower()
            )
            if not is_edit_noop:
                logger.warning(
                    '{"event":"telegram_api_failed","method":"%s",'
                    '"status":%s,"error":"%s"}',
                    method,
                    getattr(exc, "code", "unknown"),
                    detail[:160],
                )
            self._last_delivery_failed_at = datetime.now(timezone.utc).isoformat()
            return {
                "ok": False,
                "error_code": getattr(exc, "code", None),
                "description": detail,
            }
        except Exception as exc:  # noqa: BLE001
            self._last_delivery_failed_at = datetime.now(timezone.utc).isoformat()
            logger.warning(
                '{"event":"telegram_api_failed","method":"%s","error":"%s"}',
                method,
                str(exc)[:160],
            )
            return None

    def answer_callback_query(self, callback_query_id: str) -> bool:
        if not callback_query_id:
            return False
        body = self._api_request("answerCallbackQuery", {"callback_query_id": callback_query_id})
        return bool(body and body.get("ok"))

    def send_inline_menu(self, chat_id: str, text: str, keyboard: list[list[dict]]) -> Optional[int]:
        text, keyboard = localize_outbound(
            text,
            keyboard,
            self._language_for_chat(chat_id),
        )
        body = self._api_request(
            "sendMessage",
            {
                "chat_id": chat_id,
                "text": text,
                "parse_mode": self._config.parse_mode,
                "reply_markup": json.dumps({"inline_keyboard": keyboard}),
            },
        )
        return body.get("result", {}).get("message_id") if body and body.get("ok") else None

    def edit_inline_menu(
        self, chat_id: str, message_id: int, text: str, keyboard: list[list[dict]]
    ) -> bool:
        text, keyboard = localize_outbound(
            text,
            keyboard,
            self._language_for_chat(chat_id),
        )
        body = self._api_request(
            "editMessageText",
            {
                "chat_id": chat_id,
                "message_id": message_id,
                "text": text,
                "parse_mode": self._config.parse_mode,
                "reply_markup": json.dumps({"inline_keyboard": keyboard}),
            },
        )
        if body and not body.get("ok"):
            description = str(body.get("description") or "")
            if "message is not modified" in description.lower():
                # Double-clicks and repeated refreshes are safe and expected
                # with inline keyboards; the requested state is already shown.
                logger.info(
                    '{"event":"telegram_edit_noop","chat_id":"%s",'
                    '"message_id":%s,"reason":"message_not_modified"}',
                    str(chat_id)[:80],
                    message_id,
                )
                return True
        return bool(body and body.get("ok"))

    def poll_commands(self) -> list[dict]:
        """
        Polls the Telegram API for new messages from anyone.
        Returns command, callback, and ordinary-text events. Ordinary text is
        required only for explicit admin setup flows; the scanner ignores it
        unless a safe, server-side draft is active.
        """
        if not self._config.enabled:
            return []
            
        try:
            import urllib.request
            import urllib.parse
        except ImportError:
            return []
            
        self._last_poll_at = datetime.now(timezone.utc).isoformat()
        url = f"{self._TELEGRAM_API_BASE}{self._config.bot_token}/getUpdates"
        params = {"timeout": 2, "allowed_updates": json.dumps(["message", "callback_query"])}
        if self._last_update_id > 0:
            params["offset"] = self._last_update_id + 1
            
        url_with_params = f"{url}?{urllib.parse.urlencode(params)}"
        
        commands = []
        self._last_poll_failed = False
        try:
            req = urllib.request.Request(url_with_params)
            with urllib.request.urlopen(req, timeout=5) as resp:
                body = json.loads(resp.read().decode("utf-8"))
                
                if body.get("ok") and "result" in body:
                    self._last_poll_success_at = datetime.now(timezone.utc).isoformat()
                    for update in body["result"]:
                        update_id = update.get("update_id", 0)
                        self._last_update_id = max(self._last_update_id, update_id)
                        
                        callback = update.get("callback_query")
                        if callback:
                            message = callback.get("message", {})
                            chat = message.get("chat", {})
                            sender = callback.get("from", {})
                            commands.append({
                                "cmd": callback.get("data", ""),
                                "chat_id": str(chat.get("id", "")),
                                "username": sender.get("username") or sender.get("first_name", "Unknown"),
                                "language_code": sender.get("language_code", ""),
                                "is_admin": str(chat.get("id")) == self._config.chat_id,
                                "type": "callback",
                                "message_id": message.get("message_id"),
                                "callback_query_id": callback.get("id", ""),
                            })
                            continue

                        message = update.get("message", {})
                        chat = message.get("chat", {})
                        sender = message.get("from", {})
                        text = message.get("text", "").strip()
                        
                        if text:
                            commands.append({
                                "cmd":      text.split("@", 1)[0] if text.startswith("/") and "@" in text.split()[0] else text,
                                "chat_id":  str(chat.get("id", "")),
                                "username": sender.get("username") or sender.get("first_name", "Unknown"),
                                "language_code": sender.get("language_code", ""),
                                "is_admin": str(chat.get("id")) == self._config.chat_id,
                                "message_id": message.get("message_id"),
                                "type": "command" if text.startswith("/") else "message",
                            })
        except Exception as exc:  # noqa: BLE001
            self._last_poll_failed = True
            logger.warning(
                '{"event":"telegram_poll_failed","error":"%s"}',
                str(exc)[:160],
            )
            
        return commands

    def register_bot_commands(self) -> None:
        commands = [
            {"command": "start", "description": "Open ATLAX"},
            {"command": "signals", "description": "Open signal terminal"},
            {"command": "profile", "description": "View your profile"},
            {"command": "subscribe", "description": "Subscribe or renew access"},
            {"command": "status", "description": "Check access status"},
            {"command": "help", "description": "Show help"},
        ]
        self._api_request("setMyCommands", {"commands": json.dumps(commands)})

    def check_for_commands(self) -> None:
        return None

    def mini_app_button(self) -> Optional[dict]:
        """Return the configured Mini App button, or None when disabled."""
        url = str(getattr(self._config, "mini_app_url", "") or "").strip()
        if not url:
            return None
        return {"text": "Open ATLAX", "web_app": {"url": url}}

    def register_mini_app_menu_button(self) -> None:
        button = self.mini_app_button()
        if button:
            self._api_request(
                "setChatMenuButton",
                {"menu_button": json.dumps({"type": "web_app", "text": button["text"], "web_app": button["web_app"]})},
            )

