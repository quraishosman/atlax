"""Service contracts backed by the explicit PostgreSQL repositories."""

from __future__ import annotations

import copy
import json
import logging
import secrets
from dataclasses import asdict, is_dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Optional

from core.alerts.subscriber_db import SUBSCRIPTION_PLANS, STYLE_TIMEFRAMES, SubscriberDB
from core.analytics.journal import SQLiteJournal
from core.analytics.atr_passport import atr_quality_fields
from core.analytics.key_level_passport import key_level_passport_fields
from core.analytics.volume_profile_passport import volume_profile_passport_fields
from core.confidence.atr_quality import ATRQualityResult
from core.models.volume_profile_result import VolumeProfileResult
from core.logging.audit_log import OperationalAuditLog
from core.models.analytics import JournalEntry
from core.models.approval import ApprovalStatus, PendingTrade
from core.models.prop_firm import LossBudgetDecision
from core.models.execution import ExecutionRecord, ExecutionRequest
from core.storage.repositories import (
    NO_WRITE,
    PostgresApprovalRepository,
    PostgresAuditRepository,
    PostgresInviteCodeRepository,
    PostgresJournalRepository,
    PostgresPaymentRepository,
    PostgresRuntimeRepository,
    PostgresSubscriberRepository,
)

logger = logging.getLogger("atlax.storage.adapters")


