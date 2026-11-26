from types import SimpleNamespace

from core.market_data.mt5_symbol import resolve_mt5_symbol


class FakeMT5:
    @staticmethod
    def symbol_info(symbol):
        return SimpleNamespace(name=symbol) if symbol in {"EURUSDm", "GBPUSD"} else None


def test_exact_broker_symbol_wins(monkeypatch):
    monkeypatch.setenv("ATLAX_MT5_SYMBOL_SUFFIX", "m")
    assert resolve_mt5_symbol(FakeMT5, "GBPUSD") == "GBPUSD"


def test_configured_suffix_resolves_broker_symbol(monkeypatch):
    monkeypatch.setenv("ATLAX_MT5_SYMBOL_SUFFIX", "m")
    assert resolve_mt5_symbol(FakeMT5, "EURUSD") == "EURUSDm"


def test_missing_suffix_does_not_guess(monkeypatch):
    monkeypatch.delenv("ATLAX_MT5_SYMBOL_SUFFIX", raising=False)
    assert resolve_mt5_symbol(FakeMT5, "USDJPY") is None
