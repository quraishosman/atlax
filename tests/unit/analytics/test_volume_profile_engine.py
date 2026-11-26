"""Runtime contract tests for the completed-period Volume Profile engine."""

from datetime import datetime, timezone
from decimal import Decimal

from core.analytics.volume_profile_engine import VolumeProfileEngine
from core.models.candle import Candle
from core.models.volume_profile_result import VolumeProfileStatus
from core.settings.volume_profile_config import VolumeProfileConfig


def _candle() -> Candle:
    return Candle(
        symbol="EURUSD",
        timeframe="M15",
        open_time=datetime(2026, 8, 15, 12, 0, tzinfo=timezone.utc),
        close_time=datetime(2026, 8, 15, 12, 15, tzinfo=timezone.utc),
        open=Decimal("1.1000"),
        high=Decimal("1.1010"),
        low=Decimal("1.0990"),
        close=Decimal("1.1005"),
        is_closed=True,
        tick_volume=100,
    )


def _config(**overrides) -> VolumeProfileConfig:
    values = {
        "enabled": True,
        "shadow_mode": True,
        "profile_periods": ("PREVIOUS_DAY",),
        "minimum_source_bars": 1,
        "minimum_valid_volume_bars": 1,
    }
    values.update(overrides)
    return VolumeProfileConfig(**values)


def test_engine_calculates_completed_period_and_caches_repeat_evaluation():
    engine = VolumeProfileEngine(_config())
    reference = datetime(2026, 8, 16, 12, 0, tzinfo=timezone.utc)
    candles = (_candle(),)

    first = engine.evaluate(
        symbol="EURUSD",
        instrument_class="forex_non_jpy",
        candles=candles,
        tick_size=Decimal("0.00001"),
        reference_utc=reference,
    )
    second = engine.evaluate(
        symbol="EURUSD",
        instrument_class="forex_non_jpy",
        candles=candles,
        tick_size=Decimal("0.00001"),
        reference_utc=reference,
    )

    assert first["PREVIOUS_DAY"].status == VolumeProfileStatus.VALID
    assert second == first
    assert engine.last_cache_hits == ("PREVIOUS_DAY",)


def test_engine_snapshot_changes_when_scoring_configuration_changes():
    first = VolumeProfileEngine(_config(value_area_percent=0.70))
    second = VolumeProfileEngine(_config(value_area_percent=0.71))

    assert first.configuration_snapshot_id != second.configuration_snapshot_id

