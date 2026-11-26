"""Subscription access maintenance for the ATLAX Telegram service."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from html import escape
from math import ceil
from typing import Any, Callable, Optional

from core.alerts.subscriber_db import STYLE_LABELS, SubscriberDB
from core.logging.audit_log import OperationalAuditLog
from core.payments.payment_service import PaymentService

logger = logging.getLogger("atlax.payments.lifecycle")


class SubscriptionLifecycleService:
    """Run idempotent payment expiry, access expiry, and reminder work."""

    def __init__(
        self,
        payment_service: PaymentService,
        subscriber_db: SubscriberDB,
        audit_log: OperationalAuditLog,
        now_fn: Optional[Callable[[], datetime]] = None,
    ) -> None:
        self._payment_service = payment_service
        self._subscriber_db = subscriber_db
        self._audit_log = audit_log
        self._now_fn = now_fn or (lambda: datetime.now(timezone.utc))

    def attach_subscriber_db(self, subscriber_db: SubscriberDB) -> None:
        self._subscriber_db = subscriber_db

    def _now(self) -> datetime:
        current = self._now_fn()
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        return current.astimezone(timezone.utc)

    def _audit(
        self,
        action: str,
        *,
        target_chat_id: str = "",
        result: str = "success",
        metadata: Optional[dict[str, Any]] = None,
    ) -> None:
        self._audit_log.record(
            "system",
            action,
            target_chat_id=target_chat_id,
            result=result,
            metadata=metadata,
        )

    def _expiry_notice(self, subscriber: dict[str, Any]) -> str:
        style = STYLE_LABELS.get(
            str(subscriber.get("style", "")),
            str(subscriber.get("style", "your selected profile")),
        )
        return (
            "\U0001F512 <b>ATLAX access has expired</b>\n\n"
            "Your signal delivery and ATLAX AI access are now paused because "
            "the subscription period has ended. Your profile and history remain saved.\n\n"
            f"\U0001F3AF Previous routing profile: <b>{escape(style)}</b>\n\n"
            "Use /renew to choose a payment method and restore access. "
            "After payment review, ATLAX will reactivate the saved account."
        )

    def _reminder_message(
        self,
        subscriber: dict[str, Any],
        reminder_hours: int,
        expires_at: datetime,
        remaining_hours: float,
    ) -> str:
        style = STYLE_LABELS.get(
            str(subscriber.get("style", "")),
            str(subscriber.get("style", "your selected profile")),
        )
        remaining_label = f"{max(1, ceil(remaining_hours))} hour(s)"
        return (
            "\u23F3 <b>ATLAX subscription reminder</b>\n\n"
            f"Your ATLAX access expires in approximately <b>{remaining_label}</b> "
            f"(the next reminder window is {reminder_hours} hours).\n\n"
            f"\U0001F3AF Current routing profile: <b>{escape(style)}</b>\n"
            f"\U0001F4C5 Expiry: <code>{escape(expires_at.strftime('%Y-%m-%d %H:%M UTC'))}</code>\n\n"
            "Renew now to keep receiving the signals and ATLAX AI tools "
            "associated with your profile. Use /renew to continue."
        )

    def _send_expiry_notices(self, telegram_channel) -> tuple[int, int]:
        if telegram_channel is None or not getattr(telegram_channel, "is_enabled", True):
            return 0, 0

        sent = 0
        failures = 0
        for subscriber in self._subscriber_db.list_subscribers():
            if subscriber.get("subscription_status") != "expired":
                continue
            if subscriber.get("subscription_expiry_notice_sent") is True:
                continue
            chat_id = str(subscriber.get("chat_id", ""))
            if not chat_id:
                continue
            try:
                delivered = bool(
                    telegram_channel.send_reply(chat_id, self._expiry_notice(subscriber))
                )
            except Exception as exc:  # noqa: BLE001
                delivered = False
                logger.warning(
                    '{"event":"subscription_expiry_notice_error","chat_id":"%s","error":"%s"}',
                    chat_id[:80],
                    str(exc)[:160],
                )
            if delivered:
                self._subscriber_db.mark_subscription_expiry_notice_sent(chat_id)
                self._audit(
                    "subscription_expiry_notice_sent",
                    target_chat_id=chat_id,
                )
                sent += 1
            else:
                self._audit(
                    "subscription_expiry_notice",
                    target_chat_id=chat_id,
                    result="failed",
                )
                failures += 1
        return sent, failures

    def _due_reminder_hours(
        self,
        subscriber: dict[str, Any],
        now: datetime,
    ) -> Optional[int]:
        expires_at = self._subscriber_db.get_subscription_expires_at(
            str(subscriber.get("chat_id", ""))
        )
        if expires_at is None:
            return None
        remaining_hours = (expires_at - now).total_seconds() / 3600
        if remaining_hours <= 0:
            return None
        sent = subscriber.get("subscription_reminders_sent", {})
        if not isinstance(sent, dict):
            sent = {}
        due = [
            int(hours)
            for hours in getattr(self._payment_service._config, "expiry_reminder_hours", ())
            if remaining_hours <= int(hours) and str(int(hours)) not in sent
        ]
        return min(due) if due else None

    def _send_expiry_reminders(self, telegram_channel, now: datetime) -> tuple[int, int]:
        if telegram_channel is None or not getattr(telegram_channel, "is_enabled", True):
            return 0, 0

        sent = 0
        failures = 0
        for subscriber in self._subscriber_db.list_subscribers():
            if not subscriber.get("active"):
                continue
            chat_id = str(subscriber.get("chat_id", ""))
            if not chat_id:
                continue
            reminder_hours = self._due_reminder_hours(subscriber, now)
            if reminder_hours is None:
                continue
            expires_at = self._subscriber_db.get_subscription_expires_at(chat_id)
            if expires_at is None:
                continue
            remaining_hours = (expires_at - now).total_seconds() / 3600
            try:
                delivered = bool(
                    telegram_channel.send_reply(
                        chat_id,
                        self._reminder_message(
                            subscriber,
                            reminder_hours,
                            expires_at,
                            remaining_hours,
                        ),
                    )
                )
            except Exception as exc:  # noqa: BLE001
                delivered = False
                logger.warning(
                    '{"event":"subscription_expiry_reminder_error","chat_id":"%s","error":"%s"}',
                    chat_id[:80],
                    str(exc)[:160],
                )
            if delivered:
                self._subscriber_db.mark_subscription_reminder_sent(
                    chat_id,
                    reminder_hours,
                    sent_at=now,
                )
                self._audit(
                    "subscription_expiry_reminder_sent",
                    target_chat_id=chat_id,
                    metadata={"reminder_hours": reminder_hours},
                )
                sent += 1
            else:
                self._audit(
                    "subscription_expiry_reminder",
                    target_chat_id=chat_id,
                    result="failed",
                    metadata={"reminder_hours": reminder_hours},
                )
                failures += 1
        return sent, failures

    def run(self, telegram_channel=None) -> dict[str, int]:
        """Run one maintenance pass; safe to repeat on every scanner cycle."""
        result = {
            "expired_payment_intents": 0,
            "suspended_subscriptions": 0,
            "expiry_notices_sent": 0,
            "expiry_reminders_sent": 0,
            "delivery_failures": 0,
        }

        try:
            expired_intents = self._payment_service.expire_stale_intents()
            result["expired_payment_intents"] = len(expired_intents)
            for intent in expired_intents:
                self._audit(
                    "payment_intent_expired",
                    target_chat_id=str(intent.get("chat_id", "")),
                    metadata={
                        "reference": str(intent.get("reference", "")),
                        "method": str(intent.get("method", "")),
                    },
                )
        except Exception as exc:  # noqa: BLE001
            logger.error(
                '{"event":"payment_lifecycle_expiry_failed","error":"%s"}',
                str(exc)[:200],
            )

        now = self._now()
        try:
            expired_subscribers = self._subscriber_db.expire_subscriptions(now=now)
            result["suspended_subscriptions"] = len(expired_subscribers)
            for subscriber in expired_subscribers:
                self._audit(
                    "subscription_access_suspended",
                    target_chat_id=str(subscriber.get("chat_id", "")),
                    metadata={"reason": "subscription_expired"},
                )
        except Exception as exc:  # noqa: BLE001
            logger.error(
                '{"event":"subscription_expiry_sweep_failed","error":"%s"}',
                str(exc)[:200],
            )

        notice_sent, notice_failures = self._send_expiry_notices(telegram_channel)
        reminder_sent, reminder_failures = self._send_expiry_reminders(
            telegram_channel,
            now,
        )
        result["expiry_notices_sent"] = notice_sent
        result["expiry_reminders_sent"] = reminder_sent
        result["delivery_failures"] = notice_failures + reminder_failures
        return result
