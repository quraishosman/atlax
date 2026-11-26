"""
Tests for Volume Profile candle volume field plumbing.

Verifies that:
  - MT5 feed populates tick_volume and real_volume when the rate dict contains them.
  - Fields default to None when the rate dict is OHLC-only (backward compat).
  - volume_quality property returns the correct VolumeQuality for every case.
  - Existing CRT-style Candle construction with positional args is unaffected.

Authority:
    docs/40_VOLUME_PROFILE_CONFLUENCE_PLAN.md §4 (Volume Source Contract)
    docs/decisions/ADR-017.md
"""

import sys
import types
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from core.models.candle import Candle, CandleSequence
from core.models.volume_source import VolumeQuality
from core.settings.market_data_config import WatchedInstrument
from core.market_data.mt5_candle_feed import MT5CandleFeed


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_candle(
    tick_volume=None,
    real_volume=None,
    volume=None,
) -> Candle:
    now = datetime(2026, 8, 17, 12, 0, tzinfo=timezone.utc)
    return Candle(
        symbol="EURUSD",
        timeframe="M15",
        open_time=now,
        close_time=now,
        open=Decimal("1.1000"),
        high=Decimal("1.1020"),
        low=Decimal("1.0990"),
        close=Decimal("1.1010"),
        is_closed=True,
        volume=volume,
        tick_volume=tick_volume,
        real_volume=real_volume,
    )


def _fake_mt5_module(rates):
    """Build a minimal fake MetaTrader5 module returning the given rate list."""
    fake = types.ModuleType("MetaTrader5")
    fake.TIMEFRAME_M5 = 5
    fake.TIMEFRAME_M15 = 15
    fake.TIMEFRAME_M30 = 30
    fake.TIMEFRAME_H1 = 16385
    fake.TIMEFRAME_H4 = 16388
    fake.TIMEFRAME_D1 = 16408
    fake.TIMEFRAME_W1 = 32769
    fake.TIMEFRAME_MN1 = 49153
    fake.last_error = lambda: (0, "ok")
    fake.copy_rates_from_pos = lambda *a, **kw: rates
    return fake


# ---------------------------------------------------------------------------
# Candle model: volume_quality property
# ---------------------------------------------------------------------------


class TestVolumeQualityProperty:
    def test_real_volume_takes_precedence(self):
        candle = _make_candle(tick_volume=1000, real_volume=5000)
        assert candle.volume_quality == VolumeQuality.REAL

    def test_tick_volume_fallback_when_no_real(self):
        candle = _make_candle(tick_volume=800, real_volume=None)
        assert candle.volume_quality == VolumeQuality.BROKER_TICK_PROXY

    def test_tick_volume_zero_is_unknown(self):
        candle = _make_candle(tick_volume=0, real_volume=None)
        assert candle.volume_quality == VolumeQuality.UNKNOWN

    def test_real_volume_zero_falls_back_to_tick(self):
        candle = _make_candle(tick_volume=500, real_volume=0)
        assert candle.volume_quality == VolumeQuality.BROKER_TICK_PROXY

    def test_both_none_is_unknown(self):
        candle = _make_candle(tick_volume=None, real_volume=None)
        assert candle.volume_quality == VolumeQuality.UNKNOWN

    def test_both_zero_is_unknown(self):
        candle = _make_candle(tick_volume=0, real_volume=0)
        assert candle.volume_quality == VolumeQuality.UNKNOWN

    def test_real_volume_positive_with_zero_tick(self):
        candle = _make_candle(tick_volume=0, real_volume=200)
        assert candle.volume_quality == VolumeQuality.REAL


# ---------------------------------------------------------------------------
# Candle model: backward compatibility
# ---------------------------------------------------------------------------


class TestCandleBackwardCompat:
    def test_ohlc_only_construction_defaults_volume_fields_to_none(self):
        """Existing callers that do not pass volume fields must not break."""
        now = datetime(2026, 8, 17, 12, 0, tzinfo=timezone.utc)
        candle = Candle(
            symbol="GBPUSD",
            timeframe="H4",
            open_time=now,
            close_time=now,
            open=Decimal("1.2700"),
            high=Decimal("1.2750"),
            low=Decimal("1.2680"),
            close=Decimal("1.2730"),
            is_closed=True,
        )
        assert candle.tick_volume is None
        assert candle.real_volume is None
        assert candle.volume is None
        assert candle.volume_quality == VolumeQuality.UNKNOWN

    def test_legacy_volume_field_unaffected(self):
        """The original `volume` (Decimal) field remains independent."""
        candle = _make_candle(volume=Decimal("12345.0"))
        assert candle.volume == Decimal("12345.0")
        assert candle.tick_volume is None
        assert candle.real_volume is None

    def test_frozen_dataclass_immutability(self):
        from dataclasses import FrozenInstanceError
        candle = _make_candle(tick_volume=100)
        with pytest.raises(FrozenInstanceError):
            candle.tick_volume = 999  # type: ignore[misc]


# ---------------------------------------------------------------------------
# MT5 feed: volume field population
# ---------------------------------------------------------------------------

