from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

from core.market_data.mt5_account_identity import read_mt5_account_identity
from core.models.account_mode import (
    AccountModeStatus,
    MT5AccountIdentity,
    TradingAccountMode,
)
from core.models.prop_firm import (
    AccountReferenceBasis,
    LossLimitMode,
    LossLimitSpec,
    PropFirmRuleProfile,
)
from core.models.risk import AccountState
from core.risk.account_mode import (
    bind_current_account,
    load_account_mode_state,
    record_verification,
    set_account_mode,
    verify_account_binding,
)
from core.risk.account_mode import (
    ConcurrentAccountModeUpdate,
    _save,
)
from core.risk.prop_firm_runtime import PropFirmRuntimeAdapter
from core.settings.account_mode_config import AccountModeConfig
from core.settings.prop_firm_config import PropFirmRecoveryConfig


NOW = datetime(2026, 9, 19, 12, 0, tzinfo=timezone.utc)


class MemoryStore:
    def __init__(self):
        self.values = {}

    def get(self, key, default=None):
        return self.values.get(key, default)

    def set(self, key, value):
        self.values[key] = value


def _identity(**overrides):
    values = dict(
        login=123456,
        server="Broker-Server",
        currency="USD",
        trade_mode="DEMO",
        terminal_name="MetaTrader 5",
        terminal_build=5000,
        terminal_instance_id="scanner-one",
        observed_at=NOW,
    )
    values.update(overrides)
    return MT5AccountIdentity(**values)


def _profile():
    return PropFirmRuleProfile(
        profile_id="approved-funded-usd",
        firm_name="Approved Firm",
        program_name="Funded Program",
        phase="FUNDED",
        account_type="STANDARD",
        currency="USD",
        overall_loss=LossLimitSpec(
            mode=LossLimitMode.STATIC,
            basis=AccountReferenceBasis.INITIAL_BALANCE,
            percentage=Decimal("8"),
        ),
        daily_loss=LossLimitSpec(
            mode=LossLimitMode.STATIC,
            basis=AccountReferenceBasis.OPENING_EQUITY,
            percentage=Decimal("5"),
            reset_timezone="UTC",
        ),
        source_urls=("https://firm.example/rules",),
        source_retrieved_at=NOW,
        version="2026-09-19.1",
        content_hash="abc123",
        nominal_balance=Decimal("2000"),
    )


def _account():
    return AccountState(
        balance=Decimal("2000"),
        equity=Decimal("1980"),
        daily_pnl=Decimal("0"),
        weekly_pnl=Decimal("0"),
        open_trade_count=0,
        trades_today=0,
        open_symbols=(),
        currency="USD",
        snapshot_at=NOW,
    )


def test_identity_reader_returns_secret_free_identity():
    mt5 = SimpleNamespace(
        account_info=lambda: SimpleNamespace(
            login=123456,
            server="Broker-Server",
            currency="USD",
            trade_mode=0,
        ),
        terminal_info=lambda: SimpleNamespace(name="MetaTrader 5", build=5000),
    )

    identity = read_mt5_account_identity(mt5, terminal_instance_id="scanner-one", observed_at=NOW)

    assert identity.login == 123456
    assert identity.server == "Broker-Server"
    assert identity.currency == "USD"
    assert len(identity.fingerprint) == 20
    assert "password" not in identity.to_dict()


def test_mode_state_binds_and_detects_account_mismatch():
    store = MemoryStore()
    identity = _identity()
    bind_current_account(store, identity, actor="admin")
    set_account_mode(
        store,
        mode=TradingAccountMode.PROP_FIRM_ENFORCED,
        actor="admin",
        selected_profile_id="approved-funded-usd",
        selected_profile_version="2026-09-19.1",
    )
    state = load_account_mode_state(store)

    status, reason = verify_account_binding(state, _identity(server="Other-Server"))

    assert status is AccountModeStatus.MISMATCH
    assert "does not match" in reason


def test_normal_mode_does_not_require_binding():
    state = load_account_mode_state(MemoryStore())
    status, reason = verify_account_binding(state, None)
    assert status is AccountModeStatus.NOT_REQUIRED
    assert "Normal" in reason


def test_invalid_persisted_mode_fails_closed_as_blocked_prop_firm_state():
    store = MemoryStore()
    store.values["account_mode.state"] = {"binding": {"not": "a valid binding"}}

    state = load_account_mode_state(store)

    assert state.mode is TradingAccountMode.PROP_FIRM_ENFORCED
    assert state.status is AccountModeStatus.UNKNOWN
    assert "blocked" in state.status_reason


