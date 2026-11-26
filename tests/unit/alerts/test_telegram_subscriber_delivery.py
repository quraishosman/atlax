import json
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from urllib.parse import parse_qs

from core.alerts.telegram_channel import TelegramAlertChannel, _build_message
from core.settings.atr_quality_config import load_atr_quality_config
from core.detectors.crt.quality_tightening import CRTQualityTighteningResult
from core.models.alert_event import AlertEvent
from core.models.volume_profile_result import VolumeProfileStatus
from core.operations.monitoring import OperationalMetricsLedger
from core.settings.alert_config import TelegramConfig


class _Response:
    def __init__(self, body):
        self._body = json.dumps(body).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

    def read(self):
        return self._body


class _Runtime:
    def __init__(self):
        self.values = {}

    def get(self, key, default=None):
        return self.values.get(key, default)

    def set(self, key, value):
        self.values[key] = value


def _quality(*, reasons=(), status="VALID"):
    return CRTQualityTighteningResult(
        enabled=True,
        shadow_mode=True,
        version="1.0",
        status=status,
        direction="bullish",
        reasons=tuple(reasons),
        parent_atr_ratio=Decimal("0.9"),
        parent_body_ratio=Decimal("0.5"),
        sweep_depth_atr_ratio=Decimal("0.3"),
        sweep_retracement_pct=Decimal("0.4"),
    )


def _alert(
    confidence=72.0,
    parent_at_htf_zone=None,
    quality_tightening=None,
    atr_quality=None,
    volume_profiles=(),
    volume_profile_confluence=None,
    volume_profile_matches=(),
):
    return AlertEvent(
        alert_id="alert-1",
        candidate_id="candidate-1",
        symbol="EURUSD",
        timeframe="H1",
        profile="day_trader",
        direction="BUY",
        classification="bullish_crt",
        confidence_score=confidence,
        confidence_breakdown={},
        missing_factors=(),
        crt_high=Decimal("1.11"),
        crt_low=Decimal("1.10"),
        entry_zone_high=Decimal("1.105"),
        entry_zone_low=Decimal("1.104"),
        invalidation_level=Decimal("1.100"),
        midpoint_target=Decimal("1.108"),
        opposite_extreme_target=Decimal("1.112"),
        explanation="test alert",
        created_at=datetime.now(timezone.utc),
        parent_at_htf_zone=parent_at_htf_zone,
        atr_quality=atr_quality,
        quality_tightening=quality_tightening,
        volume_profiles=tuple(volume_profiles),
        volume_profile_confluence=volume_profile_confluence,
        volume_profile_matches=tuple(volume_profile_matches),
    )


def test_subscriber_broadcast_accepts_confidence_and_applies_final_filters(monkeypatch):
    subscribers = [
        {"chat_id": "123", "active": True, "settings": {"symbols": [], "min_confidence": 60}},
        {"chat_id": "456", "active": True, "settings": {"symbols": [], "min_confidence": 80}},
        {"chat_id": "789", "active": True, "settings": {"symbols": [], "min_confidence": None}},
    ]
    calls = []
    messages = {}

    def get_subscribers_for_timeframe(timeframe, symbol=None):
        assert timeframe == "H1"
        assert symbol == "EURUSD"
        return subscribers

    def fake_urlopen(request, timeout):
        payload = parse_qs(request.data.decode("utf-8"))
        chat_id = payload["chat_id"][0]
        calls.append(chat_id)
        messages[chat_id] = payload["text"][0]
        return _Response({"ok": True, "result": {"message_id": len(calls)}})

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    config = TelegramConfig(
        enabled=True,
        bot_token="test-token",
        chat_id="999",
        parse_mode="HTML",
        timeout_seconds=1,
        retry_attempts=1,
    )
    channel = TelegramAlertChannel(
        config,
        subscriber_db=SimpleNamespace(get_subscribers_for_timeframe=get_subscribers_for_timeframe),
    )

    delivered = channel.broadcast_to_subscribers(
        _alert(),
        confidence_score=72.0,
        profile_min_confidence=44.0,
    )

    assert delivered == [1, 2]
    assert calls == ["123", "789"]
    assert "STANDARD SIGNAL" in messages["123"]
    assert "STANDARD SIGNAL" in messages["789"]


