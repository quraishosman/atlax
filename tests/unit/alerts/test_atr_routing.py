from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import parse_qs

from core.alerts.telegram_channel import TelegramAlertChannel
from core.confidence.atr_quality import ATRPlanEntitlement, ATRQualityConfig, ATRQualityResult
from core.models.alert_event import AlertEvent


def _config() -> ATRQualityConfig:
    return ATRQualityConfig(
        enabled=True,
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
    )


def _quality(status: str, grade: str | None) -> ATRQualityResult:
    valid = status == "VALID"
    return ATRQualityResult(
        status=status,
        grade=grade,
        atr=Decimal("1") if valid else None,
        atr_period=14,
        method="SIMPLE_TR_MEAN",
        parent_range=Decimal("0.8") if valid else None,
        parent_atr_ratio=Decimal("0.8") if valid else None,
        sweep_depth=Decimal("0.2") if valid else None,
        sweep_atr_ratio=Decimal("0.2") if valid else None,
        configuration_snapshot_id="test-snapshot",
        explanation="test quality",
    )


def _alert(quality: ATRQualityResult) -> AlertEvent:
    return AlertEvent(
        alert_id="alert-atr-1",
        candidate_id="candidate-atr-1",
        symbol="EURUSD",
        timeframe="H1",
        profile="day_trader",
        direction="BUY",
        classification="bullish_crt",
        confidence_score=85.0,
        confidence_breakdown={},
        missing_factors=(),
        crt_high=Decimal("1.1100"),
        crt_low=Decimal("1.1000"),
        entry_zone_high=Decimal("1.1080"),
        entry_zone_low=Decimal("1.1050"),
        invalidation_level=Decimal("1.0950"),
        midpoint_target=Decimal("1.1100"),
        opposite_extreme_target=Decimal("1.1150"),
        explanation="test alert",
        created_at=datetime.now(timezone.utc),
        atr_quality=quality,
    )


class _Response:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return b'{"ok": true, "result": {"message_id": 42}}'


def _channel(subscribers):
    config = SimpleNamespace(
        enabled=True,
        bot_token="test-token",
        parse_mode="HTML",
        timeout_seconds=1.0,
    )
    db = SimpleNamespace(
        get_subscribers_for_timeframe=lambda timeframe, symbol=None: subscribers,
    )
    return TelegramAlertChannel(config, subscriber_db=db)


def _subscriber(plan: str) -> dict:
    return {
        "chat_id": plan,
        "active": True,
        "subscription_status": "active",
        "subscription_plan": plan,
        "settings": {"min_confidence": 70},
    }


def test_standard_subscriber_receives_b_grade_while_premium_is_suppressed():
    channel = _channel([_subscriber("standard"), _subscriber("premium")])

    with patch("urllib.request.urlopen", return_value=_Response()) as urlopen:
        delivered = channel.broadcast_to_subscribers(
            _alert(_quality("VALID", "B")),
            confidence_score=85,
            profile_min_confidence=70,
            atr_quality_config=_config(),
        )

    assert delivered == [42]
    assert urlopen.call_count == 1
    payload = parse_qs(urlopen.call_args.args[0].data.decode("utf-8"))
    assert "STANDARD SIGNAL" in payload["text"][0]
    assert "PREMIUM SIGNAL" not in payload["text"][0]


def test_premium_subscriber_receives_only_accepted_atr_grade():
    channel = _channel([_subscriber("premium")])

    with patch("urllib.request.urlopen", return_value=_Response()) as urlopen:
        delivered = channel.broadcast_to_subscribers(
            _alert(_quality("VALID", "A+")),
            confidence_score=85,
            profile_min_confidence=70,
            atr_quality_config=_config(),
        )

    assert delivered == [42]
    assert urlopen.call_count == 1
    payload = parse_qs(urlopen.call_args.args[0].data.decode("utf-8"))
    assert "PREMIUM SIGNAL" in payload["text"][0]
