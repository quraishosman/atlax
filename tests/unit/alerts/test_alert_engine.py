import dataclasses
from types import SimpleNamespace
from core.alerts.base import BaseAlertChannel
from core.alerts.alert_engine import AlertEngine
from core.models.alert_event import AlertEvent
from core.settings.alert_config import AlertConfig
from core.alerts.telegram_channel import TelegramAlertChannel
from core.settings.alert_config import TelegramConfig

def test_successful_alert_dispatch(alert_engine, sample_candidate, sample_confidence, sample_profile):
    alert_engine.reset_dedup_cache()
    alert = alert_engine.process(sample_candidate, sample_confidence, sample_profile)
    
    assert alert is not None
    assert alert.symbol == "EURUSD"
    assert alert.direction == "BUY"
    assert alert.confidence_score == 85.0


def test_fvg_prediction_registration_writes_passport(
    sample_candidate,
    sample_confidence,
    sample_profile,
    alert_config,
    tmp_path,
    runtime_store,
):
    engine = AlertEngine(
        alert_config,
        channels=[],
        db_path=str(tmp_path / "journal.sqlite"),
        runtime_store=runtime_store,
    )

    alert = engine.register_fvg_prediction(sample_candidate, sample_confidence)

    assert alert.candidate_id == sample_candidate.candidate_id
    assert alert.alert_id in engine.tracker.predictions
    assert engine.tracker.predictions[alert.alert_id]["telegram_message_id"] is None

def test_below_threshold_suppression(alert_engine, sample_candidate, sample_confidence, sample_profile):
    low_confidence = dataclasses.replace(sample_confidence, final_score=60.0)
    alert_engine.reset_dedup_cache()
    alert = alert_engine.process(sample_candidate, low_confidence, sample_profile)
    
    # The profile min_confidence_threshold is 70.0, so this should be suppressed
    assert alert is None


def test_explicit_lower_subscriber_threshold_bypasses_profile_floor(
    sample_candidate,
    sample_confidence,
    sample_profile,
    alert_config,
    tmp_path,
    runtime_store,
):
    subscribers = [{
        "chat_id": "subscriber-1",
        "active": True,
        "settings": {"min_confidence": 35},
    }]
    channel = TelegramAlertChannel(
        alert_config.telegram,
        subscriber_db=SimpleNamespace(
            get_subscribers_for_timeframe=lambda timeframe, symbol=None: subscribers,
        ),
    )
    channel.send = lambda alert: False
    channel.broadcast_to_subscribers = lambda *args, **kwargs: [101]
    engine = AlertEngine(
        alert_config,
        channels=[channel],
        db_path=str(tmp_path / "journal.sqlite"),
        runtime_store=runtime_store,
    )

    low_confidence = dataclasses.replace(sample_confidence, final_score=60.0)
    alert = engine.process(sample_candidate, low_confidence, sample_profile)

    assert alert is not None
    assert alert.confidence_score == 60.0


def test_alert_suppression_is_counted(sample_candidate, sample_confidence, sample_profile, alert_config, tmp_path):
    from core.alerts.alert_engine import AlertEngine
    from core.alerts.log_channel import LogAlertChannel
    from core.operations.monitoring import OperationalMetricsLedger

    class MemoryRuntime:
        def __init__(self):
            self.values = {}

        def get(self, key, default=None):
            return self.values.get(key, default)

        def set(self, key, value):
            self.values[key] = value

    monitoring = OperationalMetricsLedger(MemoryRuntime())
    engine = AlertEngine(
        alert_config,
        channels=[LogAlertChannel(enabled=True)],
        db_path=str(tmp_path / "journal.sqlite"),
        monitoring=monitoring,
    )
    low_confidence = dataclasses.replace(sample_confidence, final_score=60.0)

    assert engine.process(sample_candidate, low_confidence, sample_profile) is None
    report = monitoring.report()
    assert report["alert_suppressed"] == 1
    assert list(report["alert_suppressed_by_reason"]) == ["below_threshold(60.0<70.0)"]