def test_runtime_adapter_builds_dynamic_context_and_history():
    store = MemoryStore()
    identity = _identity()
    bind_current_account(store, identity, actor="admin")
    set_account_mode(
        store,
        mode=TradingAccountMode.PROP_FIRM_SHADOW,
        actor="admin",
        selected_profile_id="approved-funded-usd",
        selected_profile_version="2026-09-19.1",
    )
    state = load_account_mode_state(store)
    config = AccountModeConfig(
        enabled=True,
        max_open_risk_fraction=Decimal("0.5"),
        new_trade_buffer_fraction=Decimal("0.5"),
    )
    result = PropFirmRuntimeAdapter(store, config, (_profile(),)).resolve(
        state=state,
        identity=identity,
        account=_account(),
        open_risk=Decimal("10"),
        now=NOW,
    )

    assert result.status is AccountModeStatus.VERIFIED
    assert result.context is not None
    assert result.context.snapshot.initial_balance == Decimal("2000")
    assert result.context.snapshot.opening_equity == Decimal("1980")
    assert store.get("prop_firm.snapshot")["account_fingerprint"] == identity.fingerprint


def test_runtime_adapter_scopes_and_persists_recovery_state():
    store = MemoryStore()
    identity = _identity()
    profile = _profile()
    bind_current_account(store, identity, actor="admin")
    set_account_mode(
        store,
        mode=TradingAccountMode.PROP_FIRM_ENFORCED,
        actor="admin",
        selected_profile_id=profile.profile_id,
        selected_profile_version=profile.version,
    )
    config = AccountModeConfig(
        enabled=True,
        max_open_risk_fraction=Decimal("0.5"),
        new_trade_buffer_fraction=Decimal("0.5"),
        recovery_config=PropFirmRecoveryConfig(
            enabled=True,
            caution_drawdown_fraction=Decimal("0.25"),
            recovery_drawdown_fraction=Decimal("0.50"),
            caution_risk_multiplier=Decimal("0.75"),
            recovery_risk_multiplier=Decimal("0.40"),
            minimum_risk_multiplier=Decimal("0.20"),
            consecutive_loss_trigger=3,
            restore_after_drawdown_recovered=True,
            restore_profit_cushion=Decimal("10"),
            stabilization_closed_trades=2,
        ),
    )

    result = PropFirmRuntimeAdapter(store, config, (profile,)).resolve(
        state=load_account_mode_state(store),
        identity=identity,
        account=_account(),
        open_risk=Decimal("0"),
        now=NOW,
    )

    assert result.status is AccountModeStatus.VERIFIED
    assert result.context is not None
    assert result.context.recovery_state is not None
    assert result.context.recovery_state.mode.value == "NORMAL"
    assert result.context.recovery_state_key
    assert store.get(result.context.recovery_state_key)["mode"] == "NORMAL"
    assert store.get("prop_firm.recovery_state_binding") == {
        "account_fingerprint": identity.fingerprint,
        "profile_id": profile.profile_id,
        "profile_version": profile.version,
    }


def test_runtime_adapter_blocks_when_operational_caps_are_missing():
    store = MemoryStore()
    identity = _identity()
    bind_current_account(store, identity, actor="admin")
    set_account_mode(
        store,
        mode=TradingAccountMode.PROP_FIRM_ENFORCED,
        actor="admin",
        selected_profile_id="approved-funded-usd",
        selected_profile_version="2026-09-19.1",
    )
    result = PropFirmRuntimeAdapter(
        store,
        AccountModeConfig(enabled=False),
        (_profile(),),
    ).resolve(
        state=load_account_mode_state(store),
        identity=identity,
        account=_account(),
        open_risk=Decimal("0"),
        now=NOW,
    )
    assert result.status is AccountModeStatus.UNKNOWN
    assert result.context is None


def test_verification_state_is_restart_safe():
    store = MemoryStore()
    updated = record_verification(
        store,
        status=AccountModeStatus.MISMATCH,
        reason="server mismatch",
        actor="runtime",
        verified_at=NOW,
    )
    assert updated.status is AccountModeStatus.MISMATCH
    assert load_account_mode_state(store).status_reason == "server mismatch"


def test_account_mode_updates_use_monotonic_revision_and_reject_stale_writes():
    store = MemoryStore()
    stale = load_account_mode_state(store)
    first = set_account_mode(
        store,
        mode=TradingAccountMode.NORMAL_ACCOUNT,
        actor="admin",
    )

    assert first.revision == 1
    assert load_account_mode_state(store).revision == 1

    try:
        _save(store, stale, "stale-callback")
    except ConcurrentAccountModeUpdate:
        pass
    else:
        raise AssertionError("stale account-mode state was accepted")

    assert load_account_mode_state(store).revision == 1
