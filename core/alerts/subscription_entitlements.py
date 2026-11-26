"""Subscriber-plan entitlement helpers for downstream alert routing.

This module does not send alerts or make trading decisions. It resolves the
stored subscription plan against the validated ATR quality configuration.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from core.confidence.atr_quality import (
    ATRPlanEntitlement,
    ATRQualityConfig,
    ATRQualityResult,
)
from core.alerts.subscriber_db import SUBSCRIPTION_PLANS


def subscriber_plan(record: Optional[dict[str, Any]]) -> str:
    """Return a normalized plan, or UNKNOWN for an invalid stored value."""
    if not isinstance(record, dict):
        return "unknown"
    plan = str(record.get("subscription_plan", "standard")).strip().lower()
    return plan if plan in SUBSCRIPTION_PLANS else "unknown"


def resolve_atr_entitlement(
    record: Optional[dict[str, Any]],
    config: ATRQualityConfig,
) -> Optional[ATRPlanEntitlement]:
    """Resolve premium ATR access only for currently active subscribers."""
    if not isinstance(record, dict):
        return None
    if not record.get("active") or record.get("subscription_status") != "active":
        return None
    expiry = record.get("subscription_expires_at")
    if expiry:
        try:
            parsed = datetime.fromisoformat(str(expiry).replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            if datetime.now(timezone.utc) >= parsed.astimezone(timezone.utc):
                return None
        except (TypeError, ValueError):
            return None
    return config.get_plan(subscriber_plan(record))


def has_atr_quality_access(
    record: Optional[dict[str, Any]],
    config: ATRQualityConfig,
) -> bool:
    """Return whether the subscriber is entitled to the ATR-filtered route."""
    entitlement = resolve_atr_entitlement(record, config)
    return bool(
        config.enabled
        and entitlement is not None
        and entitlement.atr_quality_filter
    )


def has_premium_confluence_access(
    record: Optional[dict[str, Any]],
    config: Optional[ATRQualityConfig],
) -> bool:
    """Return whether an active subscriber may use Premium Confluence.

    Premium Confluence is a plan entitlement, not merely a persisted Telegram
    preference. Requiring the validated ATR entitlement here also makes a
    missing or invalid quality configuration fail closed.
    """
    if config is None:
        return False
    return has_atr_quality_access(record, config)


_GRADE_RANK = {"C": 0, "B": 1, "A": 2, "A+": 3}


def atr_delivery_decision(
    record: Optional[dict[str, Any]],
    result: Optional[ATRQualityResult],
    config: Optional[ATRQualityConfig],
) -> tuple[bool, str]:
    """Return whether a subscriber may receive an ATR-filtered signal."""
    if config is None or not config.enabled:
        return True, "atr_overlay_disabled"

    plan = subscriber_plan(record)
    if plan == "standard":
        return True, "standard_plan"
    entitlement = resolve_atr_entitlement(record, config)
    if entitlement is None:
        if plan not in SUBSCRIPTION_PLANS:
            return False, "unknown_subscription_plan"
        return False, "inactive_subscription"
    if not entitlement.atr_quality_filter:
        return True, "standard_plan"

    # Shadow mode records the quality and entitlement decision without
    # filtering premium delivery. Subscription activity and expiry checks
    # still apply before this point.
    if config.shadow_mode:
        return True, "atr_shadow_mode_bypass"

    if result is None:
        return False, "atr_quality_missing"
    if result.status != "VALID":
        return False, f"atr_quality_{result.status.lower()}"
    if result.grade is None:
        return False, "atr_quality_missing_grade"

    minimum_grade = entitlement.minimum_grade
    if minimum_grade is None:
        return False, "premium_minimum_grade_missing"
    if _GRADE_RANK.get(result.grade, -1) < _GRADE_RANK.get(minimum_grade, 99):
        return False, f"atr_grade_below_{minimum_grade.replace('+', 'plus')}"
    return True, "premium_grade_accepted"


def has_volume_profile_confluence_access(
    record: Optional[dict[str, Any]],
    config: Optional[ATRQualityConfig],
) -> bool:
    """Return whether an active subscriber may use VP Confluence routing.

    VP Confluence is a premium entitlement that is independent from Key-Level
    Only, ATR quality, High-Quality Only, HTF momentum, and Nested CRT
    suppression (Doc 40 Section 7.3).

    Fail-closed: if the subscriber's plan is unknown, expired, or the
    ATR quality config is missing, access is denied.
    """
    if config is None:
        return False
    return has_atr_quality_access(record, config)