class PostgresSubscriberDB(SubscriberDB):
    """SubscriberDB-compatible adapter using row-locked PostgreSQL writes."""

    def __init__(self, subscribers: PostgresSubscriberRepository, invite_codes: PostgresInviteCodeRepository) -> None:
        self._subscriber_repo = subscribers
        self._invite_repo = invite_codes
        self.CODES_FILE = "postgres://invite_codes"
        self.SUBS_FILE = "postgres://subscribers"

    def generate_code(self, label: str = "", days_valid: int = 30) -> str:
        code = "ATLAX-" + secrets.token_hex(4).upper()
        self._invite_repo.upsert(code, {
            "label": label,
            "expires": (datetime.now(timezone.utc) + timedelta(days=days_valid)).isoformat(),
            "used_by": None,
            "used_at": None,
            "days_valid": days_valid,
        })
        return code

    def validate_code(self, code: str) -> tuple[bool, str]:
        record = self._invite_repo.get(str(code).strip().upper())
        if not record or record.get("revoked"):
            return False, "Invalid code."
        if record.get("used_by") is not None:
            return False, "Code already used."
        expires = self._parse_datetime(record.get("expires"))
        if expires and datetime.now(timezone.utc) > expires:
            return False, "Code expired."
        return True, "OK"

    def consume_code(self, code: str, chat_id: str) -> None:
        normalized = str(code).strip().upper()
        record = self._invite_repo.get(normalized)
        if not record or record.get("used_by") is not None or record.get("revoked"):
            return
        record["used_by"] = str(chat_id)
        record["used_at"] = datetime.now(timezone.utc).isoformat()
        self._invite_repo.upsert(normalized, record)

    def revoke_code(self, code: str) -> None:
        normalized = str(code).strip().upper()
        record = self._invite_repo.get(normalized)
        if not record:
            return
        record["revoked"] = True
        record["revoked_at"] = datetime.now(timezone.utc).isoformat()
        self._invite_repo.upsert(normalized, record)

    def list_codes(self) -> list[dict[str, Any]]:
        return self._invite_repo.list()

    def register(self, chat_id: str, username: str, style: str, invite_code: str,
                 subscription_days: Optional[int] = None,
                 subscription_source: str = "invite_code", payment_method: str = "",
                 payment_reference: str = "", subscription_plan: str = "standard") -> bool:
        if style not in STYLE_TIMEFRAMES:
            return False
        subscription_plan = str(subscription_plan or "standard").strip().lower()
        if subscription_plan not in SUBSCRIPTION_PLANS:
            return False
        chat_id = str(chat_id)
        now = datetime.now(timezone.utc)
        existing = copy.deepcopy(self._subscriber_repo.get(chat_id) or {})
        self._ensure_settings(existing)
        record = {
            **existing,
            "chat_id": chat_id,
            "username": username,
            "style": style,
            "timeframes": STYLE_TIMEFRAMES[style],
            "invite_code": invite_code,
            "joined": existing.get("joined", now.isoformat()),
            "active": True,
            "subscription_status": "active",
            "subscription_source": subscription_source,
            "payment_method": payment_method,
            "payment_reference": payment_reference,
            "subscription_plan": subscription_plan,
            "subscription_started_at": now.isoformat(),
            "updated_at": now.isoformat(),
        }
        current_expiry = self._parse_datetime(existing.get("subscription_expires_at"))
        if subscription_days is None:
            if current_expiry and current_expiry > now:
                record["subscription_expires_at"] = current_expiry.isoformat()
            else:
                record.pop("subscription_expires_at", None)
        else:
            base = current_expiry if current_expiry and current_expiry > now else now
            record["subscription_expires_at"] = (base + timedelta(days=subscription_days)).isoformat()
            record["subscription_duration_days"] = subscription_days
            record["subscription_reminders_sent"] = {}
            record["subscription_expiry_notice_sent"] = False
        self._subscriber_repo.upsert(record)
        return True

    def activate_subscription(self, chat_id: str, username: str, style: str,
                              subscription_days: int, payment_method: str,
                              payment_reference: str, invite_code: str = "",
                              subscription_source: str = "paid",
                              subscription_plan: str = "standard") -> bool:
        return self.register(
            chat_id, username, style, invite_code, subscription_days,
            subscription_source, payment_method, payment_reference,
            subscription_plan,
        )

    def get_subscriber(self, chat_id: str) -> Optional[dict[str, Any]]:
        record = self._subscriber_repo.get(str(chat_id))
        if record is not None:
            record.setdefault("subscription_plan", "standard")
        return copy.deepcopy(record) if record else None

    def get_subscription_plan(self, chat_id: str) -> str:
        record = self.get_subscriber(chat_id) or {}
        plan = str(record.get("subscription_plan", "standard")).strip().lower()
        return plan if plan in SUBSCRIPTION_PLANS else "unknown"

    def update_subscription_plan(self, chat_id: str, plan: str) -> bool:
        normalized = str(plan or "").strip().lower()
        if normalized not in SUBSCRIPTION_PLANS:
            return False

        def mutate(record: dict[str, Any]) -> bool:
            record["subscription_plan"] = normalized
            return True

        return bool(self._subscriber_repo.mutate(str(chat_id), mutate))

    def get_subscription_expires_at(self, chat_id: str) -> Optional[datetime]:
        record = self._subscriber_repo.get(str(chat_id))
        return self._parse_datetime(record.get("subscription_expires_at")) if record else None

    def is_registered(self, chat_id: str) -> bool:
        record = self._subscriber_repo.get(str(chat_id))
        if not record or not record.get("active"):
            return False
        expires = self._parse_datetime(record.get("subscription_expires_at"))
        if expires and datetime.now(timezone.utc) >= expires:
            self.expire_subscriptions()
            return False
        return True

    def expire_subscriptions(self, now: Optional[datetime] = None) -> list[dict[str, Any]]:
        current = now or datetime.now(timezone.utc)
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        current = current.astimezone(timezone.utc)
        changed: list[dict[str, Any]] = []
        for snapshot in self._subscriber_repo.list(active_only=True):
            chat_id = str(snapshot.get("chat_id", ""))

            def mutate(record: dict[str, Any], current=current) -> Any:
                expires = self._parse_datetime(record.get("subscription_expires_at"))
                if not record.get("active") or expires is None or current < expires:
                    return NO_WRITE
                now_iso = current.isoformat()
                record.update({
                    "active": False,
                    "subscription_status": "expired",
                    "subscription_expired_at": now_iso,
                    "subscription_expiry_notice_sent": False,
                })
                return copy.deepcopy(record)

            result = self._subscriber_repo.mutate(chat_id, mutate)
            if isinstance(result, dict):
                changed.append(result)
        return changed

    def mark_subscription_reminder_sent(self, chat_id: str, reminder_hours: int,
                                        sent_at: Optional[datetime] = None) -> bool:
        timestamp = sent_at or datetime.now(timezone.utc)
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=timezone.utc)
        key = str(int(reminder_hours))

        def mutate(record: dict[str, Any]) -> Any:
            if not record.get("active"):
                return NO_WRITE
            reminders = record.setdefault("subscription_reminders_sent", {})
            if not isinstance(reminders, dict):
                reminders = {}
                record["subscription_reminders_sent"] = reminders
            if key in reminders:
                return NO_WRITE
            reminders[key] = timestamp.astimezone(timezone.utc).isoformat()
            return True

        return bool(self._subscriber_repo.mutate(str(chat_id), mutate))

    def mark_subscription_expiry_notice_sent(self, chat_id: str) -> bool:
        def mutate(record: dict[str, Any]) -> Any:
            if record.get("subscription_status") != "expired" or record.get("subscription_expiry_notice_sent"):
                return NO_WRITE
            record["subscription_expiry_notice_sent"] = True
            return True

        return bool(self._subscriber_repo.mutate(str(chat_id), mutate))

    def update_style(self, chat_id: str, style: str) -> bool:
        if style not in STYLE_TIMEFRAMES:
            return False

        def mutate(record: dict[str, Any]) -> Any:
            record["style"] = style
            record["timeframes"] = STYLE_TIMEFRAMES[style]
            return True

        return bool(self._subscriber_repo.mutate(str(chat_id), mutate))

    def deactivate(self, chat_id: str) -> None:
        def mutate(record: dict[str, Any]) -> Any:
            record["active"] = False
            record["subscription_status"] = "inactive"
            return True

        self._subscriber_repo.mutate(str(chat_id), mutate)

    def update_settings(self, chat_id: str, key: str, value: Any) -> bool:
        def mutate(record: dict[str, Any]) -> Any:
            self._ensure_settings(record)
            record["settings"][key] = value
            return True

        return bool(self._subscriber_repo.mutate(str(chat_id), mutate))

    def get_settings(self, chat_id: str) -> dict[str, Any]:
        record = self._subscriber_repo.get(str(chat_id)) or {}
        return copy.deepcopy(record.get("settings") or {
            "symbols": [], "min_confidence": None, "notifications": "all",
        })

    def list_subscribers(self) -> list[dict[str, Any]]:
        return copy.deepcopy(self._subscriber_repo.list())

    def total_active(self) -> int:
        return sum(
            1 for record in self._subscriber_repo.list(active_only=True)
            if self.is_registered(record.get("chat_id", ""))
        )

    def get_subscribers_for_timeframe(self, timeframe: str, symbol: str = None) -> list[dict[str, Any]]:
        result = []
        for record in self._subscriber_repo.list(active_only=True):
            chat_id = str(record.get("chat_id", ""))
            if not self.is_registered(chat_id) or timeframe not in record.get("timeframes", []):
                continue
            if symbol:
                allowed = (record.get("settings") or {}).get("symbols", [])
                if allowed and symbol not in allowed:
                    continue
            result.append(copy.deepcopy(record))
        return result

    def reserve_ai_request(self, chat_id: str, *, daily_limit: int,
                           per_minute_limit: int, cooldown_seconds: int,
                           active_timeout_seconds: int) -> dict[str, Any]:
        now = datetime.now(timezone.utc)

        def mutate(record: dict[str, Any]) -> Any:
            expires = self._parse_datetime(record.get("subscription_expires_at"))
            if not record.get("active") or (expires and now >= expires):
                return NO_WRITE
            security = record.setdefault("ai_security", {})
            if security.get("blocked"):
                return NO_WRITE
            usage = record.setdefault("ai_usage", {})
            now_iso = now.isoformat()
            if usage.get("date") != now.date().isoformat():
                usage.update({"date": now.date().isoformat(), "daily_count": 0,
                              "minute_count": 0, "minute_window_started_at": now_iso})
            active_started = self._parse_datetime(usage.get("active_started_at"))
            if usage.get("active_request") and active_started:
                if (now - active_started).total_seconds() < active_timeout_seconds:
                    return NO_WRITE
            if usage.get("active_request"):
                usage["active_request"] = False
                usage["active_started_at"] = None
            last_request = self._parse_datetime(usage.get("last_request_at"))
            if last_request and (now - last_request).total_seconds() < cooldown_seconds:
                return NO_WRITE
            if int(usage.get("daily_count", 0)) >= daily_limit:
                return NO_WRITE
            window_started = self._parse_datetime(usage.get("minute_window_started_at"))
            if not window_started or (now - window_started).total_seconds() >= 60:
                usage["minute_window_started_at"] = now_iso
                usage["minute_count"] = 0
                window_started = now
            if int(usage.get("minute_count", 0)) >= per_minute_limit:
                return NO_WRITE
            usage["daily_count"] = int(usage.get("daily_count", 0)) + 1
            usage["minute_count"] = int(usage.get("minute_count", 0)) + 1
            usage["last_request_at"] = now_iso
            usage["active_request"] = True
            usage["active_started_at"] = now_iso
            return {"allowed": True, "daily_count": usage["daily_count"], "daily_limit": daily_limit}

        result = self._subscriber_repo.mutate(str(chat_id), mutate)
        return result if isinstance(result, dict) else {"allowed": False, "reason": "rate_limited"}

    def release_ai_request(self, chat_id: str) -> None:
        def mutate(record: dict[str, Any]) -> Any:
            usage = record.setdefault("ai_usage", {})
            usage["active_request"] = False
            usage["active_started_at"] = None
            return True

        self._subscriber_repo.mutate(str(chat_id), mutate)

    def get_ai_usage(self, chat_id: str) -> dict[str, Any]:
        record = self._subscriber_repo.get(str(chat_id)) or {}
        usage = record.get("ai_usage") or {}
        security = record.get("ai_security") or {}
        return {
            "date": usage.get("date"),
            "daily_count": int(usage.get("daily_count", 0)),
            "minute_count": int(usage.get("minute_count", 0)),
            "active_request": bool(usage.get("active_request", False)),
            "blocked": bool(security.get("blocked", False)),
            "abuse_strikes": int(security.get("abuse_strikes", 0)),
            "last_abuse_category": security.get("last_abuse_category"),
            "last_abuse_at": security.get("last_abuse_at"),
        }

    def reset_ai_usage(self, chat_id: str) -> bool:
        now = datetime.now(timezone.utc)

        def mutate(record: dict[str, Any]) -> Any:
            record["ai_usage"] = {
                "date": now.date().isoformat(), "daily_count": 0, "minute_count": 0,
                "minute_window_started_at": now.isoformat(), "last_request_at": None,
                "active_request": False, "active_started_at": None,
            }
            return True

        return bool(self._subscriber_repo.mutate(str(chat_id), mutate))

    def set_ai_blocked(self, chat_id: str, blocked: bool) -> bool:
        def mutate(record: dict[str, Any]) -> Any:
            record.setdefault("ai_security", {})["blocked"] = bool(blocked)
            return True

        return bool(self._subscriber_repo.mutate(str(chat_id), mutate))

    def record_ai_abuse(self, chat_id: str, category: str, strike_limit: int) -> dict[str, Any]:
        now_iso = datetime.now(timezone.utc).isoformat()

        def mutate(record: dict[str, Any]) -> Any:
            security = record.setdefault("ai_security", {})
            strikes = int(security.get("abuse_strikes", 0)) + 1
            blocked = bool(security.get("blocked")) or strikes >= strike_limit
            security.update({"abuse_strikes": strikes,
                             "last_abuse_category": str(category)[:40],
                             "last_abuse_at": now_iso, "blocked": blocked})
            return {"blocked": blocked, "strikes": strikes}

        result = self._subscriber_repo.mutate(str(chat_id), mutate)
        return result if isinstance(result, dict) else {"blocked": False, "strikes": 0}


