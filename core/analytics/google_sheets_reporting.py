"""Unified Google Sheets reporting events.

Google Sheets is an analysis mirror only. This module never participates in
signal detection, delivery decisions, payments, or execution decisions. A
reporting failure is logged and returned to the caller as ``False``.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import uuid
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any, Mapping

logger = logging.getLogger("atlax.analytics.google_sheets_reporting")

WEBHOOK_ENV = "ATLAX_GOOGLE_SHEETS_WEBHOOK_URL"
SHARED_SECRET_ENV = "ATLAX_GOOGLE_SHEETS_SHARED_SECRET"
SCHEMA_VERSION = "3.0"


def _json_value(value: Any) -> Any:
    """Convert common ATLAX values into JSON-safe values without guessing."""
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_value(item) for item in value]
    return value


def _signature_material(body: Mapping[str, Any]) -> str:
    return "|".join(
        str(body.get(field, ""))
        for field in ("event_type", "event_id", "correlation_id", "occurred_at")
    )


def _post_json(url: str, payload: Mapping[str, Any]) -> bool:
    try:
        import requests
    except ImportError as exc:
        import json
        import urllib.error
        import urllib.request

        try:
            body = json.dumps(dict(payload), ensure_ascii=True).encode("utf-8")
            request = urllib.request.Request(
                url,
                data=body,
                method="POST",
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(request, timeout=10) as response:
                status_code = response.getcode()
                response_text = response.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as error:
            status_code = error.code
            response_text = error.read().decode("utf-8", errors="replace")
        except Exception as error:  # noqa: BLE001
            logger.warning(
                '{"event":"google_sheets_request_failed","error":"%s"}',
                str(error)[:160],
            )
            return False

        if status_code != 200:
            logger.warning(
                '{"event":"google_sheets_response_error","status_code":%d}',
                status_code,
            )
            return False
        try:
            response_body = json.loads(response_text)
        except (TypeError, ValueError):
            response_body = None
        if isinstance(response_body, Mapping) and response_body.get("ok") is False:
            logger.warning(
                '{"event":"google_sheets_event_rejected","reason":"%s"}',
                str(response_body.get("error", "remote_rejection"))[:160],
            )
            return False
        return True

    try:
        response = requests.post(url, json=dict(payload), timeout=10)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            '{"event":"google_sheets_request_failed","error":"%s"}',
            str(exc)[:160],
        )
        return False

    if response.status_code != 200:
        logger.warning(
            '{"event":"google_sheets_response_error","status_code":%d}',
            response.status_code,
        )
        return False
    try:
        response_body = response.json()
    except Exception:  # noqa: BLE001
        response_body = None
    if isinstance(response_body, Mapping) and response_body.get("ok") is False:
        logger.warning(
            '{"event":"google_sheets_event_rejected","reason":"%s"}',
            str(response_body.get("error", "remote_rejection"))[:160],
        )
        return False
    return True


def report_event(
    event_type: str,
    payload: Mapping[str, Any] | None = None,
    *,
    webhook_url: str | None = None,
    event_id: str = "",
    correlation_id: str = "",
) -> bool:
    """Send one typed, idempotent reporting event to the Apps Script router."""
    url = (webhook_url or os.environ.get(WEBHOOK_ENV, "")).strip()
    if not url:
        logger.debug("Google Sheets webhook URL not configured; skipping event.")
        return False

    normalized_type = str(event_type or "").strip().lower()
    if not normalized_type:
        logger.warning(
            '{"event":"google_sheets_event_rejected","reason":"missing_event_type"}'
        )
        return False

    body: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "event_type": normalized_type,
        "event_id": str(event_id or f"{normalized_type}:{uuid.uuid4().hex}"),
        "correlation_id": str(correlation_id or ""),
        "occurred_at": datetime.now().astimezone().isoformat(),
    }
    body.update(_json_value(dict(payload or {})))

    # Only a derived HMAC is sent. The shared secret remains local to ATLAX
    # and Google Apps Script properties and is never sent or written to a tab.
    shared_secret = os.environ.get(SHARED_SECRET_ENV, "").strip()
    if shared_secret:
        body["signature"] = hmac.new(
            shared_secret.encode("utf-8"),
            _signature_material(body).encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    ok = _post_json(url, body)
    if ok:
        logger.info(
            '{"event":"google_sheets_event_sent","event_type":"%s",'
            '"event_id":"%s"}',
            normalized_type,
            body["event_id"],
        )
    return ok


def _value(obj: Any, name: str, default: Any = "") -> Any:
    value = getattr(obj, name, default)
    return default if value is None else value


def signal_snapshot(alert: Any) -> dict[str, Any]:
    """Build shared signal fields used by formed and delivery events."""
    quality = _value(alert, "atr_quality", None)
    key_level = _value(alert, "key_level_evaluation", None) or {}
    return {
        "symbol": _value(alert, "symbol"),
        "timeframe": _value(alert, "timeframe"),
        "profile": _value(alert, "profile"),
        "direction": _value(alert, "direction"),
        "classification": _value(alert, "classification", "UNKNOWN"),
        "confidence_score": _value(alert, "confidence_score"),
        "atr_status": _value(quality, "status", "UNKNOWN") if quality else "UNKNOWN",
        "atr_grade": _value(quality, "grade", "") if quality else "",
        "parent_atr_ratio": _value(quality, "parent_atr_ratio", "") if quality else "",
        "sweep_atr_ratio": _value(quality, "sweep_atr_ratio", "") if quality else "",
        "key_level_status": key_level.get("status", "") if isinstance(key_level, dict) else "",
        "key_level_zone": key_level.get("parent_at_htf_zone", "") if isinstance(key_level, dict) else "",
        "key_level_match_count": key_level.get("matched_level_count", "") if isinstance(key_level, dict) else "",
        "entry": _value(alert, "entry_zone_high"),
        "entry_low": _value(alert, "entry_zone_low"),
        "sl": _value(alert, "invalidation_level"),
        "tp1": _value(alert, "midpoint_target"),
        "tp2": _value(alert, "opposite_extreme_target"),
        "signal_status": "OPEN",
        "outcome": "UNKNOWN",
        "delivery_route": "",
        "delivery_count": 0,
    }


def report_signal_formed(alert: Any, *, webhook_url: str = "") -> bool:
    correlation_id = str(_value(alert, "alert_id", ""))
    payload = signal_snapshot(alert)
    payload["created_at"] = _value(alert, "created_at", "")
    return report_event(
        "signal_formed",
        payload,
        webhook_url=webhook_url,
        event_id=f"signal_formed:{correlation_id}",
        correlation_id=correlation_id,
    )


def report_signal_delivery(
    alert: Any,
    route: str,
    delivery_count: int,
    *,
    webhook_url: str = "",
) -> bool:
    correlation_id = str(_value(alert, "alert_id", ""))
    payload = signal_snapshot(alert)
    payload.update({
        "created_at": _value(alert, "created_at", ""),
        "delivery_route": str(route or "unknown").strip().lower(),
        "delivery_count": int(delivery_count),
        "delivered_at": datetime.now().astimezone().isoformat(),
    })
    return report_event(
        "signal_delivered",
        payload,
        webhook_url=webhook_url,
        event_id=f"signal_delivered:{correlation_id}:{payload['delivery_route']}",
        correlation_id=correlation_id,
    )


def report_payment_event(
    intent: Mapping[str, Any],
    event_type: str,
    *,
    webhook_url: str = "",
) -> bool:
    reference = str(intent.get("reference", "")).strip().upper()
    payload = {
        "reference": reference,
        "chat_id": str(intent.get("chat_id", "")),
        "username": str(intent.get("username", "")),
        "subscription_plan": str(intent.get("subscription_plan", "standard")),
        "method": str(intent.get("method", "")),
        "amount_usd": intent.get("amount_usd", ""),
        "status": str(intent.get("status", "")),
        "submitted_tx_reference": str(intent.get("submitted_tx_reference", "")),
        "created_at": intent.get("created_at", ""),
        "submitted_at": intent.get("submitted_at", ""),
        "confirmed_at": intent.get("confirmed_at", ""),
        "rejected_at": intent.get("rejected_at", ""),
        "expires_at": intent.get("expires_at", ""),
        "confirmed_by": str(intent.get("confirmed_by", "")),
        "rejected_by": str(intent.get("rejected_by", "")),
        "rejection_reason": str(intent.get("rejection_reason", "")),
    }
    return report_event(
        event_type,
        payload,
        webhook_url=webhook_url,
        event_id=f"payment:{reference}:{event_type}",
        correlation_id=reference,
    )


def report_subscriber_event(
    subscriber: Mapping[str, Any],
    event_type: str,
    *,
    webhook_url: str = "",
) -> bool:
    chat_id = str(subscriber.get("chat_id", ""))
    started_at = str(subscriber.get("subscription_started_at", ""))
    payload = {
        "chat_id": chat_id,
        "username": str(subscriber.get("username", "")),
        "subscription_plan": str(subscriber.get("subscription_plan", "standard")),
        "subscription_status": str(subscriber.get("subscription_status", "")),
        "style": str(subscriber.get("style", "")),
        "timeframes": subscriber.get("timeframes", []),
        "payment_method": str(subscriber.get("payment_method", "")),
        "payment_reference": str(subscriber.get("payment_reference", "")),
        "subscription_source": str(subscriber.get("subscription_source", "")),
        "joined": subscriber.get("joined", ""),
        "subscription_started_at": started_at,
        "subscription_expires_at": subscriber.get("subscription_expires_at", ""),
        "updated_at": subscriber.get("updated_at", ""),
    }
    event_key = started_at or str(subscriber.get("joined", "")) or chat_id
    return report_event(
        event_type,
        payload,
        webhook_url=webhook_url,
        event_id=f"subscriber:{chat_id}:{event_type}:{event_key}",
        correlation_id=chat_id,
    )
