from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

from core.market_data.scanner import MarketScanner
from core.models.account_mode import MT5AccountIdentity, TradingAccountMode
from core.models.prop_firm import (
    AccountReferenceBasis,
    LossLimitMode,
    LossLimitSpec,
    PropFirmRuleProfile,
)
from core.models.risk import AccountState
from core.risk.account_mode import bind_current_account, set_account_mode
from core.risk.prop_firm_runtime import PropFirmRuntimeAdapter
from core.settings.account_mode_config import AccountModeConfig


class MemoryStore:
    def __init__(self):
        self.values = {}

    def get(self, key, default=None):
        return self.values.get(key, default)

    def set(self, key, value):
        self.values[key] = value


def _profile() -> PropFirmRuleProfile:
    return PropFirmRuleProfile(
        profile_id="scanner-wiring-profile",
        firm_name="Example Firm",
        program_name="Example Program",
        phase="FUNDED",
        account_type="STANDARD",
        currency="USD",
        overall_loss=LossLimitSpec(
            mode=LossLimitMode.STATIC,
            basis=AccountReferenceBasis.INITIAL_BALANCE,
            percentage=Decimal("8"),
        ),
        daily_loss=None,
        source_urls=("https://example.test/rules",),
        source_retrieved_at=datetime.now(timezone.utc),
        version="1.0",
        content_hash="sha256:scanner-wiring",
        nominal_balance=Decimal("10000"),
    )


def _identity() -> MT5AccountIdentity:
    return MT5AccountIdentity(
        login=123456,
        server="Example-Server",
        currency="USD",
        trade_mode="DEMO",
        terminal_name="MetaTrader 5",
        terminal_build=5000,
        terminal_instance_id=None,
        observed_at=datetime.now(timezone.utc),
    )


def _account() -> AccountState:
    return AccountState(
        balance=Decimal("10000"),
        equity=Decimal("10000"),
        daily_pnl=Decimal("0"),
        weekly_pnl=Decimal("0"),
        open_trade_count=0,
        trades_today=0,
        open_symbols=(),
        currency="USD",
        snapshot_at=datetime.now(timezone.utc),
        daily_trade_count_available=True,
        daily_pnl_available=True,
        weekly_pnl_available=True,
    )


def _scanner(store, profile):
    scanner = object.__new__(MarketScanner)
    scanner._runtime_store = store
    scanner._execution_cfg = SimpleNamespace(magic_number=20260101)
    scanner._account_mode_cfg = AccountModeConfig(
        enabled=True,
        max_open_risk_fraction=Decimal("0.5"),
        new_trade_buffer_fraction=Decimal("0.5"),
    )
    scanner._prop_firm_profiles = (profile,)
    scanner._prop_firm_runtime = PropFirmRuntimeAdapter(
        store,
        scanner._account_mode_cfg,
        scanner._prop_firm_profiles,
    )
    return scanner


def test_scanner_builds_context_only_for_bound_account(monkeypatch):
    store = MemoryStore()
    profile = _profile()
    identity = _identity()
    bind_current_account(store, identity, actor="test")
    set_account_mode(
        store,
        mode=TradingAccountMode.PROP_FIRM_SHADOW,
        actor="test",
        selected_profile_id=profile.profile_id,
        selected_profile_version=profile.version,
    )

    class FakeMT5:
        @staticmethod
        def account_info():
            return SimpleNamespace(login=identity.login, server=identity.server, currency="USD")

        @staticmethod
        def terminal_info():
            return SimpleNamespace(build=identity.terminal_build, name=identity.terminal_name)

        @staticmethod
        def positions_get():
            return []

    monkeypatch.setattr("core.market_data.scanner.mt5", FakeMT5)
    scanner = _scanner(store, profile)
    context = scanner._resolve_prop_firm_context(
        SimpleNamespace(candidate_id="candidate-1"),
        _account(),
        positions=[],
    )

    assert context is not None
    assert context.profile.profile_id == profile.profile_id
    assert store.get("account_mode.state")["status"] == "VERIFIED"


def test_scanner_returns_unknown_when_open_risk_cannot_be_valued(monkeypatch):
    store = MemoryStore()
    profile = _profile()
    identity = _identity()
    bind_current_account(store, identity, actor="test")
    set_account_mode(
        store,
        mode=TradingAccountMode.PROP_FIRM_ENFORCED,
        actor="test",
        selected_profile_id=profile.profile_id,
        selected_profile_version=profile.version,
    )

    class FakeMT5:
        @staticmethod
        def account_info():
            return SimpleNamespace(login=identity.login, server=identity.server, currency="USD")

        @staticmethod
        def terminal_info():
            return SimpleNamespace(build=identity.terminal_build, name=identity.terminal_name)

        @staticmethod
        def positions_get():
            return [SimpleNamespace(magic=20260101, symbol="EURUSD", sl=0, price_open=1.1, volume=1.0)]

        @staticmethod
        def symbol_info(symbol):
            return SimpleNamespace(trade_tick_value=None, trade_tick_size=None)

    monkeypatch.setattr("core.market_data.scanner.mt5", FakeMT5)
    scanner = _scanner(store, profile)
    context = scanner._resolve_prop_firm_context(
        SimpleNamespace(candidate_id="candidate-2"),
        _account(),
    )

    assert context is None
    assert store.get("account_mode.state")["status"] == "UNKNOWN"


def test_normal_account_mode_never_promotes_an_old_prop_firm_binding(monkeypatch):
    store = MemoryStore()
    profile = _profile()
    identity = _identity()
    bind_current_account(store, identity, actor="test")
    set_account_mode(
        store,
        mode=TradingAccountMode.NORMAL_ACCOUNT,
        actor="test",
        selected_profile_id=profile.profile_id,
        selected_profile_version=profile.version,
    )

    class FakeMT5:
        @staticmethod
        def account_info():
            return SimpleNamespace(login=identity.login, server=identity.server, currency="USD")

        @staticmethod
        def terminal_info():
            return SimpleNamespace(build=identity.terminal_build, name=identity.terminal_name)

    monkeypatch.setattr("core.market_data.scanner.mt5", FakeMT5)
    scanner = _scanner(store, profile)
    scanner._account_mode_cfg = AccountModeConfig(
        enabled=True,
        auto_enforce_when_bound=True,
        max_open_risk_fraction=Decimal("0.5"),
        new_trade_buffer_fraction=Decimal("0.5"),
    )

    context = scanner._resolve_prop_firm_context(
        SimpleNamespace(candidate_id="normal-account-candidate"),
        _account(),
        positions=[],
    )

    assert context is None
    assert store.get("account_mode.state")["mode"] == "NORMAL_ACCOUNT"