class PostgresPaymentStore:
    """PaymentStore-compatible adapter using PostgreSQL row locks."""

    def __init__(self, payments: PostgresPaymentRepository) -> None:
        self._payment_repo = payments

    def _now(self) -> datetime:
        return datetime.now(timezone.utc)

    def _parse_dt(self, value: Any) -> Optional[datetime]:
        if not value:
            return None
        try:
            parsed = datetime.fromisoformat(str(value))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc)
        except (TypeError, ValueError):
            return None

    def _reference_exists(self, reference: str) -> bool:
        return self._payment_repo.get_by_reference(reference) is not None

    def _generate_reference(self, prefix: str, method: str) -> str:
        # CR is the approved checkout-reference tag for every subscription
        # rail; the stored method field identifies the actual payment rail.
        return f"{prefix.strip().upper()}-CR-{secrets.token_hex(3).upper()}"

    def _record_status_event(
        self,
        intent: dict[str, Any],
        status: str,
        actor: str = "",
        note: str = "",
    ) -> None:
        history = intent.get("status_history")
        if not isinstance(history, list):
            history = []
            intent["status_history"] = history
        if history and history[-1].get("status") == status:
            return
        event: dict[str, Any] = {"status": status, "at": self._now().isoformat()}
        if actor.strip():
            event["actor"] = actor.strip()
        if note.strip():
            event["note"] = note.strip()
        history.append(event)

    def _expire_stale_intents(self) -> list[dict[str, Any]]:
        expired: list[dict[str, Any]] = []
        now = self._now()
        for intent in self._payment_repo.list():
            if intent.get("status") in {"confirmed", "rejected", "expired"}:
                continue
            expires_at = self._parse_dt(intent.get("expires_at"))
            if not expires_at or now <= expires_at:
                continue
            reference = str(intent.get("reference", "")).strip().upper()

            def expire(record: dict[str, Any]) -> dict[str, Any] | object:
                if record.get("status") in {"confirmed", "rejected", "expired"}:
                    return NO_WRITE
                timestamp = self._now().isoformat()
                record["status"] = "expired"
                record["expired_at"] = timestamp
                self._record_status_event(record, "expired")
                return copy.deepcopy(record)

            updated = self._payment_repo.mutate_by_reference(reference, expire)
            if isinstance(updated, dict):
                expired.append(updated)
        return expired

    def expire_stale_intents(self) -> list[dict[str, Any]]:
        return self._expire_stale_intents()

    def create_intent(
        self,
        *,
        chat_id: str,
        username: str,
        method: str,
        style: str,
        amount_usd: str,
        payment_destination: dict[str, Any],
        reference_prefix: str,
        support_contact: str,
        payment_ttl_hours: int,
        note: str = "",
        subscription_plan: str = "standard",
    ) -> dict[str, Any]:
        method = method.strip().lower()
        now = self._now()
        reference = self._generate_reference(reference_prefix, method)
        while self._reference_exists(reference):
            reference = self._generate_reference(reference_prefix, method)

        record = {
            "intent_id": f"pi_{secrets.token_hex(8)}",
            "reference": reference,
            "chat_id": str(chat_id),
            "username": username,
            "method": method,
            "style": style,
            "subscription_plan": str(subscription_plan or "standard").strip().lower(),
            "amount_usd": str(amount_usd),
            "payment_destination": copy.deepcopy(payment_destination),
            "support_contact": support_contact,
            "note": note,
            "status": "awaiting_payment",
            "status_history": [{"status": "awaiting_payment", "at": now.isoformat()}],
            "created_at": now.isoformat(),
            "updated_at": now.isoformat(),
            "expires_at": (now + timedelta(hours=payment_ttl_hours)).isoformat(),
            "submitted_at": None,
            "submitted_tx_reference": "",
            "submitted_note": "",
            "confirmed_at": None,
            "confirmed_by": "",
            "rejected_at": None,
            "rejected_by": "",
            "rejection_reason": "",
        }
        self._payment_repo.upsert(record)
        return copy.deepcopy(record)

    def get_intent(self, intent_id: str) -> Optional[dict[str, Any]]:
        self._expire_stale_intents()
        intent = self._payment_repo.get(intent_id)
        return copy.deepcopy(intent) if intent else None

    def get_by_reference(self, reference: str) -> Optional[dict[str, Any]]:
        self._expire_stale_intents()
        intent = self._payment_repo.get_by_reference(reference)
        return copy.deepcopy(intent) if intent else None

    def get_by_submitted_tx_reference(self, tx_reference: str) -> Optional[dict[str, Any]]:
        """Return an active intent that already uses this proof identifier."""
        self._expire_stale_intents()
        normalized = str(tx_reference or "").strip().lower()
        if not normalized:
            return None
        for intent in self._payment_repo.list():
            if str(intent.get("submitted_tx_reference", "")).strip().lower() == normalized:
                return copy.deepcopy(intent)
        return None

    def get_latest_for_chat(self, chat_id: str) -> Optional[dict[str, Any]]:
        self._expire_stale_intents()
        items = self._payment_repo.list(chat_id=str(chat_id))
        return copy.deepcopy(items[0]) if items else None

    def list_intents(
        self,
        status: Optional[str] = None,
        chat_id: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        self._expire_stale_intents()
        return [
            copy.deepcopy(item)
            for item in self._payment_repo.list(status=status, chat_id=chat_id)
        ]

    def list_pending(self) -> list[dict[str, Any]]:
        self._expire_stale_intents()
        return [
            copy.deepcopy(item)
            for item in self._payment_repo.list()
            if item.get("status") in {"awaiting_payment", "submitted"}
        ]

    def count_pending(self) -> int:
        return len(self.list_pending())

    def _transition(self, reference: str, mutator: Any) -> Optional[dict[str, Any]]:
        self._expire_stale_intents()
        if not self._payment_repo.get_by_reference(reference):
            return None
        self._payment_repo.mutate_by_reference(reference, mutator)
        updated = self._payment_repo.get_by_reference(reference)
        return copy.deepcopy(updated) if updated else None

    def mark_submitted(
        self,
        reference: str,
        tx_reference: str = "",
        note: str = "",
    ) -> Optional[dict[str, Any]]:
        def mutate(intent: dict[str, Any]) -> dict[str, Any] | object:
            if intent.get("status") in {"confirmed", "rejected", "expired", "submitted"}:
                return NO_WRITE
            timestamp = self._now().isoformat()
            intent.update({
                "status": "submitted",
                "submitted_at": timestamp,
                "submitted_tx_reference": tx_reference.strip(),
                "submitted_note": note.strip(),
            })
            self._record_status_event(intent, "submitted", note=note)
            return intent

        return self._transition(reference, mutate)

    def confirm(
        self,
        reference: str,
        confirmed_by: str,
        note: str = "",
    ) -> Optional[dict[str, Any]]:
        def mutate(intent: dict[str, Any]) -> dict[str, Any] | object:
            status = intent.get("status")
            if status == "confirmed" or status not in {"awaiting_payment", "submitted"}:
                return NO_WRITE
            timestamp = self._now().isoformat()
            intent.update({
                "status": "confirmed",
                "confirmed_at": timestamp,
                "confirmed_by": confirmed_by.strip(),
            })
            if note:
                intent["confirmation_note"] = note.strip()
            self._record_status_event(intent, "confirmed", actor=confirmed_by, note=note)
            return intent

        return self._transition(reference, mutate)

    def reject(
        self,
        reference: str,
        rejected_by: str,
        reason: str = "",
    ) -> Optional[dict[str, Any]]:
        def mutate(intent: dict[str, Any]) -> dict[str, Any] | object:
            status = intent.get("status")
            if status == "rejected" or status not in {"awaiting_payment", "submitted"}:
                return NO_WRITE
            timestamp = self._now().isoformat()
            intent.update({
                "status": "rejected",
                "rejected_at": timestamp,
                "rejected_by": rejected_by.strip(),
                "rejection_reason": reason.strip(),
            })
            self._record_status_event(intent, "rejected", actor=rejected_by, note=reason)
            return intent

        return self._transition(reference, mutate)

    def void_confirmed(
        self,
        reference: str,
        voided_by: str,
        reason: str = "",
    ) -> Optional[dict[str, Any]]:
        """Invalidate a confirmed payment after a verification or audit failure."""
        def mutate(intent: dict[str, Any]) -> dict[str, Any] | object:
            if intent.get("status") == "voided":
                return copy.deepcopy(intent)
            if intent.get("status") != "confirmed":
                return NO_WRITE
            timestamp = self._now().isoformat()
            intent.update({
                "status": "voided",
                "voided_at": timestamp,
                "voided_by": voided_by.strip(),
                "void_reason": reason.strip(),
            })
            self._record_status_event(intent, "voided", actor=voided_by, note=reason)
            return intent

        return self._transition(reference, mutate)


class PostgresAuditLog:
    """OperationalAuditLog-compatible append-only PostgreSQL adapter."""

    _SENSITIVE_KEYS = {
        "token", "secret", "password", "wallet", "wallet_address",
        "webhook", "webhook_secret", "jwt", "api_key",
    }

    def __init__(self, audit: PostgresAuditRepository) -> None:
        self._audit_repo = audit

    def _sanitize(self, value: Any, key: str = "") -> Any:
        normalized_key = key.lower()
        if any(marker in normalized_key for marker in self._SENSITIVE_KEYS):
            return "[REDACTED]"
        if isinstance(value, dict):
            return {str(k): self._sanitize(v, str(k)) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [self._sanitize(item) for item in value]
        if isinstance(value, (str, int, float, bool)) or value is None:
            return value
        return str(value)

    def record(
        self,
        actor_chat_id: str,
        action: str,
        *,
        target_chat_id: str = "",
        result: str = "success",
        metadata: Optional[dict[str, Any]] = None,
    ) -> None:
        entry = {
            "event": "operational_audit",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "actor_chat_id": str(actor_chat_id),
            "action": str(action),
            "target_chat_id": str(target_chat_id) if target_chat_id else "",
            "result": str(result),
            "metadata": self._sanitize(metadata or {}),
        }
        self._audit_repo.record(entry)
        logger.info(json.dumps(entry, sort_keys=True))

    def recent(self, limit: int = 20) -> list[dict[str, Any]]:
        return self._audit_repo.list(limit=limit)


class PostgresApprovalEngine:
    """ApprovalEngine-compatible adapter with PostgreSQL queue state."""

    def __init__(self, approvals: PostgresApprovalRepository, config: Any) -> None:
        self._approval_repo = approvals
        self.config = config

    @staticmethod
    def _jsonable(value: Any) -> Any:
        custom_serializer = getattr(value, "to_dict", None)
        if callable(custom_serializer):
            return json.loads(json.dumps(custom_serializer(), default=str))
        if is_dataclass(value):
            value = asdict(value)
        return json.loads(json.dumps(value, default=str))

    @staticmethod
    def _decimal(value: Any) -> Decimal:
        return Decimal(str(value or "0"))

    @staticmethod
    def _datetime(value: Any) -> datetime:
        parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

    def submit_for_approval(
        self,
        request: ExecutionRequest,
        *,
        execution_ready: bool = True,
    ) -> PendingTrade:
        pending = PendingTrade(request=request)
        pending.set_expiration(int(self.config.auto_reject_after_seconds))
        pending.execution_ready = execution_ready
        if not bool(self.config.require_manual):
            pending.status = ApprovalStatus.APPROVED

        request_data = {
            "candidate": self._jsonable(request.candidate),
            "risk_evaluation": self._jsonable(request.risk_evaluation),
            "magic_number": request.magic_number,
            "requested_at": request.requested_at.isoformat(),
            "execution_ready": execution_ready,
        }
        self._approval_repo.upsert({
            "candidate_id": request.candidate.candidate_id,
            "status": pending.status.value,
            "profile": request.candidate.profile,
            "expires_at": pending.expires_at.isoformat(),
            "request": request_data,
            "created_at": pending.created_at.isoformat(),
            "updated_at": pending.created_at.isoformat(),
        })
        return pending

    def _hydrate(self, record: dict[str, Any]) -> PendingTrade:
        from core.models.risk import RiskCalculation, RiskEvaluation
        from core.models.trade_candidate import (
            EntryModel,
            InvalidationModel,
            TargetModel,
            TradeCandidate,
        )

        request_data = record.get("request") or {}
        candidate_data = request_data.get("candidate") or {}
        entry_data = candidate_data.get("entry_model") or {}
        invalidation_data = candidate_data.get("invalidation_model") or {}
        target_data = candidate_data.get("target_model")
        candidate = TradeCandidate(
            candidate_id=str(candidate_data.get("candidate_id", record.get("candidate_id", ""))),
            strategy_name=str(candidate_data.get("strategy_name", "")),
            source_detector_event_ids=tuple(candidate_data.get("source_detector_event_ids") or ()),
            symbol=str(candidate_data.get("symbol", "")),
            timeframe=str(candidate_data.get("timeframe", "")),
            profile=str(candidate_data.get("profile", record.get("profile", ""))),
            direction=str(candidate_data.get("direction", "UNKNOWN")),
            entry_model=EntryModel(
                entry_zone_high=self._decimal(entry_data.get("entry_zone_high")),
                entry_zone_low=self._decimal(entry_data.get("entry_zone_low")),
                reference_price=self._decimal(entry_data.get("reference_price")),
                description=str(entry_data.get("description", "")),
            ),
            invalidation_model=InvalidationModel(
                invalidation_level=self._decimal(invalidation_data.get("invalidation_level")),
                description=str(invalidation_data.get("description", "")),
            ),
            target_model=(TargetModel(
                midpoint_target=self._decimal(target_data.get("midpoint_target")),
                opposite_extreme_target=self._decimal(target_data.get("opposite_extreme_target")),
                description=str(target_data.get("description", "")),
            ) if target_data else None),
            confidence_inputs=candidate_data.get("confidence_inputs") or {},
            explanation=str(candidate_data.get("explanation", "")),
            created_at=self._datetime(candidate_data.get("created_at") or record.get("created_at")),
        )

        risk_data = request_data.get("risk_evaluation") or {}
        calculation_data = risk_data.get("calculation")
        calculation = None
        if isinstance(calculation_data, dict):
            calculation = RiskCalculation(**{
                field: self._decimal(calculation_data.get(field))
                for field in (
                    "sl_distance_price", "sl_distance_pips", "pip_value", "risk_amount",
                    "raw_lot_size", "lot_size", "min_lot", "max_lot", "lot_step",
                )
            })
        risk = RiskEvaluation(
            candidate_id=str(risk_data.get("candidate_id", candidate.candidate_id)),
            approved=bool(risk_data.get("approved", False)),
            lot_size=(self._decimal(risk_data.get("lot_size")) if risk_data.get("lot_size") is not None else None),
            calculation=calculation,
            rejection_code=risk_data.get("rejection_code"),
            reason=str(risk_data.get("reason", "")),
            evaluated_at=self._datetime(risk_data.get("evaluated_at") or record.get("created_at")),
            loss_budget=(
                LossBudgetDecision.from_dict(risk_data["loss_budget"])
                if isinstance(risk_data.get("loss_budget"), dict)
                else None
            ),
        )
        request = ExecutionRequest(
            candidate=candidate,
            risk_evaluation=risk,
            magic_number=int(request_data.get("magic_number", 0)),
            requested_at=self._datetime(request_data.get("requested_at") or record.get("created_at")),
        )
        pending = PendingTrade(request=request)
        pending.status = ApprovalStatus(str(record.get("status", ApprovalStatus.PENDING.value)))
        pending.created_at = self._datetime(record.get("created_at"))
        pending.expires_at = self._datetime(record.get("expires_at"))
        pending.execution_ready = bool(request_data.get("execution_ready", True))
        return pending

    def cleanup_expired(self) -> int:
        now = datetime.now(timezone.utc)
        count = 0
        for record in self._approval_repo.list_pending():
            expires_at = self._datetime(record.get("expires_at"))
            if expires_at < now and self._approval_repo.set_status(
                str(record.get("candidate_id", "")), ApprovalStatus.EXPIRED.value
            ):
                count += 1
        return count

    def get_pending(self, candidate_id: str) -> Optional[PendingTrade]:
        self.cleanup_expired()
        record = self._approval_repo.get(candidate_id, ApprovalStatus.PENDING.value)
        return self._hydrate(record) if record else None

    def get_request(self, candidate_id: str) -> Optional[ExecutionRequest]:
        record = self._approval_repo.get(candidate_id)
        if not record:
            return None
        return self._hydrate(record).request

    def get_status(self, candidate_id: str) -> Optional[ApprovalStatus]:
        record = self._approval_repo.get(candidate_id)
        if not record:
            return None
        return ApprovalStatus(str(record.get("status")))

    def get_all_pending(self, profile: str = None) -> list[PendingTrade]:
        self.cleanup_expired()
        return [
            self._hydrate(record)
            for record in self._approval_repo.list_pending(profile)
        ]

    def get_all_approved_ready(self, profile: str = None) -> list[PendingTrade]:
        return [
            pending
            for pending in (
                self._hydrate(record)
                for record in self._approval_repo.list_by_status(
                    ApprovalStatus.APPROVED.value, profile
                )
            )
            if pending.execution_ready
        ]

    def mark_execution_ready(self, candidate_id: str) -> bool:
        return self._approval_repo.mark_execution_ready(candidate_id)

    def claim_approved(self, candidate_id: str) -> Optional[PendingTrade]:
        record = self._approval_repo.get(candidate_id, ApprovalStatus.APPROVED.value)
        if not record:
            return None
        pending = self._hydrate(record)
        if not pending.execution_ready:
            return None
        if not self._approval_repo.compare_and_set_status(
            candidate_id, ApprovalStatus.APPROVED.value, ApprovalStatus.EXECUTING.value
        ):
            return None
        pending.status = ApprovalStatus.EXECUTING
        return pending

    def mark_execution_status(self, candidate_id: str, status: ApprovalStatus) -> bool:
        if status not in {
            ApprovalStatus.EXECUTED,
            ApprovalStatus.EXECUTION_PENDING_RECONCILIATION,
            ApprovalStatus.EXECUTION_AMBIGUOUS,
            ApprovalStatus.EXECUTION_FAILED,
        }:
            raise ValueError(f"Invalid terminal execution status: {status}")
        return self._approval_repo.compare_and_set_status(
            candidate_id, ApprovalStatus.EXECUTING.value, status.value
        )

    def approve_trade(self, candidate_id: str) -> Optional[ExecutionRequest]:
        self.cleanup_expired()
        record = self._approval_repo.get(candidate_id, ApprovalStatus.PENDING.value)
        if not record or not self._approval_repo.set_status(
            candidate_id, ApprovalStatus.APPROVED.value
        ):
            return None
        return self._hydrate({**record, "status": ApprovalStatus.APPROVED.value}).request

    def reject_trade(self, candidate_id: str) -> bool:
        self.cleanup_expired()
        return self._approval_repo.set_status(candidate_id, ApprovalStatus.REJECTED.value)


class PostgresRuntimeStateStore:
    """Small key/value adapter for scanner state that must survive restarts."""

    def __init__(self, runtime: PostgresRuntimeRepository) -> None:
        self._runtime_repo = runtime

    def get(self, key: str, default: Any = None) -> Any:
        value = self._runtime_repo.get(key)
        return default if value is None else copy.deepcopy(value)

    def set(self, key: str, value: Any) -> None:
        self._runtime_repo.set(key, copy.deepcopy(value))


class PostgresJournal:
    """SQLiteJournal-compatible Signal Passport adapter."""

    def __init__(self, journal: PostgresJournalRepository) -> None:
        self._journal_repo = journal

    @staticmethod
    def _datetime(value: Any) -> datetime:
        parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

    @staticmethod
    def _entry(record: dict[str, Any]) -> JournalEntry:
        return JournalEntry(
            record_id=str(record.get("record_id", "")),
            candidate_id=str(record.get("candidate_id", "")),
            symbol=str(record.get("symbol", "")),
            timeframe=str(record.get("timeframe", "")),
            profile=str(record.get("profile", "")),
            strategy_name=str(record.get("strategy_name", "")),
            direction=str(record.get("direction", "")),
            confidence_score=float(record.get("confidence_score") or 0),
            confidence_breakdown=record.get("confidence_breakdown") or {},
            lot_size=Decimal(str(record.get("lot_size") or "0")),
            entry_price=Decimal(str(record.get("entry_price") or "0")),
            sl_price=Decimal(str(record.get("sl_price") or "0")),
            tp_price=Decimal(str(record.get("tp_price") or "0")),
            exit_price=Decimal(str(record.get("exit_price") or "0")),
            pnl=Decimal(str(record.get("pnl") or "0")),
            closed_at=PostgresJournal._datetime(record.get("closed_at")),
            exit_reason=str(record.get("exit_reason", "")),
            tags=list(record.get("tags") or []),
        )

    def add_entry(self, entry: JournalEntry) -> None:
        self._journal_repo.upsert({
            "record_id": entry.record_id,
            "candidate_id": entry.candidate_id,
            "symbol": entry.symbol,
            "timeframe": entry.timeframe,
            "profile": entry.profile,
            "strategy_name": entry.strategy_name,
            "direction": entry.direction,
            "confidence_score": entry.confidence_score,
            "confidence_breakdown": entry.confidence_breakdown,
            "lot_size": str(entry.lot_size),
            "entry_price": str(entry.entry_price),
            "sl_price": str(entry.sl_price),
            "tp_price": str(entry.tp_price),
            "exit_price": str(entry.exit_price),
            "pnl": str(entry.pnl),
            "closed_at": entry.closed_at.isoformat(),
            "exit_reason": entry.exit_reason,
            "tags": entry.tags,
            "signal_status": "RESOLVED",
            "final_outcome": "WIN" if entry.pnl > 0 else "LOSS" if entry.pnl < 0 else "UNKNOWN",
            "created_at": entry.closed_at.isoformat(),
            "outcome_at": entry.closed_at.isoformat(),
        })

    def get_entries(self, profile: Optional[str] = None) -> list[JournalEntry]:
        return [self._entry(record) for record in self._journal_repo.list(profile=profile)]

    def get_signal_outcome(self, record_id: str) -> dict[str, Any] | None:
        return self._journal_repo.get_signal_outcome(record_id)

    def record_execution(self, record: ExecutionRecord) -> None:
        self._journal_repo.record_execution(record)
        logger.info(
            '{"event":"execution_record_persisted","record_id":"%s",'
            '"candidate_id":"%s","status":"%s"}',
            str(record.record_id),
            str(record.candidate_id),
            getattr(record.status, "value", str(record.status)),
        )

    def record_execution_quality_event(self, event) -> None:
        self._journal_repo.record_execution_quality_event(event)

    def list_execution_quality_events(
        self,
        execution_record_id: Optional[str] = None,
        *,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        return self._journal_repo.list_execution_quality_events(
            execution_record_id,
            limit=limit,
        )

    def get_execution_record(self, record_id: str) -> Optional[dict[str, Any]]:
        return self._journal_repo.get_execution_record(record_id)

    def link_execution_position(self, record_id: str, position_id: int) -> bool:
        return bool(self._journal_repo.link_execution_position(record_id, position_id))

    def list_unsettled_executions(self) -> list[dict[str, Any]]:
        return self._journal_repo.list_unsettled_executions()

    def record_execution_realization(
        self,
        record_id: str,
        reconciliation: Any,
        realized_at: Optional[datetime] = None,
    ) -> bool:
        return bool(
            self._journal_repo.record_execution_realization(
                record_id, reconciliation, realized_at
            )
        )

    def add_signal_entry(
        self,
        record_id: str,
        candidate_id: str,
        symbol: str,
        timeframe: str,
        profile: str,
        direction: str,
        confidence_score: float,
        confidence_breakdown: dict,
        entry_price: Decimal,
        sl_price: Decimal,
        tp_price: Decimal,
        atr_quality: Optional[ATRQualityResult] = None,
        key_level_evaluation: Optional[dict] = None,
        volume_profile: Optional[VolumeProfileResult] = None,
        volume_profiles: Optional[tuple[VolumeProfileResult, ...]] = None,
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        atr_fields = atr_quality_fields(atr_quality)
        key_level_fields = key_level_passport_fields(key_level_evaluation)
        volume_profile_fields = volume_profile_passport_fields(
            volume_profiles if volume_profiles is not None else volume_profile
        )
        self._journal_repo.upsert({
            "record_id": record_id,
            "candidate_id": candidate_id,
            "symbol": symbol,
            "timeframe": timeframe,
            "profile": profile,
            "strategy_name": "CRT",
            "direction": direction,
            "confidence_score": confidence_score,
            "confidence_breakdown": confidence_breakdown,
            "lot_size": "0",
            "entry_price": str(entry_price),
            "sl_price": str(sl_price),
            "tp_price": str(tp_price),
            "exit_price": "0",
            "pnl": "0",
            "closed_at": now,
            "exit_reason": "signal_pending",
            "tags": ["signal"],
            "signal_status": "OPEN",
            "invalidation_reason": "",
            "final_outcome": "UNKNOWN",
            "created_at": now,
            "outcome_at": None,
            **atr_fields,
            **key_level_fields,
            **volume_profile_fields,
        })
        logger.info(
            '{"event":"journal_signal_entry","record_id":"%s","symbol":"%s","direction":"%s"}',
            record_id,
            symbol,
            direction,
        )

    def update_signal_outcome(
        self,
        record_id: str,
        exit_reason: str,
        exit_price: Decimal,
        pnl: Decimal,
    ) -> None:
        self._journal_repo.update_signal_outcome(record_id, exit_reason, exit_price, pnl)
        logger.info(
            '{"event":"journal_signal_outcome","record_id":"%s","exit_reason":"%s"}',
            record_id,
            exit_reason,
        )

    def mark_signal_expired(self, record_id: str) -> None:
        self._journal_repo.mark_signal_expired(record_id)
        logger.info('{"event":"journal_signal_expired","record_id":"%s"}', record_id)

    def append_note_for_symbol(self, symbol: str, note_text: str) -> bool:
        return bool(self._journal_repo.append_note_for_symbol(symbol, note_text))