UTC_NOW = 1_000_000.0
BROKER_OFFSET = 10_800  # 3-hour broker timezone offset (seconds)


def _make_feed(monkeypatch, rates):
    instrument = WatchedInstrument("EURUSD", "M15", 50, 14, 0.0001)
    feed = MT5CandleFeed(instrument)

    fake_mt5 = _fake_mt5_module(rates)
    fake_mt5.symbol_info_tick = lambda symbol: types.SimpleNamespace(
        time=int(UTC_NOW + BROKER_OFFSET + 5)
    )
    monkeypatch.setitem(sys.modules, "MetaTrader5", fake_mt5)
    monkeypatch.setattr("core.market_data.mt5_candle_feed.time.time", lambda: UTC_NOW)
    return feed


def _closed_rate(offset_seconds: int, tick_volume=None, real_volume=None) -> dict:
    """Build a closed rate dict whose bar is fully before UTC_NOW."""
    rate = {
        "time": int(UTC_NOW - offset_seconds + BROKER_OFFSET),
        "open": 1.1000,
        "high": 1.1020,
        "low": 1.0980,
        "close": 1.1010,
    }
    if tick_volume is not None:
        rate["tick_volume"] = tick_volume
    if real_volume is not None:
        rate["real_volume"] = real_volume
    return rate


class TestMT5FeedVolumeFields:
    def test_numpy_structured_rate_populates_volume_fields(self, monkeypatch):
        """Live MT5 structured rows must work, not only dictionary fixtures."""
        np = pytest.importorskip("numpy")
        dtype = [
            ("time", "i8"),
            ("open", "f8"),
            ("high", "f8"),
            ("low", "f8"),
            ("close", "f8"),
            ("tick_volume", "i8"),
            ("real_volume", "i8"),
        ]
        rates = np.array([
            (int(UTC_NOW - 1800 + BROKER_OFFSET), 1.1000, 1.1020,
             1.0980, 1.1010, 1234, 8000),
        ], dtype=dtype)

        feed = _make_feed(monkeypatch, rates)
        seq = feed.get_candles()

        assert len(seq.candles) == 1
        assert seq.candles[0].tick_volume == 1234
        assert seq.candles[0].real_volume == 8000
        assert seq.candles[0].volume_quality == VolumeQuality.REAL

    def test_tick_volume_populated_when_present(self, monkeypatch):
        rates = [_closed_rate(1800, tick_volume=1234)]
        feed = _make_feed(monkeypatch, rates)
        seq = feed.get_candles()
        assert len(seq.candles) == 1
        candle = seq.candles[0]
        assert candle.tick_volume == 1234
        assert candle.real_volume is None
        assert candle.volume_quality == VolumeQuality.BROKER_TICK_PROXY

    def test_real_volume_populated_when_present(self, monkeypatch):
        rates = [_closed_rate(1800, tick_volume=500, real_volume=8000)]
        feed = _make_feed(monkeypatch, rates)
        seq = feed.get_candles()
        candle = seq.candles[0]
        assert candle.tick_volume == 500
        assert candle.real_volume == 8000
        assert candle.volume_quality == VolumeQuality.REAL

    def test_ohlc_only_rate_produces_none_volume_fields(self, monkeypatch):
        """Rate dicts without volume keys → both fields None (backward compat)."""
        rates = [_closed_rate(1800)]  # no tick_volume or real_volume key
        feed = _make_feed(monkeypatch, rates)
        seq = feed.get_candles()
        candle = seq.candles[0]
        assert candle.tick_volume is None
        assert candle.real_volume is None
        assert candle.volume_quality == VolumeQuality.UNKNOWN

    def test_zero_tick_volume_produces_none(self, monkeypatch):
        """A rate dict with tick_volume=0 should be treated as absent."""
        rates = [_closed_rate(1800, tick_volume=0)]
        feed = _make_feed(monkeypatch, rates)
        seq = feed.get_candles()
        candle = seq.candles[0]
        assert candle.tick_volume is None
        assert candle.volume_quality == VolumeQuality.UNKNOWN

    def test_multiple_candles_mixed_volume(self, monkeypatch):
        """Volume data can be present on some candles but not others."""
        rates = [
            _closed_rate(3600, tick_volume=900),
            _closed_rate(1800, tick_volume=0),   # zero → None
        ]
        feed = _make_feed(monkeypatch, rates)
        seq = feed.get_candles()
        assert len(seq.candles) == 2
        assert seq.candles[0].volume_quality == VolumeQuality.BROKER_TICK_PROXY
        assert seq.candles[1].volume_quality == VolumeQuality.UNKNOWN

    def test_candle_sequence_symbol_timeframe_preserved(self, monkeypatch):
        rates = [_closed_rate(1800, tick_volume=100)]
        feed = _make_feed(monkeypatch, rates)
        seq = feed.get_candles()
        assert seq.symbol == "EURUSD"
        assert seq.timeframe == "M15"
        assert seq.candles[0].symbol == "EURUSD"
