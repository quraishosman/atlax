"""
Persistent payment intent store for ATLAX subscription purchases.

The store keeps a small JSON ledger of payment attempts so the Telegram
bot, the admin callbacks, and any future webhook integrations all speak to
the same payment state.
"""

from __future__ import annotations

import json
import logging
import secrets
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("atlax.payments.store")


class PaymentStore:
    """Simple JSON-backed payment intent store."""

    def __init__(self, path: str = "data/payment_intents.json") -> None:
        self._path = Path(path)
        self._intents: dict[str, dict[str, Any]] = {}
        self._load()

    def _now(self) -> datetime:
        return datetime.now(timezone.utc)

    def _load(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        if not self._path.exists():
            return
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                self._intents = data
        except Exception as exc:  # noqa: BLE001
            logger.error("Failed to load payment intents: %s", exc)
            self._intents = {}
        self._expire_stale_intents(persist=False)

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(
            json.dumps(self._intents, indent=2, sort_keys=True),
            encoding="utf-8",
        )

    def _reference_exists(self, reference: str) -> bool:
        return any(
            intent.get("reference") == reference
            for intent in self._intents.values()
        )

    def _generate_reference(self, prefix: str, method: str) -> str:
        # CR is the approved checkout-reference tag for every subscription
        # rail; the stored method field identifies the actual payment rail.
        return f"{prefix.strip().upper()}-CR-{secrets.token_hex(3).upper()}"

    def _parse_dt(self, value: Any) -> Optional[datetime]:
        if not value:
            return None
        try:
            parsed = datetime.fromisoformat(str(value))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc)
        except Exception:  # noqa: BLE001
            return None

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
        event = {
            "status": status,
            "at": self._now().isoformat(),
        }
        if actor.strip():
            event["actor"] = actor.strip()
        if note.strip():
            event["note"] = note.strip()
        history.append(event)

    def _expire_stale_intents(self, persist: bool = True) -> list[dict[str, Any]]:
        changed = False
        expired: list[dict[str, Any]] = []
        now = self._now()
        for intent in self._intents.values():
            if intent.get("status") in {"confirmed", "rejected", "expired"}:
                continue
            expires_at = self._parse_dt(intent.get("expires_at"))
            if expires_at and now > expires_at:
                intent["status"] = "expired"
                intent["expired_at"] = now.isoformat()
                intent["updated_at"] = now.isoformat()
                self._record_status_event(intent, "expired")
                expired.append(deepcopy(intent))
                changed = True
        if changed and persist:
            self._save()
        return expired

    def expire_stale_intents(self) -> list[dict[str, Any]]:
        """Expire open payment intents and return the records changed now."""
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

        intent_id = f"pi_{secrets.token_hex(8)}"
        record = {
            "intent_id": intent_id,
            "reference": reference,
            "chat_id": str(chat_id),
            "username": username,
            "method": method,
            "style": style,
            "subscription_plan": str(subscription_plan or "standard").strip().lower(),
            "amount_usd": str(amount_usd),
            "payment_destination": deepcopy(payment_destination),
            "support_contact": support_contact,
            "note": note,
            "status": "awaiting_payment",
            "status_history": [{
                "status": "awaiting_payment",
                "at": now.isoformat(),
            }],
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
        self._intents[intent_id] = record
        self._save()
        return deepcopy(record)

    def get_intent(self, intent_id: str) -> Optional[dict[str, Any]]:
        self._expire_stale_intents()
        intent = self._intents.get(intent_id)
        return deepcopy(intent) if intent else None

    def get_by_reference(self, reference: str) -> Optional[dict[str, Any]]:
        self._expire_stale_intents()
        reference = reference.strip().upper()
        for intent in self._intents.values():
            if intent.get("reference", "").upper() == reference:
                return deepcopy(intent)
        return None

    def get_by_submitted_tx_reference(self, tx_reference: str) -> Optional[dict[str, Any]]:
        """Return an active intent that already uses this proof identifier."""
        self._expire_stale_intents()
        normalized = str(tx_reference or "").strip().lower()
        if not normalized:
            return None
        for intent in self._intents.values():
            if str(intent.get("submitted_tx_reference", "")).strip().lower() == normalized:
                return deepcopy(intent)
        return None

    def get_latest_for_chat(self, chat_id: str) -> Optional[dict[str, Any]]:
        self._expire_stale_intents()
        chat_id = str(chat_id)
        candidates = [
            intent
            for intent in self._intents.values()
            if intent.get("chat_id") == chat_id
        ]
        if not candidates:
            return None
        candidates.sort(key=lambda item: item.get("created_at", ""), reverse=True)
        return deepcopy(candidates[0])

    def list_intents(
        self,
        status: Optional[str] = None,
        chat_id: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        self._expire_stale_intents()
        items = list(self._intents.values())
        if status is not None:
            items = [item for item in items if item.get("status") == status]
        if chat_id is not None:
            items = [item for item in items if item.get("chat_id") == str(chat_id)]
        items.sort(key=lambda item: item.get("created_at", ""), reverse=True)
        return [deepcopy(item) for item in items]

    def list_pending(self) -> list[dict[str, Any]]:
        self._expire_stale_intents()
        return [
            deepcopy(intent)
            for intent in self._intents.values()
            if intent.get("status") in {"awaiting_payment", "submitted"}
        ]

    def count_pending(self) -> int:
        return len(self.list_pending())

    def mark_submitted(
        self,
        reference: str,
        tx_reference: str = "",
        note: str = "",
    ) -> Optional[dict[str, Any]]:
        self._expire_stale_intents()
        reference = reference.strip().upper()
        for intent_id, intent in self._intents.items():
            if intent.get("reference", "").upper() != reference:
                continue
            if intent.get("status") in {"confirmed", "rejected", "expired"}:
                return deepcopy(intent)
            if intent.get("status") == "submitted":
                return deepcopy(intent)
            now = self._now().isoformat()
            intent["status"] = "submitted"
            intent["submitted_at"] = now
            intent["submitted_tx_reference"] = tx_reference.strip()
            intent["submitted_note"] = note.strip()
            intent["updated_at"] = now
            self._record_status_event(intent, "submitted", note=note)
            self._intents[intent_id] = intent
            self._save()
            return deepcopy(intent)
        return None

    def confirm(self, reference: str, confirmed_by: str, note: str = "") -> Optional[dict[str, Any]]:
        self._expire_stale_intents()
        reference = reference.strip().upper()
        for intent_id, intent in self._intents.items():
            if intent.get("reference", "").upper() != reference:
                continue
            if intent.get("status") == "confirmed":
                return deepcopy(intent)
            if intent.get("status") not in {"awaiting_payment", "submitted"}:
                return None
            now = self._now().isoformat()
            intent["status"] = "confirmed"
            intent["confirmed_at"] = now
            intent["confirmed_by"] = confirmed_by.strip()
            if note:
                intent["confirmation_note"] = note.strip()
            intent["updated_at"] = now
            self._record_status_event(intent, "confirmed", actor=confirmed_by, note=note)
            self._intents[intent_id] = intent
            self._save()
            return deepcopy(intent)
        return None

    def void_confirmed(
        self,
        reference: str,
        voided_by: str,
        reason: str = "",
    ) -> Optional[dict[str, Any]]:
        """Invalidate a confirmed payment after a verification or audit failure."""
        self._expire_stale_intents()
        reference = reference.strip().upper()
        for intent_id, intent in self._intents.items():
            if intent.get("reference", "").upper() != reference:
                continue
            if intent.get("status") == "voided":
                return deepcopy(intent)
            if intent.get("status") != "confirmed":
                return None
            now = self._now().isoformat()
            intent["status"] = "voided"
            intent["voided_at"] = now
            intent["voided_by"] = voided_by.strip()
            intent["void_reason"] = reason.strip()
            intent["updated_at"] = now
            self._record_status_event(intent, "voided", actor=voided_by, note=reason)
            self._intents[intent_id] = intent
            self._save()
            return deepcopy(intent)
        return None

    def reject(self, reference: str, rejected_by: str, reason: str = "") -> Optional[dict[str, Any]]:
        self._expire_stale_intents()
        reference = reference.strip().upper()
        for intent_id, intent in self._intents.items():
            if intent.get("reference", "").upper() != reference:
                continue
            if intent.get("status") == "rejected":
                return deepcopy(intent)
            if intent.get("status") not in {"awaiting_payment", "submitted"}:
                return None
            now = self._now().isoformat()
            intent["status"] = "rejected"
            intent["rejected_at"] = now
            intent["rejected_by"] = rejected_by.strip()
            intent["rejection_reason"] = reason.strip()
            intent["updated_at"] = now
            self._record_status_event(intent, "rejected", actor=rejected_by, note=reason)
            self._intents[intent_id] = intent
            self._save()
            return deepcopy(intent)
        return None