def test_deduplication_suppression(alert_engine, sample_candidate, sample_confidence, sample_profile):
    alert_engine.reset_dedup_cache()
    
    # First alert goes through
    alert1 = alert_engine.process(sample_candidate, sample_confidence, sample_profile)
    assert alert1 is not None
    
    # Second identical alert should be suppressed
    alert2 = alert_engine.process(sample_candidate, sample_confidence, sample_profile)
    assert alert2 is None

def test_dedup_disabled(sample_candidate, sample_confidence, sample_profile, alert_config, tmp_path, runtime_store):
    # Disable deduplication by setting window to 0
    no_dedup_config = dataclasses.replace(alert_config, deduplication_window_seconds=0)
    from core.alerts.alert_engine import AlertEngine
    from core.alerts.log_channel import LogAlertChannel
    
    engine = AlertEngine(
        no_dedup_config,
        channels=[LogAlertChannel(enabled=True)],
        db_path=str(tmp_path / "journal.sqlite"),
        runtime_store=runtime_store,
    )
    engine.reset_dedup_cache()
    
    alert1 = engine.process(sample_candidate, sample_confidence, sample_profile)
    alert2 = engine.process(sample_candidate, sample_confidence, sample_profile)
    
    assert alert1 is not None
    assert alert2 is not None


class ExplodingChannel(BaseAlertChannel):
    name = "exploding"
    is_enabled = True

    def send(self, alert: AlertEvent) -> bool:
        raise RuntimeError("channel exploded")


class FlakyChannel(BaseAlertChannel):
    name = "flaky"
    is_enabled = True

    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = 0

    def send(self, alert: AlertEvent) -> bool:
        self.calls += 1
        return self.outcomes.pop(0) if self.outcomes else False


def test_failed_delivery_does_not_consume_dedup_window(
    sample_candidate,
    sample_confidence,
    sample_profile,
    alert_config,
    tmp_path,
    runtime_store,
):
    channel = FlakyChannel([False, True])
    engine = AlertEngine(
        alert_config,
        channels=[channel],
        db_path=str(tmp_path / "journal.sqlite"),
        runtime_store=runtime_store,
    )

    first = engine.process(sample_candidate, sample_confidence, sample_profile)
    second = engine.process(sample_candidate, sample_confidence, sample_profile)
    third = engine.process(sample_candidate, sample_confidence, sample_profile)

    assert first is not None
    assert second is not None
    assert third is None
    assert channel.calls == 2


def test_telegram_retryable_delivery_is_exposed_to_scanner(
    sample_candidate,
    sample_confidence,
    sample_profile,
    alert_config,
    tmp_path,
    runtime_store,
):
    channel = TelegramAlertChannel(
        alert_config.telegram,
        subscriber_db=SimpleNamespace(
            get_subscribers_for_timeframe=lambda timeframe, symbol=None: [],
        ),
    )
    calls = []
    channel.send = lambda alert: 999

    def retryable_broadcast(*args, **kwargs):
        calls.append(1)
        channel.last_delivery_status = "retryable"
        return []

    channel.broadcast_to_subscribers = retryable_broadcast
    engine = AlertEngine(
        alert_config,
        channels=[channel],
        db_path=str(tmp_path / "journal.sqlite"),
        runtime_store=runtime_store,
    )

    first = engine.process(sample_candidate, sample_confidence, sample_profile)
    second = engine.process(sample_candidate, sample_confidence, sample_profile)

    assert first is not None
    assert second is not None
    assert engine.last_delivery_status == "retryable"
    assert engine.last_telegram_delivery_status == "retryable"
    assert len(calls) == 2


def test_channel_exception_does_not_abort_alert_dispatch(sample_candidate, sample_confidence, sample_profile, alert_config, tmp_path, runtime_store):
    from core.alerts.alert_engine import AlertEngine
    from core.alerts.log_channel import LogAlertChannel

    engine = AlertEngine(
        alert_config,
        channels=[LogAlertChannel(enabled=True), ExplodingChannel()],
        db_path=str(tmp_path / "journal.sqlite"),
        runtime_store=runtime_store,
    )
    engine.reset_dedup_cache()

    alert = engine.process(sample_candidate, sample_confidence, sample_profile)

    assert alert is not None
    assert alert.symbol == "EURUSD"
