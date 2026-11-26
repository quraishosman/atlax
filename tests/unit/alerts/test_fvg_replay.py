"""Captured-value replay for the FVG two-alert path.

The values below come from the pre-fix XAUUSD M5 runtime log.  This test
does not invent a new trading rule; it verifies that a captured, already
detected FVG can travel through the alert, Passport, watch, and entry-alert
boundaries without the old missing-method failure.
"""

import dataclasses
import json
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

from core.alerts.alert_engine import AlertEngine
from core.alerts.telegram_channel import TelegramAlertChannel
from core.detectors.fvg.detector import FVGResult
from core.detectors.fvg.watcher import FVGWatcher
from core.detectors.crt.quality_tightening import CRTQualityTighteningResult
from core.analytics.signal_passport import get_signal_passport_history


class _Response:
    def __init__(self, message_id):
        self.payload = json.dumps({"ok": True, "result": {"message_id": message_id}}).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return self.payload


class _Subscribers:
    def get_subscribers_for_timeframe(self, timeframe, symbol=None):
        assert timeframe == "H1"
        assert symbol == "XAUUSD"
        return [{
            "chat_id": "8803483793",
            "username": "ocroxyz",
            "subscription_plan": "premium",
            "active": True,
            "settings": {"min_confidence": 40},
        }]


class _MemoryState:
    def __init__(self):
        self.values = {}

    def get(self, key, default=None):
        return self.values.get(key, default)

    def set(self, key, value):
        self.values[key] = value


def test_captured_xauusd_fvg_replay_reaches_passport_watch_and_entry(
    monkeypatch,
    alert_config,
    sample_candidate,
    sample_confidence,
    tmp_path,
):
    """Replay the captured XAUUSD event through both FVG Telegram alerts."""
    candidate = dataclasses.replace(
        sample_candidate,
        candidate_id="04fd7808-d7a1-42fa-957f-3fa554ac7444",
        symbol="XAUUSD",
        timeframe="H1",
        profile="day_trader",
        created_at=datetime(2026, 8, 13, 19, 25, tzinfo=timezone.utc),
    )
    confidence = dataclasses.replace(
        sample_confidence,
        candidate_id=candidate.candidate_id,
        symbol=candidate.symbol,
        timeframe=candidate.timeframe,
        profile=candidate.profile,
        final_score=49.02,
        raw_score=49.02,
    )
    fvg = FVGResult(
        found=True,
        direction="bullish",
        fvg_high=Decimal("4362.440"),
        fvg_low=Decimal("4358.320"),
        formed_at=datetime(2026, 8, 13, 19, 25, tzinfo=timezone.utc),
        ltf="M5",
    )

    engine = AlertEngine(
        alert_config,
        channels=[],
        db_path=str(tmp_path / "journal.sqlite"),
        runtime_store=_MemoryState(),
    )
    alert = engine.register_fvg_prediction(candidate, confidence)

    assert alert.candidate_id == candidate.candidate_id
    assert alert.alert_id in engine.tracker.predictions
    passport = get_signal_passport_history(
        str(tmp_path / "journal.sqlite"),
        days=30,
        limit=10,
    )
    assert any(
        record["signal_id"] == alert.alert_id and record["pair"] == "XAUUSD"
        for record in passport
    )

    telegram_config = SimpleNamespace(
        enabled=True,
        bot_token="test-token",
        chat_id="123",
        parse_mode="HTML",
        timeout_seconds=2,
        retry_attempts=1,
        mini_app_url="",
    )
    channel = TelegramAlertChannel(
        telegram_config,
        subscriber_db=_Subscribers(),
    )
    requests = []

    def fake_urlopen(request, timeout):
        requests.append(request)
        return _Response(len(requests))

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    delivered_watch = channel.broadcast_fvg_watch(
        symbol="XAUUSD",
        htf_timeframe="H1",
        ltf_timeframe="M5",
        direction="bullish",
        fvg_high=str(fvg.fvg_high),
        fvg_low=str(fvg.fvg_low),
        fvg_midpoint=str(fvg.midpoint),
        invalidation_level="4350.000",
        midpoint_target="4370.000",
        opposite_extreme_target="4380.000",
        confidence_score=confidence.final_score,
        profile="day_trader",
        candidate_id=candidate.candidate_id,
        parent_at_htf_zone=True,
        profile_min_confidence=45,
    )

    assert delivered_watch == [2]
    assert len(requests) == 2
    assert b"FVG+ENTRY+WATCH" in requests[0].data
    assert b"FVG+ENTRY+WATCH" in requests[1].data

    watcher = FVGWatcher(max_age_seconds=300, state_store=_MemoryState())
    watcher.add_watch_full(
        symbol="XAUUSD",
        fvg=fvg,
        candidate_id=candidate.candidate_id,
        confidence_score=confidence.final_score,
        profile="day_trader",
        entry_zone_high=Decimal("4365.000"),
        entry_zone_low=Decimal("4355.000"),
        invalidation_level=Decimal("4350.000"),
        midpoint_target=Decimal("4370.000"),
        opposite_extreme_target=Decimal("4380.000"),
        htf_timeframe="H1",
        parent_at_htf_zone=True,
        profile_min_confidence=45,
    )
    triggered = watcher.check_live_price(
        "XAUUSD",
        live_bid=4360.000,
        live_ask=4360.500,
    )
    assert [watch.candidate_id for watch in triggered] == [candidate.candidate_id]

    delivered_entry = channel.broadcast_fvg_entry(
        symbol="XAUUSD",
        htf_timeframe="H1",
        ltf_timeframe="M5",
        direction="bullish",
        fvg_high=str(fvg.fvg_high),
        fvg_low=str(fvg.fvg_low),
        invalidation_level="4350.000",
        midpoint_target="4370.000",
        opposite_extreme_target="4380.000",
        confidence_score=confidence.final_score,
        profile="day_trader",
        candidate_id=candidate.candidate_id,
        parent_at_htf_zone=True,
        profile_min_confidence=45,
    )

    assert delivered_entry == [4]
    assert len(requests) == 4
    assert b"FVG+ENTRY+TRIGGERED" in requests[2].data
    assert b"FVG+ENTRY+TRIGGERED" in requests[3].data


