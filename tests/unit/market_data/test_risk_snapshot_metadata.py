from decimal import Decimal
from types import SimpleNamespace

from core.market_data.scanner import MarketScanner


def _scanner(monkeypatch, mt5_adapter):
    scanner = object.__new__(MarketScanner)
    scanner._execution_cfg = SimpleNamespace(magic_number=20260101)
    monkeypatch.setattr("core.market_data.scanner.mt5", mt5_adapter)
    return scanner


def _account_info():
    return SimpleNamespace(balance=10000, equity=9987.5, currency="USD")


def test_live_risk_snapshot_uses_candidate_symbol_metadata(monkeypatch):
    queried_symbols = []

    class FakeMT5:
        @staticmethod
        def symbol_info(symbol):
            queried_symbols.append(symbol)
            return SimpleNamespace(
                trade_tick_value=1.25,
                trade_tick_size=0.01,
                volume_min=0.01,
                volume_max=50.0,
                volume_step=0.01,
            )

    state = _scanner(monkeypatch, FakeMT5)._build_live_risk_account_state(
        _account_info(),
        [SimpleNamespace(symbol="EURUSD")],
        "XAUUSD",
    )

    assert queried_symbols == ["XAUUSD"]
    assert state.symbol_tick_value == Decimal("1.25")
    assert state.symbol_tick_size == Decimal("0.01")
    assert state.symbol_volume_min == Decimal("0.01")
    assert state.symbol_volume_max == Decimal("50.0")
    assert state.symbol_volume_step == Decimal("0.01")
    assert state.broker_metadata_required is True
    assert state.daily_trade_count_available is False
    assert state.daily_pnl_available is False
    assert state.weekly_pnl_available is False


def test_live_risk_snapshot_requires_metadata_when_symbol_info_is_unavailable(monkeypatch):
    class FakeMT5:
        @staticmethod
        def symbol_info(symbol):
            return None

    state = _scanner(monkeypatch, FakeMT5)._build_live_risk_account_state(
        _account_info(),
        (),
        "USDJPY",
    )

    assert state.symbol_tick_value is None
    assert state.symbol_tick_size is None
    assert state.symbol_volume_min is None
    assert state.symbol_volume_max is None
    assert state.symbol_volume_step is None
    assert state.broker_metadata_required is True
    assert state.daily_trade_count_available is False
    assert state.daily_pnl_available is False
    assert state.weekly_pnl_available is False


def test_live_risk_snapshot_counts_distinct_atlax_opening_trades(monkeypatch):
    class FakeMT5:
        DEAL_ENTRY_IN = 0

        @staticmethod
        def symbol_info(symbol):
            return SimpleNamespace(
                trade_tick_value=1.25,
                trade_tick_size=0.01,
                volume_min=0.01,
                volume_max=50.0,
                volume_step=0.01,
            )

        @staticmethod
        def history_deals_get(start, end):
            return (
                SimpleNamespace(magic=20260101, entry=0, position_id=70, order=170, ticket=270),
                SimpleNamespace(magic=20260101, entry=0, position_id=70, order=170, ticket=271),
                SimpleNamespace(magic=20260101, entry=0, position_id=71, order=171, ticket=272),
                SimpleNamespace(magic=999, entry=0, position_id=72, order=172, ticket=273),
                SimpleNamespace(
                    magic=20260101,
                    entry=1,
                    position_id=70,
                    order=170,
                    ticket=274,
                    profit=10,
                    swap=-1,
                    commission=-2,
                    fee=0,
                ),
            )

    state = _scanner(monkeypatch, FakeMT5)._build_live_risk_account_state(
        _account_info(),
        (),
        "EURUSD",
    )

    assert state.trades_today == 2
    assert state.daily_trade_count_available is True
    assert state.daily_pnl == Decimal("7")
    assert state.daily_pnl_available is True
    assert state.weekly_pnl == Decimal("7")
    assert state.weekly_pnl_available is True


def test_live_risk_snapshot_marks_history_failure_unavailable(monkeypatch):
    class FakeMT5:
        @staticmethod
        def symbol_info(symbol):
            return None

        @staticmethod
        def history_deals_get(start, end):
            return None

    state = _scanner(monkeypatch, FakeMT5)._build_live_risk_account_state(
        _account_info(),
        (),
        "EURUSD",
    )

    assert state.trades_today == 0
    assert state.daily_trade_count_available is False
    assert state.daily_pnl == Decimal("0")
    assert state.daily_pnl_available is False
    assert state.weekly_pnl == Decimal("0")
    assert state.weekly_pnl_available is False


def test_weekly_pnl_uses_net_atlax_exit_deals_and_iso_week(monkeypatch):
    observed_ranges = []

    class FakeMT5:
        DEAL_ENTRY_OUT = 1

        @staticmethod
        def history_deals_get(start, end):
            observed_ranges.append((start, end))
            return (
                SimpleNamespace(magic=20260101, entry=1, profit=100, swap=-2, commission=-4, fee=-1),
                SimpleNamespace(magic=20260101, entry=1, profit=-30, swap=0, commission=-1, fee=0),
                SimpleNamespace(magic=999, entry=1, profit=-999, swap=0, commission=0, fee=0),
                SimpleNamespace(magic=20260101, entry=0, profit=0, swap=0, commission=-1, fee=0),
            )

    weekly_pnl, available = _scanner(monkeypatch, FakeMT5)._get_atlax_weekly_pnl()

    start, end = observed_ranges[0]
    assert start.weekday() == 0
    assert (start.hour, start.minute, start.second, start.microsecond) == (0, 0, 0, 0)
    assert start.tzinfo is not None
    assert end >= start
    assert weekly_pnl == Decimal("62")
    assert available is True


def test_weekly_pnl_marks_malformed_matching_exit_unavailable(monkeypatch):
    class FakeMT5:
        DEAL_ENTRY_OUT = 1

        @staticmethod
        def history_deals_get(start, end):
            return (
                SimpleNamespace(magic=20260101, entry=1, profit=20, swap=0, commission=None, fee=0),
            )

    weekly_pnl, available = _scanner(monkeypatch, FakeMT5)._get_atlax_weekly_pnl()

    assert weekly_pnl == Decimal("0")
    assert available is False


def test_daily_pnl_uses_net_atlax_exit_deals_and_utc_day(monkeypatch):
    observed_ranges = []

    class FakeMT5:
        DEAL_ENTRY_OUT = 1

        @staticmethod
        def history_deals_get(start, end):
            observed_ranges.append((start, end))
            return (
                SimpleNamespace(magic=20260101, entry=1, profit=50, swap=-1, commission=-2, fee=0),
                SimpleNamespace(magic=999, entry=1, profit=-999, swap=0, commission=0, fee=0),
            )

    daily_pnl, available = _scanner(monkeypatch, FakeMT5)._get_atlax_daily_pnl()

    start, end = observed_ranges[0]
    assert (start.hour, start.minute, start.second, start.microsecond) == (0, 0, 0, 0)
    assert start.tzinfo is not None
    assert end >= start
    assert daily_pnl == Decimal("47")
    assert available is True


def test_daily_pnl_marks_malformed_matching_exit_unavailable(monkeypatch):
    class FakeMT5:
        DEAL_ENTRY_OUT = 1

        @staticmethod
        def history_deals_get(start, end):
            return (
                SimpleNamespace(magic=20260101, entry=1, profit=20, swap=0, commission=None, fee=0),
            )

    daily_pnl, available = _scanner(monkeypatch, FakeMT5)._get_atlax_daily_pnl()

    assert daily_pnl == Decimal("0")
    assert available is False