def test_key_level_only_suppresses_unknown_and_non_key_level_alerts(monkeypatch):
    subscribers = [
        {
            "chat_id": "123",
            "active": True,
            "subscription_plan": "premium",
            "settings": {
                "symbols": [],
                "min_confidence": 60,
                "key_level_only": True,
            },
        }
    ]
    calls = []

    def fake_urlopen(request, timeout):
        calls.append(request)
        return _Response({"ok": True, "result": {"message_id": 1}})

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    config = TelegramConfig(
        enabled=True,
        bot_token="test-token",
        chat_id="999",
        parse_mode="HTML",
        timeout_seconds=1,
        retry_attempts=1,
    )
    channel = TelegramAlertChannel(
        config,
        subscriber_db=SimpleNamespace(
            get_subscribers_for_timeframe=lambda timeframe, symbol=None: subscribers,
        ),
    )

    assert channel.broadcast_to_subscribers(
        _alert(parent_at_htf_zone=False),
        confidence_score=72.0,
        profile_min_confidence=60.0,
    ) == []
    assert channel.broadcast_to_subscribers(
        _alert(parent_at_htf_zone=None),
        confidence_score=72.0,
        profile_min_confidence=60.0,
    ) == []
    assert calls == []


def test_key_level_only_delivers_key_level_alert_and_labels_context(monkeypatch):
    subscribers = [
        {
            "chat_id": "123",
            "active": True,
            "subscription_plan": "premium",
            "settings": {
                "symbols": [],
                "min_confidence": 60,
                "key_level_only": True,
            },
        }
    ]
    messages = []

    def fake_urlopen(request, timeout):
        messages.append(parse_qs(request.data.decode("utf-8"))["text"][0])
        return _Response({"ok": True, "result": {"message_id": 1}})

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    config = TelegramConfig(
        enabled=True,
        bot_token="test-token",
        chat_id="999",
        parse_mode="HTML",
        timeout_seconds=1,
        retry_attempts=1,
    )
    channel = TelegramAlertChannel(
        config,
        subscriber_db=SimpleNamespace(
            get_subscribers_for_timeframe=lambda timeframe, symbol=None: subscribers,
        ),
    )

    delivered = channel.broadcast_to_subscribers(
        _alert(parent_at_htf_zone=True),
        confidence_score=72.0,
        profile_min_confidence=60.0,
    )

    assert delivered == [1]
    assert "PREMIUM SIGNAL" in messages[0]
    assert "HTF KEY-LEVEL" in messages[0]


def test_end_to_end_standard_and_key_level_only_routing(monkeypatch):
    subscribers = [
        {
            "chat_id": "standard",
            "active": True,
            "settings": {"symbols": [], "min_confidence": 60},
        },
        {
            "chat_id": "key-only",
            "active": True,
            "subscription_plan": "premium",
            "settings": {
                "symbols": [],
                "min_confidence": 60,
                "key_level_only": True,
            },
        },
    ]
    calls = []

    def fake_urlopen(request, timeout):
        payload = parse_qs(request.data.decode("utf-8"))
        calls.append(
            {
                "chat_id": payload["chat_id"][0],
                "text": payload["text"][0],
            }
        )
        return _Response({"ok": True, "result": {"message_id": len(calls)}})

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    config = TelegramConfig(
        enabled=True,
        bot_token="test-token",
        chat_id="999",
        parse_mode="HTML",
        timeout_seconds=1,
        retry_attempts=1,
    )
    monitoring = OperationalMetricsLedger(_Runtime())
    channel = TelegramAlertChannel(
        config,
        subscriber_db=SimpleNamespace(
            get_subscribers_for_timeframe=lambda timeframe, symbol=None: subscribers,
        ),
        monitoring=monitoring,
    )

    # Standard users retain both valid key-level and open-structure signals.
    assert channel.broadcast_to_subscribers(
        _alert(parent_at_htf_zone=True),
        confidence_score=72.0,
        profile_min_confidence=60.0,
    ) == [1, 2]
    assert channel.broadcast_to_subscribers(
        _alert(parent_at_htf_zone=False),
        confidence_score=72.0,
        profile_min_confidence=60.0,
    ) == [3]

    # UNKNOWN remains available to standard users but is fail-closed for the
    # stricter Key-Level Only route.
    assert channel.broadcast_to_subscribers(
        _alert(parent_at_htf_zone=None),
        confidence_score=72.0,
        profile_min_confidence=60.0,
    ) == [4]

    assert [call["chat_id"] for call in calls] == [
        "standard",
        "key-only",
        "standard",
        "standard",
    ]
    assert "HTF KEY-LEVEL" in calls[1]["text"]
    report = monitoring.report()
    assert report["subscriber_delivery"]["delivered"] == 4
    assert report["subscriber_delivery"]["suppressed"] == 2
    assert report["subscriber_delivery"]["suppressed_by_reason"] == {"key_level_only": 2}


