from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal

from core.models.account_mode import AccountModeStatus, TradingAccountMode
from core.models.risk import AccountState
from core.risk.circuit_breaker import (
    CircuitBreaker,
    CircuitBreakerState,
    evaluate_circuit_breaker,
)
from core.settings.circuit_breaker_config import CircuitBreakerConfig
from core.settings.risk_config import RiskConfig, ProfileRiskConfig


NOW = datetime(2026, 9, 19, 18, 0, tzinfo=timezone.utc)
CONFIG = CircuitBreakerConfig(True, True, True, True, True, True, True)
RISK = RiskConfig(
    enabled=True,
    max_daily_loss_pct=3,
    max_weekly_loss_pct=6,
    pip_value_per_lot=10,
    contract_size=100000,
    min_lot=Decimal("0.01"),
    max_lot=Decimal("100"),
    lot_step=Decimal("0.01"),
    profiles=(ProfileRiskConfig("scalper", 0.5, 3, 10, 2),),
)


def _account(daily=Decimal("0"), weekly=Decimal("0")):
    return AccountState(
        balance=Decimal("2000"),
        equity=Decimal("2000"),
        daily_pnl=daily,
        weekly_pnl=weekly,
        open_trade_count=0,
        trades_today=0,
        open_symbols=(),
        currency="USD",
        snapshot_at=NOW,
        daily_pnl_available=True,
        weekly_pnl_available=True,
    )


def test_circuit_breaker_allows_verified_normal_account():
    result = evaluate_circuit_breaker(
        CONFIG,
        mt5_available=True,
        trade_allowed=True,
        trade_expert=True,
        manual_paused=False,
        storage_available=True,
        account_mode=TradingAccountMode.NORMAL_ACCOUNT,
        account_status=AccountModeStatus.NOT_REQUIRED,
        prop_firm_context_ready=False,
        account=_account(),
        risk_config=RISK,
        loss_budget=None,
        reconciliation_status="MATCHED",
        now=NOW,
    )
    assert result.allowed is True
    assert result.state is CircuitBreakerState.ARMED


def test_circuit_breaker_blocks_unknown_auto_enforced_context():
    result = evaluate_circuit_breaker(
        CONFIG,
        mt5_available=True,
        trade_allowed=True,
        trade_expert=True,
        manual_paused=False,
        storage_available=True,
        account_mode=TradingAccountMode.AUTO_ENFORCED,
        account_status=AccountModeStatus.UNKNOWN,
        prop_firm_context_ready=False,
        account=_account(),
        risk_config=RISK,
        loss_budget=None,
        reconciliation_status="MATCHED",
        now=NOW,
    )
    assert result.allowed is False
    assert result.reason_code == "PROP_FIRM_CONTEXT_UNKNOWN"


def test_circuit_breaker_keeps_shadow_mode_non_blocking():
    result = evaluate_circuit_breaker(
        CONFIG,
        mt5_available=True,
        trade_allowed=True,
        trade_expert=True,
        manual_paused=False,
        storage_available=True,
        account_mode=TradingAccountMode.PROP_FIRM_SHADOW,
        account_status=AccountModeStatus.UNKNOWN,
        prop_firm_context_ready=False,
        account=_account(),
        risk_config=RISK,
        loss_budget=None,
        reconciliation_status="MATCHED",
        now=NOW,
    )
    assert result.allowed is True
    assert result.state is CircuitBreakerState.ARMED


def test_circuit_breaker_uses_existing_daily_loss_limit():
    result = evaluate_circuit_breaker(
        CONFIG,
        mt5_available=True,
        trade_allowed=True,
        trade_expert=True,
        manual_paused=False,
        storage_available=True,
        account_mode=TradingAccountMode.NORMAL_ACCOUNT,
        account_status=AccountModeStatus.NOT_REQUIRED,
        prop_firm_context_ready=False,
        account=_account(daily=Decimal("-60")),
        risk_config=RISK,
        loss_budget=None,
        reconciliation_status="MATCHED",
        now=NOW,
    )
    assert result.allowed is False
    assert result.reason_code == "DAILY_LOSS_HIT"


def test_circuit_breaker_blocks_unavailable_daily_pnl():
    account = replace(_account(), daily_pnl_available=False)
    result = evaluate_circuit_breaker(
        CONFIG,
        mt5_available=True,
        trade_allowed=True,
        trade_expert=True,
        manual_paused=False,
        storage_available=True,
        account_mode=TradingAccountMode.NORMAL_ACCOUNT,
        account_status=AccountModeStatus.NOT_REQUIRED,
        prop_firm_context_ready=False,
        account=account,
        risk_config=RISK,
        loss_budget=None,
        reconciliation_status="MATCHED",
        now=NOW,
    )
    assert result.allowed is False
    assert result.state is CircuitBreakerState.UNKNOWN
    assert result.reason_code == "DAILY_PNL_UNAVAILABLE"


def test_circuit_breaker_blocks_unavailable_weekly_pnl():
    account = replace(_account(), weekly_pnl_available=False)
    result = evaluate_circuit_breaker(
        CONFIG,
        mt5_available=True,
        trade_allowed=True,
        trade_expert=True,
        manual_paused=False,
        storage_available=True,
        account_mode=TradingAccountMode.NORMAL_ACCOUNT,
        account_status=AccountModeStatus.NOT_REQUIRED,
        prop_firm_context_ready=False,
        account=account,
        risk_config=RISK,
        loss_budget=None,
        reconciliation_status="MATCHED",
        now=NOW,
    )
    assert result.allowed is False
    assert result.state is CircuitBreakerState.UNKNOWN
    assert result.reason_code == "WEEKLY_PNL_UNAVAILABLE"


def test_circuit_breaker_blocks_unknown_reconciliation():
    result = evaluate_circuit_breaker(
        CONFIG,
        mt5_available=True,
        trade_allowed=True,
        trade_expert=True,
        manual_paused=False,
        storage_available=True,
        account_mode=TradingAccountMode.NORMAL_ACCOUNT,
        account_status=AccountModeStatus.NOT_REQUIRED,
        prop_firm_context_ready=False,
        account=_account(),
        risk_config=RISK,
        loss_budget=None,
        reconciliation_status="UNKNOWN",
        now=NOW,
    )
    assert result.allowed is False
    assert result.reason_code == "RECONCILIATION_UNKNOWN"


def test_circuit_breaker_persists_only_state_transitions():
    class Store:
        def __init__(self):
            self.values = {}
            self.writes = 0

        def get(self, key, default=None):
            return self.values.get(key, default)

        def set(self, key, value):
            self.writes += 1
            self.values[key] = value

    store = Store()
    breaker = CircuitBreaker(store, CONFIG)
    kwargs = dict(
        mt5_available=True,
        trade_allowed=False,
        trade_expert=True,
        manual_paused=False,
        storage_available=True,
        account_mode=TradingAccountMode.NORMAL_ACCOUNT,
        account_status=AccountModeStatus.NOT_REQUIRED,
        prop_firm_context_ready=False,
        account=_account(),
        risk_config=RISK,
        loss_budget=None,
        reconciliation_status="MATCHED",
        now=NOW,
    )
    first = breaker.evaluate(**kwargs)
    second = breaker.evaluate(**kwargs)
    assert first.transitioned is True
    assert second.transitioned is False
    assert store.writes == 1
