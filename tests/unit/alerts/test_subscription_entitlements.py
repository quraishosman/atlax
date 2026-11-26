from datetime import datetime, timezone
from decimal import Decimal

from core.alerts.subscription_entitlements import (
    atr_delivery_decision,
    has_atr_quality_access,
    resolve_atr_entitlement,
    subscriber_plan,
)
from core.confidence.atr_quality import (
    ATRPlanEntitlement,
    ATRQualityConfig,
    ATRQualityResult,
)


def _config(enabled: bool = True, shadow_mode: bool = False) -> ATRQualityConfig:
    return ATRQualityConfig(
        enabled=enabled,
        version="0.1.0",
        method="SIMPLE_TR_MEAN",
        atr_period=14,
        parent_high_min=Decimal("0.80"),
        parent_a_min=Decimal("0.60"),
        parent_b_min=Decimal("0.45"),
        sweep_high_min=Decimal("0.25"),
        sweep_a_min=Decimal("0.20"),
        sweep_b_min=Decimal("0.15"),
        minimum_premium_grade="A",
        missing_data_policy="UNKNOWN",
        advanced_regime_filter_enabled=False,
        max_parent_atr_ratio=None,
        max_sweep_atr_ratio=None,
        volatility_regime_min_ratio=None,
        volatility_regime_max_ratio=None,
        default_plan="standard",
        plans=(
            ATRPlanEntitlement("standard", False, None),
            ATRPlanEntitlement("premium", True, "A"),
        ),
        shadow_mode=shadow_mode,
    )


def _subscriber(plan: str = "standard", active: bool = True) -> dict:
    return {
        "chat_id": "123",
        "active": active,
        "subscription_status": "active" if active else "expired",
        "subscription_plan": plan,
        "subscription_expires_at": datetime(
            2030, 1, 1, tzinfo=timezone.utc
        ).isoformat(),
    }


def test_standard_plan_does_not_receive_atr_entitlement():
    config = _config()
    record = _subscriber("standard")

    assert subscriber_plan(record) == "standard"
    assert resolve_atr_entitlement(record, config).plan_name == "standard"
    assert has_atr_quality_access(record, config) is False


def test_premium_plan_receives_atr_entitlement():
    config = _config()
    record = _subscriber("premium")

    entitlement = resolve_atr_entitlement(record, config)

    assert entitlement is not None
    assert entitlement.plan_name == "premium"
    assert entitlement.minimum_grade == "A"
    assert has_atr_quality_access(record, config) is True


def test_expired_subscriber_cannot_use_premium_entitlement():
    config = _config()

    assert resolve_atr_entitlement(_subscriber("premium", active=False), config) is None
    assert has_atr_quality_access(_subscriber("premium", active=False), config) is False


def test_expired_timestamp_cannot_use_premium_delivery():
    record = _subscriber("premium")
    record["subscription_expires_at"] = datetime(
        2020, 1, 1, tzinfo=timezone.utc
    ).isoformat()

    allowed, reason = atr_delivery_decision(record, None, _config())

    assert (allowed, reason) == (False, "inactive_subscription")


def test_unknown_plan_fails_closed():
    config = _config()
    record = _subscriber("founder")

    assert subscriber_plan(record) == "unknown"
    assert resolve_atr_entitlement(record, config) is None
    assert has_atr_quality_access(record, config) is False


def test_legacy_record_defaults_to_standard():
    config = _config()
    record = _subscriber()
    record.pop("subscription_plan")

    assert subscriber_plan(record) == "standard"
    assert has_atr_quality_access(record, config) is False


def test_disabled_overlay_blocks_even_premium_plan():
    record = _subscriber("premium")

    assert has_atr_quality_access(record, _config(enabled=False)) is False


def _result(status: str = "VALID", grade: str | None = "A") -> ATRQualityResult:
    return ATRQualityResult(
        status=status,
        grade=grade,
        atr=Decimal("1"),
        atr_period=14,
        method="SIMPLE_TR_MEAN",
        parent_range=Decimal("0.8") if status == "VALID" else None,
        parent_atr_ratio=Decimal("0.8") if status == "VALID" else None,
        sweep_depth=Decimal("0.2") if status == "VALID" else None,
        sweep_atr_ratio=Decimal("0.2") if status == "VALID" else None,
        configuration_snapshot_id="test-snapshot",
        explanation="test",
    )


def test_standard_delivery_is_unchanged_when_overlay_is_enabled():
    allowed, reason = atr_delivery_decision(
        _subscriber("standard"), _result("VALID", "C"), _config()
    )

    assert allowed is True
    assert reason == "standard_plan"


def test_premium_delivery_accepts_a_plus_and_a_only():
    config = _config()

    assert atr_delivery_decision(_subscriber("premium"), _result("VALID", "A+"), config) == (
        True,
        "premium_grade_accepted",
    )
    assert atr_delivery_decision(_subscriber("premium"), _result("VALID", "A"), config) == (
        True,
        "premium_grade_accepted",
    )


def test_premium_delivery_rejects_lower_or_unknown_quality():
    config = _config()

    allowed_b, reason_b = atr_delivery_decision(_subscriber("premium"), _result("VALID", "B"), config)
    allowed_unknown, reason_unknown = atr_delivery_decision(_subscriber("premium"), _result("UNKNOWN", None), config)

    assert (allowed_b, reason_b) == (False, "atr_grade_below_A")
    assert (allowed_unknown, reason_unknown) == (False, "atr_quality_unknown")


def test_shadow_mode_logs_but_does_not_enforce_grade_filter():
    allowed, reason = atr_delivery_decision(
        _subscriber("premium"), _result("VALID", "C"), _config(shadow_mode=True)
    )

    assert (allowed, reason) == (True, "atr_shadow_mode_bypass")