def test_high_quality_only_route_suppresses_counterfactual_weak_alert(monkeypatch):
    subscribers = [
        {"chat_id": "standard", "active": True, "settings": {"symbols": [], "min_confidence": 60}},
        {
            "chat_id": "quality-only",
            "active": True,
            "settings": {"symbols": [], "min_confidence": 60, "high_quality_only": True},
        },
    ]
    calls = []

    def fake_urlopen(request, timeout):
        calls.append(parse_qs(request.data.decode("utf-8"))["chat_id"][0])
        return _Response({"ok": True, "result": {"message_id": len(calls)}})

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    config = TelegramConfig(
        enabled=True,
        bot_token="test-token",
        chat_id="999",
        parse_mode="HTML",
        timeout_seconds=1,
        retry_attempts=1,
    )
    monitoring = OperationalMetricsLedger(_Runtime())
    channel = TelegramAlertChannel(
        config,
        subscriber_db=SimpleNamespace(
            get_subscribers_for_timeframe=lambda timeframe, symbol=None: subscribers,
        ),
        monitoring=monitoring,
    )

    delivered = channel.broadcast_to_subscribers(
        _alert(quality_tightening=_quality(reasons=("parent_range_below_threshold",))),
        confidence_score=72.0,
        profile_min_confidence=60.0,
    )

    assert delivered == [1]
    assert calls == ["standard"]
    assert monitoring.report()["subscriber_delivery"]["suppressed_by_reason"] == {
        "high_quality_only": 1,
    }


def test_high_quality_only_route_delivers_valid_quality_alert(monkeypatch):
    subscribers = [{
        "chat_id": "quality-only",
        "active": True,
        "settings": {"symbols": [], "min_confidence": 60, "high_quality_only": True},
    }]
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda request, timeout: _Response({"ok": True, "result": {"message_id": 1}}),
    )
    config = TelegramConfig(
        enabled=True,
        bot_token="test-token",
        chat_id="999",
        parse_mode="HTML",
        timeout_seconds=1,
        retry_attempts=1,
    )
    channel = TelegramAlertChannel(
        config,
        subscriber_db=SimpleNamespace(
            get_subscribers_for_timeframe=lambda timeframe, symbol=None: subscribers,
        ),
    )

    assert channel.broadcast_to_subscribers(
        _alert(quality_tightening=_quality()),
        confidence_score=72.0,
        profile_min_confidence=60.0,
    ) == [1]


def test_premium_confluence_requires_key_level_and_high_quality(monkeypatch):
    subscribers = [{
        "chat_id": "confluence",
        "subscription_plan": "premium",
        "active": True,
        "subscription_status": "active",
        "subscription_expires_at": "2030-01-01T00:00:00+00:00",
        "settings": {"symbols": [], "min_confidence": 60, "confluence_only": True},
    }]
    calls = []

    def fake_urlopen(request, timeout):
        calls.append(request)
        return _Response({"ok": True, "result": {"message_id": len(calls)}})

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    config = TelegramConfig(
        enabled=True,
        bot_token="test-token",
        chat_id="999",
        parse_mode="HTML",
        timeout_seconds=1,
        retry_attempts=1,
    )
    monitoring = OperationalMetricsLedger(_Runtime())
    channel = TelegramAlertChannel(
        config,
        subscriber_db=SimpleNamespace(
            get_subscribers_for_timeframe=lambda timeframe, symbol=None: subscribers,
        ),
        monitoring=monitoring,
    )
    atr_quality_config = load_atr_quality_config("config/atlax.yaml")
    atr_result = SimpleNamespace(status="VALID", grade="A")

    # Good quality but no major key level: blocked by the combined gate.
    assert channel.broadcast_to_subscribers(
            _alert(
                parent_at_htf_zone=False,
                quality_tightening=_quality(),
                atr_quality=atr_result,
            ),
        confidence_score=72.0,
        profile_min_confidence=60.0,
        atr_quality_config=atr_quality_config,
    ) == []
    # Key level present but weak quality: still blocked by the combined gate.
    assert channel.broadcast_to_subscribers(
                _alert(
                    parent_at_htf_zone=True,
                    quality_tightening=_quality(reasons=("sweep_depth_below_threshold",)),
                    atr_quality=atr_result,
                ),
            confidence_score=72.0,
            profile_min_confidence=60.0,
            atr_quality_config=atr_quality_config,
    ) == []
    # Only the setup with both features is delivered.
    assert channel.broadcast_to_subscribers(
            _alert(
                parent_at_htf_zone=True,
                quality_tightening=_quality(),
                atr_quality=atr_result,
            ),
        confidence_score=72.0,
        profile_min_confidence=60.0,
        atr_quality_config=atr_quality_config,
    ) == [1]
    assert len(calls) == 1
    assert monitoring.report()["subscriber_delivery"]["suppressed_by_reason"] == {
        "confluence_key_level": 1,
        "confluence_quality": 1,
    }


