import pytest
import sys
import types
from datetime import datetime, timezone
from decimal import Decimal

from core.settings.market_data_config import WatchedInstrument, MT5ConnectionConfig
from core.market_data.mt5_candle_feed import MT5CandleFeed
from core.market_data.mt5_connection import MT5Connection
from core.models.candle import Candle, CandleSequence

def test_watched_instrument_validation():
    # Valid
    inst = WatchedInstrument('EURUSD', 'H4', 50, 14, 0.0001)
    assert inst.symbol == 'EURUSD'
    
    # Invalid candle_count
    with pytest.raises(ValueError):
        WatchedInstrument('EURUSD', 'H4', 2, 14, 0.0001)

    # Invalid atr_period
    with pytest.raises(ValueError):
        WatchedInstrument('EURUSD', 'H4', 50, 0, 0.0001)

    # Retired timeframe
    with pytest.raises(ValueError, match="retired"):
        WatchedInstrument('EURUSD', 'M1', 50, 14, 0.0001)

def test_mt5_connection_config_validation():
    # Valid
    MT5ConnectionConfig(account=0, server='', password='', timeout_ms=60000, max_retries=3)
    
    # Invalid timeout
    with pytest.raises(ValueError):
        MT5ConnectionConfig(account=0, server='', password='', timeout_ms=50, max_retries=3)


def test_mt5_connection_prefers_matching_terminal_session(monkeypatch):
    calls = []
    fake_mt5 = types.ModuleType("MetaTrader5")
    fake_mt5.initialize = lambda **kwargs: calls.append(kwargs) or True
    fake_mt5.last_error = lambda: (1, "Success")
    fake_mt5.terminal_info = lambda: types.SimpleNamespace(name="MetaTrader 5", build=6204)
    fake_mt5.account_info = lambda: types.SimpleNamespace(
        login=10422092, server="MavenTrade-Server"
    )
    fake_mt5.shutdown = lambda: None
    monkeypatch.setitem(sys.modules, "MetaTrader5", fake_mt5)

    connection = MT5Connection(MT5ConnectionConfig(
        account=10422092,
        server="MavenTrade-Server",
        password="stale-password",
        timeout_ms=60000,
        max_retries=1,
    ))
    connection.initialize()

    assert connection.is_connected is True
    assert calls == [{"timeout": 60000}]

def test_to_decimal_conversion():
    from core.market_data.mt5_candle_feed import _to_decimal
    from decimal import Decimal
    
    assert _to_decimal(1.08523, 0.0001) == Decimal('1.08523')
    assert _to_decimal(1950.55, 0.01) == Decimal('1950.550')
    assert _to_decimal(130.456, 0.001) == Decimal('130.456')

def test_timeframe_seconds():
    from core.market_data.mt5_candle_feed import MT5CandleFeed
    
    with pytest.raises(ValueError, match="retired"):
        MT5CandleFeed._timeframe_seconds('M1')
    assert MT5CandleFeed._timeframe_seconds('H4') == 14400
    assert MT5CandleFeed._timeframe_seconds('D1') == 86400
    assert MT5CandleFeed._timeframe_seconds('W1') == 604800


def test_get_candles_normalizes_broker_clock_and_rejects_forming_bar(monkeypatch):
    instrument = WatchedInstrument('GBPUSD', 'M5', 50, 14, 0.0001)
    feed = MT5CandleFeed(instrument)
    utc_now = 1_000_000.0
    broker_offset = 10_800

    fake_mt5 = types.ModuleType("MetaTrader5")
    fake_mt5.TIMEFRAME_M1 = 1
    fake_mt5.TIMEFRAME_M5 = 5
    fake_mt5.TIMEFRAME_M15 = 15
    fake_mt5.TIMEFRAME_M30 = 30
    fake_mt5.TIMEFRAME_H1 = 16385
    fake_mt5.TIMEFRAME_H4 = 16388
    fake_mt5.TIMEFRAME_D1 = 16408
    fake_mt5.TIMEFRAME_W1 = 32769
    fake_mt5.TIMEFRAME_MN1 = 49153
    fake_mt5.symbol_info_tick = lambda symbol: types.SimpleNamespace(
        time=int(utc_now + broker_offset + 17)
    )
    fake_mt5.copy_rates_from_pos = lambda symbol, timeframe, position, count: [
        {
            "time": int(utc_now - 600 + broker_offset),
            "open": 1.1000,
            "high": 1.1010,
            "low": 1.0990,
            "close": 1.1005,
        },
        {
            "time": int(utc_now - 100 + broker_offset),
            "open": 1.1005,
            "high": 1.1015,
            "low": 1.1000,
            "close": 1.1010,
        },
    ]
    monkeypatch.setitem(sys.modules, "MetaTrader5", fake_mt5)
    monkeypatch.setattr("core.market_data.mt5_candle_feed.time.time", lambda: utc_now)

    closed = feed.get_candles(include_live=False)
    assert len(closed.candles) == 1
    assert closed.candles[0].is_closed is True
    assert closed.candles[0].open_time == datetime.fromtimestamp(
        utc_now - 600,
        timezone.utc,
    )

    with_live = feed.get_candles(include_live=True)
    assert len(with_live.candles) == 2
    assert with_live.candles[-1].is_closed is False
    assert with_live.candles[-1].open_time == datetime.fromtimestamp(
        utc_now - 100,
        timezone.utc,
    )


def test_atr_returns_zero_when_period_is_not_fully_available(monkeypatch):
    instrument = WatchedInstrument('EURUSD', 'H1', 50, 14, 0.0001)
    feed = MT5CandleFeed(instrument)
    now = datetime(2026, 8, 13, tzinfo=timezone.utc)
    candles = tuple(
        Candle(
            "EURUSD", "H1", now, now,
            Decimal("1.1000"), Decimal("1.1010"), Decimal("1.0990"),
            Decimal("1.1005"), True,
        )
        for _ in range(3)
    )
    monkeypatch.setattr(
        feed,
        "get_candles",
        lambda count=None: CandleSequence(candles, "EURUSD", "H1"),
    )

    assert feed.get_atr(14) == Decimal("0")
