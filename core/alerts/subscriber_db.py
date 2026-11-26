"""
ATLAX Subscriber Database.

Manages invite codes, paid subscriptions, and registered subscribers for
the ATLAX SaaS signal system.

Files:
    data/invite_codes.json  - Admin-managed invite codes with expiry
    data/subscribers.json   - Registered subscribers with style preferences

Trading Style -> Timeframe Mapping:
    scalper      -> M5
    day_trader   -> M15, H1
    swing_trader -> H4, D1
    all_styles   -> M5, M15, H1, H4, D1
"""

from __future__ import annotations

import json
import logging
import os
import secrets
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from core.alerts.i18n import normalize_language
from core.analytics.google_sheets_reporting import report_subscriber_event

logger = logging.getLogger("atlax.subscribers")

STYLE_TIMEFRAMES: Dict[str, List[str]] = {
    "scalper": ["M5"],
    "day_trader": ["M15", "H1"],
    "swing_trader": ["H4", "D1"],
    "all_styles": ["M5", "M15", "H1", "H4", "D1"],
}

STYLE_LABELS = {
    "scalper": "\U0001F3C3 Scalper (M5)",
    "swing_trader": "\U0001F30A Swing Trader (H4/D1)",
    "all_styles": "\U0001F5C2 All Styles (M5/M15/H1/H4/D1)",
    "day_trader": "\U0001F4C5 Day Trader (M15/H1)",
}

SUBSCRIPTION_PLANS = ("standard", "premium")
_PREMIUM_ONLY_SETTINGS = ("confluence_only", "volume_profile_confluence_only")


