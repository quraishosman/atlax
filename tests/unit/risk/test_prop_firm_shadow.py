from datetime import datetime, timezone
from decimal import Decimal

from core.models.prop_firm import (
    AccountReferenceBasis,
    LossLimitMode,
    LossLimitSpec,
    PropFirmAccountSnapshot,
    PropFirmRuleProfile,
)
from core.models.risk import AccountState
from core.models.trade_candidate import (
    Direction,
    EntryModel,
    InvalidationModel,
    TargetModel,
    TradeCandidate,
)
from core.risk.loss_budget import PropFirmRiskContext
from core.risk.prop_firm_shadow import PropFirmShadowValidator
from core.risk.risk_engine import RiskEngine
from core.settings.risk_config import load_risk_config


class MemoryStore:
    def __init__(self):
        self.values = {}

    def get(self, key, default=None):
        return self.values.get(key, default)

    def set(self, key, value):
        self.values[key] = value


def _candidate() -> TradeCandidate:
    return TradeCandidate(
        candidate_id="shadow-risk-001",
        strategy_name="CRTStrategy",
        source_detector_event_ids=("det-001",),
        symbol="EURUSD",
        timeframe="H1",
        profile="day_trader",
        direction=Direction.BUY,
        entry_model=EntryModel(
            entry_zone_high=Decimal("1.10000"),
            entry_zone_low=Decimal("1.09900"),
            reference_price=Decimal("1.10000"),
            description="test",
        ),
        invalidation_model=InvalidationModel(
            invalidation_level=Decimal("1.09000"),
            description="test",
        ),
        target_model=TargetModel(
            midpoint_target=Decimal("1.10500"),
            opposite_extreme_target=Decimal("1.11000"),
            description="test",
        ),
        confidence_inputs={},
        explanation="test",
        created_at=datetime(2026, 9, 19, tzinfo=timezone.utc),
    )


def _profile() -> PropFirmRuleProfile:
    return PropFirmRuleProfile(
        profile_id="shadow-profile",
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
        source_retrieved_at=datetime(2026, 9, 19, tzinfo=timezone.utc),
        version="1.0",
        content_hash="sha256:shadow",
        nominal_balance=Decimal("10000"),
    )


def _context(new_trade_buffer_fraction: Decimal) -> PropFirmRiskContext:
    profile = _profile()
    snapshot = PropFirmAccountSnapshot(
        profile_id=profile.profile_id,
        account_type=profile.account_type,
        currency=profile.currency,
        balance=Decimal("10000"),
        equity=Decimal("10000"),
        snapshot_at=datetime(2026, 9, 19, tzinfo=timezone.utc),
        initial_balance=Decimal("10000"),
    )
    return PropFirmRiskContext(
        profile=profile,
        snapshot=snapshot,
        open_risk=Decimal("0"),
        max_open_risk_fraction=Decimal("0.5"),
        new_trade_buffer_fraction=new_trade_buffer_fraction,
        cost_reserve=Decimal("0"),
        max_profile_age_hours=168,
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
        snapshot_at=datetime(2026, 9, 19, tzinfo=timezone.utc),
        daily_trade_count_available=True,
        daily_pnl_available=True,
        weekly_pnl_available=True,
    )


def test_shadow_resize_is_recorded_without_changing_live_evaluation():
    engine = RiskEngine()
    config = load_risk_config("config/atlax.yaml")
    candidate = _candidate()
    account = _account()
    live = engine.evaluate(candidate, config, account)
    store = MemoryStore()
    validator = PropFirmShadowValidator(
        engine,
        config,
        store,
        context_provider=lambda candidate, account: _context(Decimal("0.125")),
    )

    event = validator.observe(candidate, account, live)

    assert live.approved is True
    assert event["category"] == "would_resize"
    assert Decimal(event["shadow_lot_size"]) < Decimal(event["current_lot_size"])
    assert validator.report()["would_resize"] == 1


def test_shadow_unknown_is_recorded_and_never_becomes_a_live_block():
    engine = RiskEngine()
    config = load_risk_config("config/atlax.yaml")
    candidate = _candidate()
    account = _account()
    live = engine.evaluate(candidate, config, account)
    store = MemoryStore()
    validator = PropFirmShadowValidator(
        engine,
        config,
        store,
        context_provider=lambda candidate, account: None,
    )

    event = validator.observe(candidate, account, live)

    assert event["status"] == "UNKNOWN"
    assert validator.report()["unknown"] == 1
    assert live.approved is True


def test_shadow_validator_is_inert_without_a_context_provider():
    store = MemoryStore()
    validator = PropFirmShadowValidator(RiskEngine(), load_risk_config("config/atlax.yaml"), store)

    assert validator.observe(_candidate(), _account(), object()) is None
    assert validator.report()["status"] == "NOT_CONFIGURED"