def test_volume_profile_only_requires_a_matched_valid_level(monkeypatch):
    subscribers = [{
        "chat_id": "vp-only",
        "subscription_plan": "premium",
        "active": True,
        "subscription_status": "active",
        "subscription_expires_at": "2030-01-01T00:00:00+00:00",
        "settings": {
            "symbols": [],
            "min_confidence": 60,
            "volume_profile_confluence_only": True,
        },
    }]
    calls = []

    def fake_urlopen(request, timeout):
        calls.append(request)
        return _Response({"ok": True, "result": {"message_id": len(calls)}})

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    channel = TelegramAlertChannel(
        TelegramConfig(
            enabled=True,
            bot_token="test-token",
            chat_id="999",
            parse_mode="HTML",
            timeout_seconds=1,
            retry_attempts=1,
        ),
        subscriber_db=SimpleNamespace(
            get_subscribers_for_timeframe=lambda timeframe, symbol=None: subscribers,
        ),
    )
    atr_quality_config = load_atr_quality_config("config/atlax.yaml")
    valid_profile = SimpleNamespace(status=VolumeProfileStatus.VALID)

    # A valid profile without a parent-level match is not Premium confluence.
    assert channel.broadcast_to_subscribers(
        _alert(
            volume_profiles=(valid_profile,),
            volume_profile_confluence=True,
            volume_profile_matches=(),
        ),
        confidence_score=72.0,
        profile_min_confidence=60.0,
        atr_quality_config=atr_quality_config,
    ) == []

    # A matched level is required before the Premium-only route delivers.
    assert channel.broadcast_to_subscribers(
        _alert(
            atr_quality=SimpleNamespace(status="VALID", grade="A"),
            volume_profiles=(valid_profile,),
            volume_profile_confluence=True,
            volume_profile_matches=({"level_type": "PREVIOUS_DAY_POC", "price": "1.1000"},),
        ),
        confidence_score=72.0,
        profile_min_confidence=60.0,
        atr_quality_config=atr_quality_config,
    ) == [1]
    assert len(calls) == 1


def test_volume_profile_match_is_explicit_in_alert_text():
    message = _build_message(
        _alert(
            volume_profile_matches=(
                {"level_type": "PREVIOUS_DAY_POC", "price": "1.1000"},
            ),
        )
    )

    assert "📊 VP MATCH" in message
    assert "Previous Day Poc" in message


def test_standard_confluence_preference_is_suppressed_fail_closed(monkeypatch):
    subscribers = [{
        "chat_id": "standard-confluence",
        "subscription_plan": "standard",
        "active": True,
        "subscription_status": "active",
        "subscription_expires_at": "2030-01-01T00:00:00+00:00",
        "settings": {"symbols": [], "min_confidence": 60, "confluence_only": True},
    }]
    calls = []

    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda request, timeout: calls.append(request),
    )
    config = TelegramConfig(
        enabled=True,
        bot_token="test-token",
        chat_id="999",
        parse_mode="HTML",
        timeout_seconds=1,
        retry_attempts=1,
    )
    channel = TelegramAlertChannel(
        config,
        subscriber_db=SimpleNamespace(
            get_subscribers_for_timeframe=lambda timeframe, symbol=None: subscribers,
        ),
    )

    assert channel.broadcast_to_subscribers(
        _alert(parent_at_htf_zone=True, quality_tightening=_quality()),
        confidence_score=72.0,
        profile_min_confidence=60.0,
        atr_quality_config=load_atr_quality_config("config/atlax.yaml"),
    ) == []
    assert calls == []