class SubscriberDB:
    """Manages ATLAX subscribers and invite codes."""

    def __init__(self, codes_file: str = "data/invite_codes.json", subs_file: str = "data/subscribers.json"):
        self.CODES_FILE = codes_file
        self.SUBS_FILE = subs_file
        self.LANGUAGE_FILE = os.path.join(
            os.path.dirname(subs_file) or ".",
            "language_preferences.json",
        )
        self._codes: Dict[str, Any] = {}
        self._subs: Dict[str, Any] = {}
        self._languages: Dict[str, str] = {}
        self._load()

    # ------------------------------------------------------------------
    # Load / Save
    # ------------------------------------------------------------------

    def _normalize_timeframes(self, record: Dict[str, Any]) -> bool:
        """Remove retired timeframe routes from persisted subscriber records."""
        style = str(record.get("style", ""))
        if style in STYLE_TIMEFRAMES:
            normalized = list(STYLE_TIMEFRAMES[style])
        else:
            allowed = {
                timeframe
                for timeframes in STYLE_TIMEFRAMES.values()
                for timeframe in timeframes
            }
            normalized = [
                str(timeframe)
                for timeframe in record.get("timeframes", []) or []
                if str(timeframe) in allowed
            ]
        if record.get("timeframes") == normalized:
            return False
        record["timeframes"] = normalized
        return True

    def _normalize_plan_settings(self, record: Dict[str, Any]) -> bool:
        """Remove Premium-only filters from Standard and legacy records.

        Older subscriber records can outlive the menu entitlement checks that
        created them.  Keeping those flags active would make a Standard user
        silently receive no alerts because the delivery layer correctly
        rejects Premium-only routing.  Normalize the stored profile at the
        persistence boundary so plan changes and scanner restarts converge.
        """
        plan = str(record.get("subscription_plan", "standard")).strip().lower()
        if plan == "premium":
            return False
        settings = record.get("settings")
        if not isinstance(settings, dict):
            settings = {}
            record["settings"] = settings
        changed = False
        for key in _PREMIUM_ONLY_SETTINGS:
            if settings.get(key) is True:
                settings[key] = False
                changed = True
        return changed

    def _load(self) -> None:
        os.makedirs("data", exist_ok=True)
        changed = False
        for path, attr in [
            (self.CODES_FILE, "_codes"),
            (self.SUBS_FILE, "_subs"),
            (self.LANGUAGE_FILE, "_languages"),
        ]:
            if os.path.exists(path):
                try:
                    with open(path, "r", encoding="utf-8") as f:
                        loaded = json.load(f)
                    if attr == "_subs" and isinstance(loaded, dict):
                        for record in loaded.values():
                            if isinstance(record, dict):
                                changed = self._normalize_timeframes(record) or changed
                                changed = self._normalize_plan_settings(record) or changed
                    if attr == "_languages" and isinstance(loaded, dict):
                        setattr(
                            self,
                            attr,
                            {
                                str(chat_id): normalize_language(language)
                                for chat_id, language in loaded.items()
                            },
                        )
                    else:
                        setattr(self, attr, loaded)
                except Exception as exc:  # noqa: BLE001
                    logger.error("Failed to load %s: %s", path, exc)
        if changed:
            self._save()

    def _save(self) -> None:
        for path, attr in [
            (self.CODES_FILE, "_codes"),
            (self.SUBS_FILE, "_subs"),
            (self.LANGUAGE_FILE, "_languages"),
        ]:
            try:
                os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(getattr(self, attr), f, indent=2)
            except Exception as exc:  # noqa: BLE001
                logger.error("Failed to save %s: %s", path, exc)

    def _parse_datetime(self, value: Optional[str]) -> Optional[datetime]:
        if not value:
            return None
        try:
            parsed = datetime.fromisoformat(str(value))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc)
        except Exception:  # noqa: BLE001
            return None

    def _ensure_settings(self, record: Dict[str, Any]) -> None:
        settings = record.get("settings")
        if not isinstance(settings, dict):
            settings = {}
            record["settings"] = settings
        settings.setdefault("symbols", [])
        settings.setdefault("min_confidence", None)
        settings.setdefault("notifications", "all")
        settings.setdefault("key_level_only", False)
        settings.setdefault("high_quality_only", False)
        settings.setdefault("confluence_only", False)
        settings.setdefault("volume_profile_confluence_only", False)
        settings.setdefault("language", "en")

    def _mark_expired_if_needed(self, chat_id: str, record: Dict[str, Any]) -> bool:
        expires_at = self._parse_datetime(record.get("subscription_expires_at"))
        if expires_at and datetime.now(timezone.utc) >= expires_at:
            now = datetime.now(timezone.utc).isoformat()
            record["active"] = False
            record["subscription_status"] = "expired"
            record["subscription_expired_at"] = now
            record["subscription_expiry_notice_sent"] = False
            record["updated_at"] = now
            self._subs[chat_id] = record
            self._save()
            return True
        return False

    # ------------------------------------------------------------------
    # Invite Code Management
    # ------------------------------------------------------------------

    def generate_code(self, label: str = "", days_valid: int = 30) -> str:
        code = "ATLAX-" + secrets.token_hex(4).upper()
        expires = (datetime.now(timezone.utc) + timedelta(days=days_valid)).isoformat()
        self._codes[code] = {
            "label": label,
            "expires": expires,
            "used_by": None,
            "used_at": None,
            "days_valid": days_valid,
        }
        self._save()
        logger.info("Generated invite code: %s (valid %s days, label=%r)", code, days_valid, label)
        return code

    def validate_code(self, code: str) -> tuple[bool, str]:
        code = code.strip().upper()
        if code not in self._codes:
            return False, "Invalid code."
        entry = self._codes[code]
        if entry["used_by"] is not None:
            return False, "Code already used."
        expires = datetime.fromisoformat(entry["expires"])
        if datetime.now(timezone.utc) > expires:
            return False, "Code expired."
        return True, "OK"

    def consume_code(self, code: str, chat_id: str) -> None:
        code = code.strip().upper()
        if code in self._codes:
            self._codes[code]["used_by"] = chat_id
            self._codes[code]["used_at"] = datetime.now(timezone.utc).isoformat()
            self._save()

    def revoke_code(self, code: str) -> None:
        self._codes.pop(code.strip().upper(), None)
        self._save()

    def list_codes(self) -> List[Dict]:
        return [{"code": k, **v} for k, v in self._codes.items()]

    # ------------------------------------------------------------------
    # Subscriber Management
    # ------------------------------------------------------------------

    def register(
        self,
        chat_id: str,
        username: str,
        style: str,
        invite_code: str,
        subscription_days: Optional[int] = None,
        subscription_source: str = "invite_code",
        payment_method: str = "",
        payment_reference: str = "",
        subscription_plan: str = "standard",
    ) -> bool:
        if style not in STYLE_TIMEFRAMES:
            return False

        subscription_plan = str(subscription_plan or "standard").strip().lower()
        if subscription_plan not in SUBSCRIPTION_PLANS:
            return False

        chat_id = str(chat_id)
        now = datetime.now(timezone.utc)
        is_new_subscriber = chat_id not in self._subs
        existing = dict(self._subs.get(chat_id, {}))
        self._ensure_settings(existing)
        if chat_id not in self._subs and chat_id in self._languages:
            existing["settings"]["language"] = self._languages[chat_id]

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

        existing_expiry = self._parse_datetime(existing.get("subscription_expires_at"))
        if subscription_days is None:
            if existing_expiry and existing_expiry > now:
                record["subscription_expires_at"] = existing_expiry.isoformat()
            else:
                record.pop("subscription_expires_at", None)
        else:
            base = existing_expiry if existing_expiry and existing_expiry > now else now
            expires_at = base + timedelta(days=subscription_days)
            record["subscription_expires_at"] = expires_at.isoformat()
            record["subscription_duration_days"] = subscription_days
            record["subscription_reminders_sent"] = {}
            record["subscription_expiry_notice_sent"] = False

        self._subs[chat_id] = record
        self._save()
        try:
            report_subscriber_event(
                record,
                "subscriber_created" if is_new_subscriber else "subscriber_activated",
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                '{"event":"google_sheets_subscriber_report_failed",'
                '"chat_id":"%s","error":"%s"}',
                chat_id[:80],
                str(exc)[:160],
            )
        logger.info("New subscriber: %s (%s), style=%s", username, chat_id, style)
        return True

    def expire_subscriptions(self, now: Optional[datetime] = None) -> List[Dict]:
        """Suspend all finite subscriptions whose expiry has been reached."""
        current = now or datetime.now(timezone.utc)
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        current = current.astimezone(timezone.utc)
        changed: List[Dict] = []
        dirty = False

        for chat_id, record in list(self._subs.items()):
            if not record.get("active"):
                continue
            expires_at = self._parse_datetime(record.get("subscription_expires_at"))
            if expires_at is None or current < expires_at:
                continue
            now_iso = current.isoformat()
            record["active"] = False
            record["subscription_status"] = "expired"
            record["subscription_expired_at"] = now_iso
            record["subscription_expiry_notice_sent"] = False
            record["updated_at"] = now_iso
            self._subs[chat_id] = record
            changed.append(deepcopy(record))
            dirty = True

        if dirty:
            self._save()
            for record in changed:
                try:
                    report_subscriber_event(record, "subscriber_expired")
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        '{"event":"google_sheets_subscriber_report_failed",'
                        '"chat_id":"%s","error":"%s"}',
                        str(record.get("chat_id", ""))[:80],
                        str(exc)[:160],
                    )
                logger.info(
                    '{"event":"subscription_expired","chat_id":"%s","expired_at":"%s"}',
                    str(record.get("chat_id", ""))[:80],
                    str(record.get("subscription_expired_at", "")),
                )
        return changed

    def mark_subscription_reminder_sent(
        self,
        chat_id: str,
        reminder_hours: int,
        sent_at: Optional[datetime] = None,
    ) -> bool:
        """Persist one reminder marker for the current subscription cycle."""
        record = self._subs.get(str(chat_id))
        if not record or not record.get("active"):
            return False
        reminders = record.setdefault("subscription_reminders_sent", {})
        if not isinstance(reminders, dict):
            reminders = {}
            record["subscription_reminders_sent"] = reminders
        key = str(int(reminder_hours))
        if key in reminders:
            return False
        timestamp = sent_at or datetime.now(timezone.utc)
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=timezone.utc)
        reminders[key] = timestamp.astimezone(timezone.utc).isoformat()
        record["updated_at"] = timestamp.astimezone(timezone.utc).isoformat()
        self._subs[str(chat_id)] = record
        self._save()
        return True

    def mark_subscription_expiry_notice_sent(self, chat_id: str) -> bool:
        """Mark the post-expiry access notice after Telegram accepts it."""
        record = self._subs.get(str(chat_id))
        if not record or record.get("subscription_status") != "expired":
            return False
        if record.get("subscription_expiry_notice_sent") is True:
            return False
        record["subscription_expiry_notice_sent"] = True
        record["updated_at"] = datetime.now(timezone.utc).isoformat()
        self._subs[str(chat_id)] = record
        self._save()
        return True

    def activate_subscription(
        self,
        chat_id: str,
        username: str,
        style: str,
        subscription_days: int,
        payment_method: str,
        payment_reference: str,
        invite_code: str = "",
        subscription_source: str = "paid",
        subscription_plan: str = "standard",
    ) -> bool:
        return self.register(
            chat_id=chat_id,
            username=username,
            style=style,
            invite_code=invite_code,
            subscription_days=subscription_days,
            subscription_source=subscription_source,
            payment_method=payment_method,
            payment_reference=payment_reference,
            subscription_plan=subscription_plan,
        )

    def update_style(self, chat_id: str, style: str) -> bool:
        if style not in STYLE_TIMEFRAMES:
            return False
        chat_id = str(chat_id)
        if chat_id in self._subs:
            self._subs[chat_id]["style"] = style
            self._subs[chat_id]["timeframes"] = STYLE_TIMEFRAMES[style]
            self._subs[chat_id]["updated_at"] = datetime.now(timezone.utc).isoformat()
            self._save()
            return True
        return False

    def get_subscriber(self, chat_id: str) -> Optional[Dict]:
        return self._subs.get(str(chat_id))

    def get_subscription_plan(self, chat_id: str) -> str:
        """Return the persisted plan, defaulting legacy records safely."""
        record = self._subs.get(str(chat_id)) or {}
        plan = str(record.get("subscription_plan", "standard")).strip().lower()
        return plan if plan in SUBSCRIPTION_PLANS else "unknown"

    def update_subscription_plan(self, chat_id: str, plan: str) -> bool:
        """Update a plan through an explicit administrative/service path."""
        normalized = str(plan or "").strip().lower()
        if normalized not in SUBSCRIPTION_PLANS:
            return False
        record = self._subs.get(str(chat_id))
        if not record:
            return False
        record["subscription_plan"] = normalized
        if normalized != "premium":
            self._normalize_plan_settings(record)
        record["updated_at"] = datetime.now(timezone.utc).isoformat()
        self._subs[str(chat_id)] = record
        self._save()
        return True

    def get_subscription_expires_at(self, chat_id: str) -> Optional[datetime]:
        sub = self._subs.get(str(chat_id))
        if not sub:
            return None
        return self._parse_datetime(sub.get("subscription_expires_at"))

    def is_registered(self, chat_id: str) -> bool:
        chat_id = str(chat_id)
        sub = self._subs.get(chat_id)
        if not sub or not sub.get("active", False):
            return False
        if self._mark_expired_if_needed(chat_id, sub):
            return False
        return True

    def deactivate(self, chat_id: str) -> None:
        chat_id = str(chat_id)
        if chat_id in self._subs:
            self._subs[chat_id]["active"] = False
            self._subs[chat_id]["subscription_status"] = "inactive"
            self._subs[chat_id]["updated_at"] = datetime.now(timezone.utc).isoformat()
            self._save()
            try:
                report_subscriber_event(self._subs[chat_id], "subscriber_deactivated")
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    '{"event":"google_sheets_subscriber_report_failed",'
                    '"chat_id":"%s","error":"%s"}',
                    chat_id[:80],
                    str(exc)[:160],
                )
            logger.info("Deactivated subscriber: %s", chat_id)

    def update_settings(self, chat_id: str, key: str, value) -> bool:
        chat_id = str(chat_id)
        if chat_id not in self._subs:
            return False
        if key == "language":
            value = normalize_language(value)
        if key in {"key_level_only", "high_quality_only", "confluence_only", "volume_profile_confluence_only"} and not isinstance(value, bool):
            return False
        if (
            key in _PREMIUM_ONLY_SETTINGS
            and value is True
            and self.get_subscription_plan(chat_id) != "premium"
        ):
            logger.info(
                '{"event":"subscriber_setting_rejected",'
                '"chat_id":"%s","setting":"%s",'
                '"reason":"premium_plan_required"}',
                chat_id[:80],
                key,
            )
            return False
        self._ensure_settings(self._subs[chat_id])
        self._subs[chat_id]["settings"][key] = value
        self._subs[chat_id]["updated_at"] = datetime.now(timezone.utc).isoformat()
        self._save()
        logger.info("Updated settings for %s: %s=%s", chat_id, key, value)
        return True

    def set_language(self, chat_id: str, language: str) -> bool:
        """Persist a language choice for registered or unregistered users."""
        chat_id = str(chat_id)
        normalized = normalize_language(language)
        self._languages[chat_id] = normalized
        record = self._subs.get(chat_id)
        if isinstance(record, dict):
            self._ensure_settings(record)
            record["settings"]["language"] = normalized
            record["updated_at"] = datetime.now(timezone.utc).isoformat()
        self._save()
        logger.info("Updated language preference for %s: %s", chat_id, normalized)
        return True

    def get_language(self, chat_id: str) -> str:
        """Return a persisted language choice with an English fallback."""
        chat_id = str(chat_id)
        record = self._subs.get(chat_id) or {}
        settings = record.get("settings") if isinstance(record, dict) else {}
        if isinstance(settings, dict) and settings.get("language"):
            return normalize_language(settings.get("language"))
        return normalize_language(self._languages.get(chat_id, "en"))

    def get_settings(self, chat_id: str) -> Dict:
        sub = self._subs.get(str(chat_id), {})
        settings = sub.get("settings")
        if not isinstance(settings, dict):
            settings = {}
        premium = self.get_subscription_plan(chat_id) == "premium"
        return {
            "symbols": settings.get("symbols", []),
            "min_confidence": settings.get("min_confidence"),
            "notifications": settings.get("notifications", "all"),
            "key_level_only": settings.get("key_level_only", False) is True,
            "high_quality_only": settings.get("high_quality_only", False) is True,
            "confluence_only": premium and settings.get("confluence_only", False) is True,
            "volume_profile_confluence_only": premium and settings.get("volume_profile_confluence_only", False) is True,
            "language": self.get_language(chat_id),
        }

    # ------------------------------------------------------------------
    # ATLAX AI abuse controls
    # ------------------------------------------------------------------

    def reserve_ai_request(
        self,
        chat_id: str,
        *,
        daily_limit: int,
        per_minute_limit: int,
        cooldown_seconds: int,
        active_timeout_seconds: int,
    ) -> Dict[str, Any]:
        """Atomically reserve one persistent ATLAX AI request slot."""
        chat_id = str(chat_id)
        record = self._subs.get(chat_id)
        if not record or not self.is_registered(chat_id):
            return {"allowed": False, "reason": "inactive"}

        security = record.setdefault("ai_security", {})
        if security.get("blocked", False):
            return {"allowed": False, "reason": "blocked"}

        now = datetime.now(timezone.utc)
        now_iso = now.isoformat()
        today = now.date().isoformat()
        usage = record.setdefault("ai_usage", {})
        if usage.get("date") != today:
            usage.update({"date": today, "daily_count": 0, "minute_count": 0, "minute_window_started_at": now_iso})

        active_started = self._parse_datetime(usage.get("active_started_at"))
        if usage.get("active_request"):
            if active_started and (now - active_started).total_seconds() < active_timeout_seconds:
                return {"allowed": False, "reason": "active_request"}
            usage["active_request"] = False
            usage["active_started_at"] = None

        last_request = self._parse_datetime(usage.get("last_request_at"))
        if last_request:
            elapsed = (now - last_request).total_seconds()
            if elapsed < cooldown_seconds:
                return {
                    "allowed": False,
                    "reason": "cooldown",
                    "retry_after_seconds": max(1, int(cooldown_seconds - elapsed)),
                }

        if int(usage.get("daily_count", 0)) >= daily_limit:
            return {"allowed": False, "reason": "daily_limit"}

        window_started = self._parse_datetime(usage.get("minute_window_started_at"))
        if not window_started or (now - window_started).total_seconds() >= 60:
            usage["minute_window_started_at"] = now_iso
            usage["minute_count"] = 0
            window_started = now
        if int(usage.get("minute_count", 0)) >= per_minute_limit:
            return {
                "allowed": False,
                "reason": "minute_limit",
                "retry_after_seconds": max(1, int(60 - (now - window_started).total_seconds())),
            }

        usage["date"] = today
        usage["daily_count"] = int(usage.get("daily_count", 0)) + 1
        usage["minute_count"] = int(usage.get("minute_count", 0)) + 1
        usage["last_request_at"] = now_iso
        usage["active_request"] = True
        usage["active_started_at"] = now_iso
        record["ai_usage"] = usage
        self._subs[chat_id] = record
        self._save()
        return {
            "allowed": True,
            "daily_count": usage["daily_count"],
            "daily_limit": daily_limit,
        }

    def release_ai_request(self, chat_id: str) -> None:
        """Release the active request marker after provider completion."""
        record = self._subs.get(str(chat_id))
        if not record:
            return
        usage = record.setdefault("ai_usage", {})
        usage["active_request"] = False
        usage["active_started_at"] = None
        record["updated_at"] = datetime.now(timezone.utc).isoformat()
        self._save()

    def get_ai_usage(self, chat_id: str) -> Dict[str, Any]:
        """Return non-sensitive AI quota and abuse state for admin controls."""
        record = self._subs.get(str(chat_id), {})
        usage = record.get("ai_usage", {})
        security = record.get("ai_security", {})
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
        """Reset quota counters without changing abuse strikes or block state."""
        chat_id = str(chat_id)
        record = self._subs.get(chat_id)
        if not record:
            return False
        now_iso = datetime.now(timezone.utc).isoformat()
        record["ai_usage"] = {
            "date": datetime.now(timezone.utc).date().isoformat(),
            "daily_count": 0,
            "minute_count": 0,
            "minute_window_started_at": now_iso,
            "last_request_at": None,
            "active_request": False,
            "active_started_at": None,
        }
        record["updated_at"] = now_iso
        self._save()
        return True

    def set_ai_blocked(self, chat_id: str, blocked: bool) -> bool:
        """Block or unblock ATLAX AI without changing signal access."""
        chat_id = str(chat_id)
        record = self._subs.get(chat_id)
        if not record:
            return False
        security = record.setdefault("ai_security", {})
        security["blocked"] = bool(blocked)
        record["updated_at"] = datetime.now(timezone.utc).isoformat()
        self._save()
        return True

    def record_ai_abuse(self, chat_id: str, category: str, strike_limit: int) -> Dict[str, Any]:
        """Record only an abuse category, never the subscriber's question text."""
        chat_id = str(chat_id)
        record = self._subs.get(chat_id)
        if not record:
            return {"blocked": False, "strikes": 0}
        security = record.setdefault("ai_security", {})
        strikes = int(security.get("abuse_strikes", 0)) + 1
        blocked = bool(security.get("blocked", False)) or strikes >= strike_limit
        now_iso = datetime.now(timezone.utc).isoformat()
        security.update({
            "abuse_strikes": strikes,
            "last_abuse_category": str(category)[:40],
            "last_abuse_at": now_iso,
            "blocked": blocked,
        })
        record["updated_at"] = now_iso
        self._save()
        logger.warning(
            '{"event":"subscriber_ai_abuse_recorded","chat_id":"%s","category":"%s","strikes":%d,"blocked":%s}',
            chat_id[:80], str(category)[:40], strikes, blocked,
        )
        return {"blocked": blocked, "strikes": strikes}

    def get_subscribers_for_timeframe(self, timeframe: str, symbol: str = None) -> List[Dict]:
        result = []
        for chat_id, sub in list(self._subs.items()):
            if not sub.get("active"):
                continue
            if self._mark_expired_if_needed(chat_id, sub):
                continue
            if timeframe not in sub.get("timeframes", []):
                continue
            if symbol:
                allowed = sub.get("settings", {}).get("symbols", [])
                if allowed and symbol not in allowed:
                    continue
            result.append(sub)
        return result

    def list_subscribers(self) -> List[Dict]:
        return list(self._subs.values())

    def total_active(self) -> int:
        return sum(1 for chat_id in list(self._subs.keys()) if self.is_registered(chat_id))