def test_fvg_watch_respects_high_quality_only_preference(monkeypatch):
    class QualitySubscribers:
        def get_subscribers_for_timeframe(self, timeframe, symbol=None):
            return [{
                "chat_id": "quality-only",
                "active": True,
                "settings": {"min_confidence": 40, "high_quality_only": True},
            }]

    quality = CRTQualityTighteningResult(
        enabled=True,
        shadow_mode=True,
        version="1.0",
        status="VALID",
        direction="bullish",
        reasons=(),
        parent_atr_ratio=Decimal("0.9"),
        parent_body_ratio=Decimal("0.5"),
        sweep_depth_atr_ratio=Decimal("0.3"),
        sweep_retracement_pct=Decimal("0.4"),
    )
    weak_quality = dataclasses.replace(
        quality,
        reasons=("parent_range_below_threshold",),
    )
    config = SimpleNamespace(
        enabled=True,
        bot_token="test-token",
        chat_id="123",
        parse_mode="HTML",
        timeout_seconds=2,
        retry_attempts=1,
        mini_app_url="",
    )
    channel = TelegramAlertChannel(config, subscriber_db=QualitySubscribers())
    requests = []

    def fake_urlopen(request, timeout):
        requests.append(request)
        return _Response(len(requests))

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    common = {
        "symbol": "XAUUSD",
        "htf_timeframe": "H1",
        "ltf_timeframe": "M5",
        "direction": "bullish",
        "fvg_high": "4362.440",
        "fvg_low": "4358.320",
        "fvg_midpoint": "4360.380",
        "invalidation_level": "4350.000",
        "midpoint_target": "4370.000",
        "opposite_extreme_target": "4380.000",
        "confidence_score": 72.0,
        "profile": "day_trader",
        "candidate_id": "candidate-1",
        "parent_at_htf_zone": True,
        "profile_min_confidence": 45,
    }

    assert channel.broadcast_fvg_watch(
        **common,
        quality_status=weak_quality.status,
        quality_would_suppress=weak_quality.would_suppress,
    ) == []
    assert channel.broadcast_fvg_watch(
        **common,
        quality_status=quality.status,
        quality_would_suppress=quality.would_suppress,
    ) == [3]


def test_fvg_watch_uses_profile_floor_when_subscriber_preference_is_null(monkeypatch):
    class DefaultThresholdSubscriber:
        def get_subscribers_for_timeframe(self, timeframe, symbol=None):
            return [{
                "chat_id": "profile-floor",
                "active": True,
                "settings": {"min_confidence": None},
            }]

    config = SimpleNamespace(
        enabled=True,
        bot_token="test-token",
        chat_id="123",
        parse_mode="HTML",
        timeout_seconds=2,
        retry_attempts=1,
        mini_app_url="",
    )
    channel = TelegramAlertChannel(config, subscriber_db=DefaultThresholdSubscriber())
    requests = []

    def fake_urlopen(request, timeout):
        requests.append(request)
        return _Response(len(requests))

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    delivered = channel.broadcast_fvg_watch(
        symbol="EURUSD",
        htf_timeframe="H1",
        ltf_timeframe="M5",
        direction="bullish",
        fvg_high="1.10500",
        fvg_low="1.10000",
        fvg_midpoint="1.10250",
        invalidation_level="1.09800",
        midpoint_target="1.10700",
        opposite_extreme_target="1.11000",
        confidence_score=60.0,
        profile="day_trader",
        candidate_id="profile-floor-candidate",
        profile_min_confidence=55.0,
    )

    # One admin notification and one subscriber delivery.
    assert delivered == [2]
    assert len(requests) == 2
