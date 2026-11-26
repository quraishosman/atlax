"""
Risk Engine Unit Tests.

Authority: docs/10_RISK_ENGINE.md
           docs/13_CONFIGURATION.md (fail-closed config)

Coverage:
    - RiskConfig / ProfileRiskConfig validation (fail-closed)
    - AccountState validation
    - RiskEvaluation invariants
    - Lot-size arithmetic (exact Decimal precision)
    - Daily loss limit gate
    - Weekly loss limit gate
    - Max open trades gate (global + profile)
    - Max daily trades gate
    - Correlated pairs gate
    - Disabled engine gate
    - Sub-minimum lot rejection
    - Zero SL rejection
    - Profile not found rejection
"""

import pytest
import dataclasses
from datetime import datetime, timezone
from decimal import Decimal

from core.models.candle import Candle
from core.models.prop_firm import (
    AccountReferenceBasis,
    LossLimitMode,
    LossLimitSpec,
    PropFirmAccountSnapshot,
    PropFirmRuleProfile,
)
from core.models.risk import AccountState, RiskCalculation, RiskEvaluation
from core.models.trade_candidate import (
    Direction, EntryModel, InvalidationModel, TargetModel, TradeCandidate,
)
from core.risk.risk_engine import (
    REJECTED_ADAPTIVE_LOT_EXCEEDS_RISK,
    REJECTED_BROKER_METADATA,
    REJECTED_CONFIG_ERROR,
    REJECTED_DAILY_LOSS,
    REJECTED_DAILY_PNL_UNAVAILABLE,
    REJECTED_DAILY_TRADE_COUNT_UNAVAILABLE,
    REJECTED_DISABLED,
    REJECTED_INVALID_SL,
    REJECTED_LOT_BELOW_MIN,
    REJECTED_MAX_CORRELATED,
    REJECTED_MAX_DAILY_TRADES,
    REJECTED_MAX_OPEN_TRADES,
    REJECTED_PROFILE_NOT_FOUND,
    REJECTED_PROP_FIRM_BUDGET,
    REJECTED_WEEKLY_LOSS,
    REJECTED_WEEKLY_PNL_UNAVAILABLE,
    RiskEngine,
)
from core.risk.loss_budget import PropFirmRiskContext
from core.settings.risk_config import ProfileRiskConfig, RiskConfig, load_risk_config


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def risk_config() -> RiskConfig:
    configured = load_risk_config("config/atlax.yaml")
    return dataclasses.replace(
        configured,
        adaptive_lot_sizing=dataclasses.replace(
            configured.adaptive_lot_sizing,
            enabled=False,
        ),
    )


@pytest.fixture
def engine() -> RiskEngine:
    return RiskEngine()


@pytest.fixture
def healthy_account() -> AccountState:
    """A clean account — no losses, no open trades."""
    return AccountState(
        balance=Decimal("10000"),
        equity=Decimal("10000"),
        daily_pnl=Decimal("0"),
        weekly_pnl=Decimal("0"),
        open_trade_count=0,
        trades_today=0,
        open_symbols=(),
        currency="USD",
        snapshot_at=datetime(2026, 7, 21, 10, 0, tzinfo=timezone.utc),
        daily_trade_count_available=True,
        daily_pnl_available=True,
        weekly_pnl_available=True,
    )


def _make_candidate(
    symbol: str = "EURUSD",
    timeframe: str = "H4",
    profile: str = "swing_trader",
    entry_ref: str = "1.10000",
    sl_level: str = "1.09000",   # 100 pip stop
    pip_size: str | None = None,
) -> TradeCandidate:
    now = datetime(2026, 7, 21, 10, 0, tzinfo=timezone.utc)
    return TradeCandidate(
        candidate_id="test-risk-001",
        strategy_name="CRTStrategy",
        source_detector_event_ids=("det-001",),
        symbol=symbol,
        timeframe=timeframe,
        profile=profile,
        direction=Direction.BUY,
        entry_model=EntryModel(
            entry_zone_high=Decimal(entry_ref),
            entry_zone_low=Decimal("1.08900"),
            reference_price=Decimal(entry_ref),
            description="test",
        ),
        invalidation_model=InvalidationModel(
            invalidation_level=Decimal(sl_level),
            description="test SL",
        ),
        target_model=TargetModel(
            midpoint_target=Decimal("1.105"),
            opposite_extreme_target=Decimal("1.110"),
            description="test TP",
        ),
        confidence_inputs={"pip_size": pip_size} if pip_size is not None else {},
        explanation="test",
        created_at=now,
    )


# ---------------------------------------------------------------------------
# CONFIG VALIDATION TESTS
# ---------------------------------------------------------------------------

class TestRiskConfigValidation:
    """Fail-closed config validation — docs/10_RISK_ENGINE.md + docs/13_CONFIGURATION.md"""

    def test_load_from_yaml(self, risk_config):
        """Config loads with correct values from atlax.yaml."""
        assert risk_config.enabled is True
        assert risk_config.max_daily_loss_pct == 3.0
        assert risk_config.max_weekly_loss_pct == 6.0
        assert risk_config.min_lot == 0.01
        assert risk_config.lot_step == 0.01
        assert len(risk_config.profiles) == 3
        assert risk_config.adaptive_lot_sizing.enabled is False

    def test_live_config_enables_non_gold_daily_loss_ladder(self):
        configured = load_risk_config("config/atlax.yaml")

        ladder = configured.adaptive_lot_sizing
        assert ladder.enabled is True
        assert ladder.excluded_symbol_prefixes == ("XAU",)
        assert ladder.normal_lot == 1.0
        assert ladder.caution_daily_loss_pct == 1.0
        assert ladder.caution_lot == 0.8
        assert ladder.recovery_daily_loss_pct == 2.0
        assert ladder.recovery_lot == 0.5


class TestBrokerRiskMetadata:
    def test_broker_tick_economics_override_global_pip_fallback(
        self, engine, risk_config, healthy_account
    ):
        account = dataclasses.replace(
            healthy_account,
            symbol_tick_value=Decimal("0.5"),
            symbol_tick_size=Decimal("0.00001"),
            symbol_volume_min=Decimal("0.01"),
            symbol_volume_max=Decimal("50"),
            symbol_volume_step=Decimal("0.01"),
        )

        result = engine.evaluate(_make_candidate(), risk_config, account)

        assert result.approved is True
        # 1.5% of 10,000 = 150; $50,000 per price unit × .01 stop = $500/lot.
        assert result.lot_size == Decimal("0.30")

    @pytest.mark.parametrize(
        ("symbol", "entry", "sl", "pip_size", "tick_value", "tick_size", "expected_lot"),
        [
            ("USDJPY", "150.000", "149.900", "0.01", "0.67", "0.001", "2.23"),
            ("XAUUSD", "2400.00", "2399.00", "0.01", "1.00", "0.01", "1.50"),
        ],
    )
    def test_non_forex_tick_economics_are_used(
        self,
        engine,
        risk_config,
        healthy_account,
        symbol,
        entry,
        sl,
        pip_size,
        tick_value,
        tick_size,
        expected_lot,
    ):
        account = dataclasses.replace(
            healthy_account,
            symbol_tick_value=Decimal(tick_value),
            symbol_tick_size=Decimal(tick_size),
            symbol_volume_min=Decimal("0.01"),
            symbol_volume_max=Decimal("50"),
            symbol_volume_step=Decimal("0.01"),
            broker_metadata_required=True,
        )

        result = engine.evaluate(
            _make_candidate(
                symbol=symbol,
                entry_ref=entry,
                sl_level=sl,
                pip_size=pip_size,
            ),
            risk_config,
            account,
        )

        assert result.approved is True
        assert result.lot_size == Decimal(expected_lot)

    def test_live_snapshot_missing_broker_metadata_rejects(
        self, engine, risk_config, healthy_account
    ):
        account = dataclasses.replace(healthy_account, broker_metadata_required=True)

        result = engine.evaluate(_make_candidate(), risk_config, account)

        assert result.approved is False
        assert result.rejection_code == REJECTED_BROKER_METADATA

    def test_live_snapshot_partial_broker_metadata_rejects(
        self, engine, risk_config, healthy_account
    ):
        account = dataclasses.replace(
            healthy_account,
            symbol_tick_value=Decimal("0.5"),
            symbol_tick_size=Decimal("0.00001"),
            symbol_volume_min=Decimal("0.01"),
            symbol_volume_max=Decimal("50"),
            broker_metadata_required=True,
        )

        result = engine.evaluate(_make_candidate(), risk_config, account)

        assert result.approved is False
        assert result.rejection_code == REJECTED_BROKER_METADATA

    def test_all_profiles_present(self, risk_config):
        """All three profiles must be configured."""
        names = {p.profile_name for p in risk_config.profiles}
        assert names == {"scalper", "day_trader", "swing_trader"}

    def test_weekly_loss_less_than_daily_rejects(self):
        """weekly < daily must fail closed."""
        with pytest.raises(ValueError, match="max_weekly_loss_pct"):
            RiskConfig(
                enabled=True,
                max_daily_loss_pct=5.0,
                max_weekly_loss_pct=3.0,   # invalid: < daily
                pip_value_per_lot=10.0,
                contract_size=100000.0,
                min_lot=0.01, max_lot=100.0, lot_step=0.01,
                profiles=(ProfileRiskConfig("swing_trader", 1.0, 5, 3, 3),),
            )

    def test_zero_risk_per_trade_rejects(self):
        with pytest.raises(ValueError, match="risk_per_trade_pct"):
            ProfileRiskConfig("swing_trader", 0.0, 5, 3, 3)

    def test_risk_above_10_pct_rejects(self):
        with pytest.raises(ValueError, match="risk_per_trade_pct"):
            ProfileRiskConfig("swing_trader", 11.0, 5, 3, 3)

    def test_missing_risk_section_raises(self, tmp_path):
        """Missing risk: section must fail closed."""
        cfg = tmp_path / "bad.yaml"
        cfg.write_text("crt:\n  enabled: true\n")
        with pytest.raises(ValueError, match="risk"):
            load_risk_config(str(cfg))


# ---------------------------------------------------------------------------
# ACCOUNT STATE TESTS
# ---------------------------------------------------------------------------

class TestAccountStateValidation:
    def test_zero_balance_rejects(self):
        with pytest.raises(ValueError, match="balance"):
            AccountState(
                balance=Decimal("0"), equity=Decimal("10000"),
                daily_pnl=Decimal("0"), weekly_pnl=Decimal("0"),
                open_trade_count=0, trades_today=0, open_symbols=(),
                currency="USD", snapshot_at=datetime.now(timezone.utc),
            )

    def test_invalid_currency_rejects(self):
        with pytest.raises(ValueError, match="currency"):
            AccountState(
                balance=Decimal("10000"), equity=Decimal("10000"),
                daily_pnl=Decimal("0"), weekly_pnl=Decimal("0"),
                open_trade_count=0, trades_today=0, open_symbols=(),
                currency="US",  # Only 2 chars — invalid
                snapshot_at=datetime.now(timezone.utc),
            )


# ---------------------------------------------------------------------------
# RISK EVALUATION INVARIANT TESTS
# ---------------------------------------------------------------------------

class TestRiskEvaluationInvariants:
    def test_approved_without_lot_size_raises(self):
        with pytest.raises(ValueError, match="lot_size"):
            RiskEvaluation(
                candidate_id="x", approved=True, lot_size=None,
                calculation=None, rejection_code=None,
                reason="approved", evaluated_at=datetime.now(timezone.utc),
            )

    def test_rejected_with_lot_size_raises(self):
        with pytest.raises(ValueError, match="lot_size"):
            RiskEvaluation(
                candidate_id="x", approved=False, lot_size=Decimal("0.01"),
                calculation=None, rejection_code="SOME_CODE",
                reason="rejected", evaluated_at=datetime.now(timezone.utc),
            )

    def test_empty_reason_raises(self):
        with pytest.raises(ValueError, match="reason"):
            RiskEvaluation(
                candidate_id="x", approved=False, lot_size=None,
                calculation=None, rejection_code="CODE",
                reason="",  # empty — invalid
                evaluated_at=datetime.now(timezone.utc),
            )


class TestPropFirmRiskIntegration:
    def _context(self, *, equity=Decimal("10000")):
        now = datetime.now(timezone.utc)
        profile = PropFirmRuleProfile(
            profile_id="example-funded-usd",
            firm_name="Example Firm",
            program_name="Example Program",
            phase="FUNDED",
            account_type="STANDARD",
            currency="USD",
            nominal_balance=Decimal("10000"),
            overall_loss=LossLimitSpec(
                mode=LossLimitMode.STATIC,
                basis=AccountReferenceBasis.INITIAL_BALANCE,
                percentage=Decimal("8"),
            ),
            daily_loss=LossLimitSpec(
                mode=LossLimitMode.STATIC,
                basis=AccountReferenceBasis.OPENING_EQUITY,
                percentage=Decimal("3"),
                reset_timezone="UTC",
            ),
            source_urls=("https://example.test/rules",),
            source_retrieved_at=now,
            version="2026-09-19.1",
            content_hash="sha256:example",
        )
        snapshot = PropFirmAccountSnapshot(
            profile_id=profile.profile_id,
            account_type=profile.account_type,
            currency=profile.currency,
            balance=equity,
            equity=equity,
            snapshot_at=now,
            initial_balance=Decimal("10000"),
            opening_balance=Decimal("10000"),
            opening_equity=Decimal("10000"),
            high_watermark_balance=Decimal("10000"),
            high_watermark_equity=Decimal("10000"),
        )
        return PropFirmRiskContext(
            profile=profile,
            snapshot=snapshot,
            open_risk=Decimal("0"),
            max_open_risk_fraction=Decimal("0.5"),
            new_trade_buffer_fraction=Decimal("0.5"),
            cost_reserve=Decimal("0"),
            max_profile_age_hours=24,
        )

    def test_prop_firm_budget_caps_risk_before_lot_sizing(
        self, engine, risk_config, healthy_account
    ):
        account = dataclasses.replace(
            healthy_account,
            symbol_tick_value=Decimal("0.5"),
            symbol_tick_size=Decimal("0.00001"),
            symbol_volume_min=Decimal("0.01"),
            symbol_volume_max=Decimal("50"),
            symbol_volume_step=Decimal("0.01"),
            broker_metadata_required=True,
        )

        result = engine.evaluate(
            _make_candidate(),
            risk_config,
            account,
            prop_firm_context=self._context(),
        )

        assert result.approved is True
        assert result.loss_budget is not None
        assert result.loss_budget.allowed_new_trade_risk == Decimal("75.00")
        assert result.calculation.risk_amount == Decimal("75.00")
        assert result.lot_size == Decimal("0.15")

    def test_exhausted_prop_firm_daily_buffer_rejects_before_sizing(
        self, engine, risk_config, healthy_account
    ):
        account = dataclasses.replace(
            healthy_account,
            balance=Decimal("9700"),
            equity=Decimal("9700"),
            symbol_tick_value=Decimal("0.5"),
            symbol_tick_size=Decimal("0.00001"),
            symbol_volume_min=Decimal("0.01"),
            symbol_volume_max=Decimal("50"),
            symbol_volume_step=Decimal("0.01"),
            broker_metadata_required=True,
        )

        result = engine.evaluate(
            _make_candidate(),
            risk_config,
            account,
            prop_firm_context=self._context(equity=Decimal("9700")),
        )

        assert result.approved is False
        assert result.rejection_code == REJECTED_PROP_FIRM_BUDGET
        assert result.loss_budget is not None
        assert result.loss_budget.blocked is True


# ---------------------------------------------------------------------------
# ADAPTIVE LOT TARGET TESTS
# ---------------------------------------------------------------------------

class TestAdaptiveLotTargets:
    def _live_ladder_config(self) -> RiskConfig:
        return load_risk_config("config/atlax.yaml")

    def _safe_candidate(self, symbol: str = "EURUSD") -> TradeCandidate:
        # A 10-pip SL on the standard fixture yields a 1.50 risk-safe lot.
        return _make_candidate(symbol=symbol, sl_level="1.09900")

    def test_normal_non_gold_trade_uses_one_lot_target(self, engine, healthy_account):
        result = engine.evaluate(
            self._safe_candidate(), self._live_ladder_config(), healthy_account
        )

        assert result.approved is True
        assert result.lot_size == Decimal("1.0")

    def test_one_percent_daily_loss_reduces_non_gold_target_to_point_eight(
        self, engine, healthy_account
    ):
        account = dataclasses.replace(healthy_account, daily_pnl=Decimal("-100"))

        result = engine.evaluate(
            self._safe_candidate(), self._live_ladder_config(), account
        )

        assert result.approved is True
        assert result.lot_size == Decimal("0.8")

    def test_two_percent_daily_loss_reduces_non_gold_target_to_point_five(
        self, engine, healthy_account
    ):
        account = dataclasses.replace(healthy_account, daily_pnl=Decimal("-200"))

        result = engine.evaluate(
            self._safe_candidate(), self._live_ladder_config(), account
        )

        assert result.approved is True
        assert result.lot_size == Decimal("0.5")

    def test_gold_keeps_ordinary_risk_based_sizing(self, engine, healthy_account):
        result = engine.evaluate(
            self._safe_candidate(symbol="XAUUSD"),
            self._live_ladder_config(),
            healthy_account,
        )

        assert result.approved is True
        assert result.lot_size == Decimal("1.5")

    def test_target_that_exceeds_risk_safe_lot_is_blocked(
        self, engine, healthy_account
    ):
        result = engine.evaluate(
            _make_candidate(), self._live_ladder_config(), healthy_account
        )

        assert result.approved is False
        assert result.rejection_code == REJECTED_ADAPTIVE_LOT_EXCEEDS_RISK


# ---------------------------------------------------------------------------
# LOT SIZE CALCULATION TESTS
# ---------------------------------------------------------------------------

class TestLotSizeCalculation:
    """
    Lot-size formula:
        risk_amount = equity * (risk_per_trade_pct / 100)
        sl_pips     = sl_distance_price / pip_size (0.0001)
        lot_size    = risk_amount / (sl_pips * pip_value_per_lot)

    Fixture values:
        equity               = 10 000 USD
        risk_per_trade_pct   = 1.5% (swing_trader)
        risk_amount          = 10000 * 0.015 = 150 USD
        entry_ref            = 1.10000
        sl_level             = 1.09000
        sl_distance_price    = 0.01000
        sl_pips              = 0.01 / 0.0001 = 100 pips
        pip_value_per_lot    = 10.0 USD
        raw_lot              = 150 / (100 * 10) = 0.15
        lot_step             = 0.01
        lot_size             = 0.15 (exact, no rounding needed)
    """

    def test_lot_size_exact_math(self, engine, risk_config, healthy_account):
        candidate = _make_candidate()
        result = engine.evaluate(candidate, risk_config, healthy_account)

        assert result.approved is True
        assert result.lot_size == Decimal("0.15")
        assert result.rejection_code is None

    def test_risk_amount_is_correct(self, engine, risk_config, healthy_account):
        candidate = _make_candidate()
        result = engine.evaluate(candidate, risk_config, healthy_account)

        assert result.calculation is not None
        assert result.calculation.risk_amount == Decimal("150.00")

    def test_sl_pips_is_correct(self, engine, risk_config, healthy_account):
        candidate = _make_candidate()
        result = engine.evaluate(candidate, risk_config, healthy_account)

        assert result.calculation.sl_distance_pips == Decimal("100")

    def test_lot_rounds_down_to_step(self, engine, risk_config, healthy_account):
        """A fractional lot should be rounded DOWN — never exceed risk."""
        # equity=10000, risk=1.5%, sl=67 pips → raw=150/(67*10)=0.22388...
        # lot_step=0.01 → should round DOWN to 0.22, not 0.23
        candidate = _make_candidate(sl_level="1.09330")  # 67 pips
        result = engine.evaluate(candidate, risk_config, healthy_account)

        assert result.approved is True
        # 150 / (67 * 10) = 0.22388...  → rounded DOWN to 0.22
        assert result.lot_size == Decimal("0.22")

    def test_lot_capped_at_max_lot(self, engine, healthy_account):
        """Lot must never exceed max_lot even with tiny SL."""
        tiny_sl_config = RiskConfig(
            enabled=True,
            max_daily_loss_pct=3.0, max_weekly_loss_pct=6.0,
            pip_value_per_lot=10.0, contract_size=100000.0,
            min_lot=0.01, max_lot=0.05, lot_step=0.01,  # max_lot = 0.05
            profiles=(ProfileRiskConfig("swing_trader", 1.5, 5, 3, 3),),
        )
        # sl = 1 pip → raw_lot = 150/(1*10) = 15 → capped at max_lot=0.05
        candidate = _make_candidate(sl_level="1.09990")
        result = engine.evaluate(candidate, tiny_sl_config, healthy_account)
        assert result.lot_size == Decimal("0.05")

    def test_scalper_risk_per_trade(self, engine, risk_config, healthy_account):
        """Scalper has 0.5% risk — should compute 50 USD risk on 10000 equity."""
        candidate = _make_candidate(profile="scalper", timeframe="M15")
        result = engine.evaluate(candidate, risk_config, healthy_account)

        assert result.approved is True
        assert result.calculation.risk_amount == Decimal("50.00")
        # 50 / (100 pips * 10) = 0.05
        assert result.lot_size == Decimal("0.05")


# ---------------------------------------------------------------------------
# GATE: DAILY LOSS LIMIT
# ---------------------------------------------------------------------------

class TestDailyLossGate:
    def test_unavailable_daily_pnl_rejects(self, engine, risk_config, healthy_account):
        account = dataclasses.replace(healthy_account, daily_pnl_available=False)
        result = engine.evaluate(_make_candidate(), risk_config, account)
        assert result.approved is False
        assert result.rejection_code == REJECTED_DAILY_PNL_UNAVAILABLE

    def test_at_daily_loss_limit_rejects(self, engine, risk_config):
        """Exactly at the daily loss limit — must reject."""
        # 3% of 10000 = 300 USD daily loss limit
        at_limit_account = AccountState(
            balance=Decimal("10000"), equity=Decimal("9700"),
            daily_pnl=Decimal("-300"),   # exactly at limit
            weekly_pnl=Decimal("-300"),
            open_trade_count=0, trades_today=0, open_symbols=(),
            currency="USD", snapshot_at=datetime.now(timezone.utc),
            daily_trade_count_available=True,
            daily_pnl_available=True,
            weekly_pnl_available=True,
        )
        result = engine.evaluate(_make_candidate(), risk_config, at_limit_account)
        assert result.approved is False
        assert result.rejection_code == REJECTED_DAILY_LOSS

    def test_under_daily_limit_passes(self, engine, risk_config):
        """Just under the daily loss limit — must pass."""
        under_limit = AccountState(
            balance=Decimal("10000"), equity=Decimal("9701"),
            daily_pnl=Decimal("-299"),   # 1 USD under limit
            weekly_pnl=Decimal("-299"),
            open_trade_count=0, trades_today=0, open_symbols=(),
            currency="USD", snapshot_at=datetime.now(timezone.utc),
            daily_trade_count_available=True,
            daily_pnl_available=True,
            weekly_pnl_available=True,
        )
        result = engine.evaluate(_make_candidate(), risk_config, under_limit)
        assert result.approved is True


# ---------------------------------------------------------------------------
# GATE: WEEKLY LOSS LIMIT
# ---------------------------------------------------------------------------

class TestWeeklyLossGate:
    def test_unavailable_weekly_pnl_rejects(self, engine, risk_config, healthy_account):
        account = dataclasses.replace(healthy_account, weekly_pnl_available=False)
        result = engine.evaluate(_make_candidate(), risk_config, account)
        assert result.approved is False
        assert result.rejection_code == REJECTED_WEEKLY_PNL_UNAVAILABLE

    def test_at_weekly_loss_limit_rejects(self, engine, risk_config):
        """6% weekly loss limit hit — must reject."""
        # 6% of 10000 = 600 USD
        account = AccountState(
            balance=Decimal("10000"), equity=Decimal("9400"),
            daily_pnl=Decimal("-100"),
            weekly_pnl=Decimal("-600"),  # at limit
            open_trade_count=0, trades_today=0, open_symbols=(),
            currency="USD", snapshot_at=datetime.now(timezone.utc),
            daily_trade_count_available=True,
            daily_pnl_available=True,
            weekly_pnl_available=True,
        )
        result = engine.evaluate(_make_candidate(), risk_config, account)
        assert result.approved is False
        assert result.rejection_code == REJECTED_WEEKLY_LOSS

    def test_daily_ok_but_weekly_hit_rejects(self, engine, risk_config):
        """Daily is fine but weekly breached — still rejects."""
        account = AccountState(
            balance=Decimal("10000"), equity=Decimal("9400"),
            daily_pnl=Decimal("50"),     # Today is profitable
            weekly_pnl=Decimal("-601"),  # But week is blown
            open_trade_count=0, trades_today=0, open_symbols=(),
            currency="USD", snapshot_at=datetime.now(timezone.utc),
            daily_trade_count_available=True,
            daily_pnl_available=True,
            weekly_pnl_available=True,
        )
        result = engine.evaluate(_make_candidate(), risk_config, account)
        assert result.approved is False
        assert result.rejection_code == REJECTED_WEEKLY_LOSS


# ---------------------------------------------------------------------------
# GATE: MAX OPEN TRADES
# ---------------------------------------------------------------------------

class TestMaxOpenTradesGate:
    def test_at_profile_max_open_trades_rejects(self, engine, risk_config, healthy_account):
        """swing_trader max_open_trades=5 — exactly 5 open should reject."""
        full_account = dataclasses.replace(healthy_account, open_trade_count=5)
        result = engine.evaluate(_make_candidate(), risk_config, full_account)
        assert result.approved is False
        assert result.rejection_code == REJECTED_MAX_OPEN_TRADES

    def test_one_below_max_passes(self, engine, risk_config, healthy_account):
        """4 open trades with limit=5 — should pass."""
        account = dataclasses.replace(healthy_account, open_trade_count=4)
        result = engine.evaluate(_make_candidate(), risk_config, account)
        assert result.approved is True


# ---------------------------------------------------------------------------
# GATE: MAX DAILY TRADES
# ---------------------------------------------------------------------------

class TestMaxDailyTradesGate:
    def test_unavailable_daily_trade_count_rejects(self, engine, risk_config, healthy_account):
        account = dataclasses.replace(healthy_account, daily_trade_count_available=False)
        result = engine.evaluate(_make_candidate(), risk_config, account)
        assert result.approved is False
        assert result.rejection_code == REJECTED_DAILY_TRADE_COUNT_UNAVAILABLE

    def test_at_daily_trade_limit_rejects(self, engine, risk_config, healthy_account):
        """swing_trader max_trades_per_day=3 — exactly 3 today should reject."""
        account = dataclasses.replace(healthy_account, trades_today=3)
        result = engine.evaluate(_make_candidate(), risk_config, account)
        assert result.approved is False
        assert result.rejection_code == REJECTED_MAX_DAILY_TRADES

    def test_two_trades_today_passes(self, engine, risk_config, healthy_account):
        account = dataclasses.replace(healthy_account, trades_today=2)
        result = engine.evaluate(_make_candidate(), risk_config, account)
        assert result.approved is True


# ---------------------------------------------------------------------------
# GATE: CORRELATED PAIRS
# ---------------------------------------------------------------------------

class TestCorrelatedPairsGate:
    def test_at_correlation_limit_rejects(self, engine, risk_config, healthy_account):
        """swing_trader max_correlated_pairs=3. 3 open EUR pairs — should reject new EUR pair."""
        account = dataclasses.replace(
            healthy_account,
            open_trade_count=3,
            open_symbols=("EURUSD", "EURGBP", "EURJPY"),  # 3 EUR pairs
        )
        candidate = _make_candidate(symbol="EURCAD")  # 4th EUR pair
        result = engine.evaluate(candidate, risk_config, account)
        assert result.approved is False
        assert result.rejection_code == REJECTED_MAX_CORRELATED

    def test_uncorrelated_symbol_passes(self, engine, risk_config, healthy_account):
        """Open EUR pairs don't block a GBP/JPY trade."""
        account = dataclasses.replace(
            healthy_account,
            open_trade_count=2,
            open_symbols=("EURUSD", "EURGBP"),
        )
        candidate = _make_candidate(symbol="GBPJPY")
        result = engine.evaluate(candidate, risk_config, account)
        # GBPJPY shares GBP with EURGBP (quote=GBP)... but NOT EUR.
        # swing_trader limit=3, so count is 1 (EURGBP shares GBP with GBPJPY)
        # 1 < 3, so should pass
        assert result.approved is True

    def test_shared_quote_counted_as_correlated(self, engine, risk_config, healthy_account):
        """Quote currency match also counts as correlation."""
        account = dataclasses.replace(
            healthy_account,
            open_symbols=("EURUSD", "GBPUSD", "AUDUSD"),  # All USD quote
        )
        # NZDUSD has USD as quote — should be correlated with all 3
        candidate = _make_candidate(symbol="NZDUSD")
        # correlated_count = 3, limit = 3 → reject
        result = engine.evaluate(candidate, risk_config, account)
        assert result.approved is False
        assert result.rejection_code == REJECTED_MAX_CORRELATED


# ---------------------------------------------------------------------------
# GATE: DISABLED ENGINE
# ---------------------------------------------------------------------------

class TestDisabledGate:
    def test_disabled_engine_rejects_all(self, engine, risk_config, healthy_account):
        disabled_config = dataclasses.replace(risk_config, enabled=False)
        result = engine.evaluate(_make_candidate(), disabled_config, healthy_account)
        assert result.approved is False
        assert result.rejection_code == REJECTED_DISABLED


# ---------------------------------------------------------------------------
# GATE: INVALID SL
# ---------------------------------------------------------------------------

class TestInvalidSLGate:
    def test_zero_sl_distance_rejects(self, engine, risk_config, healthy_account):
        """Entry and SL at same price — zero distance must reject."""
        candidate = _make_candidate(entry_ref="1.10000", sl_level="1.10000")
        result = engine.evaluate(candidate, risk_config, healthy_account)
        assert result.approved is False
        assert result.rejection_code == REJECTED_INVALID_SL


# ---------------------------------------------------------------------------
# GATE: LOT BELOW MINIMUM
# ---------------------------------------------------------------------------

class TestLotBelowMinGate:
    def test_tiny_equity_large_sl_rejects(self, engine, risk_config):
        """Very small account + very wide SL = lot below min_lot."""
        tiny_account = AccountState(
            balance=Decimal("100"), equity=Decimal("100"),
            daily_pnl=Decimal("0"), weekly_pnl=Decimal("0"),
            open_trade_count=0, trades_today=0, open_symbols=(),
            currency="USD", snapshot_at=datetime.now(timezone.utc),
            daily_trade_count_available=True,
            daily_pnl_available=True,
            weekly_pnl_available=True,
        )
        # risk = 100 * 0.015 = 1.50 USD
        # sl = 1000 pips
        # lot = 1.50 / (1000 * 10) = 0.00015 → below min_lot 0.01
        candidate = _make_candidate(sl_level="1.00000")  # 1000 pips
        result = engine.evaluate(candidate, risk_config, tiny_account)
        assert result.approved is False
        assert result.rejection_code == REJECTED_LOT_BELOW_MIN


# ---------------------------------------------------------------------------
# GATE: PROFILE NOT FOUND
# ---------------------------------------------------------------------------

class TestProfileNotFoundGate:
    def test_unknown_profile_rejects(self, engine, risk_config, healthy_account):
        """Candidate for an unconfigured profile must reject (not assume defaults)."""
        # Create a config that only has scalper, no swing_trader
        scalper_only = RiskConfig(
            enabled=True,
            max_daily_loss_pct=3.0, max_weekly_loss_pct=6.0,
            pip_value_per_lot=10.0, contract_size=100000.0,
            min_lot=0.01, max_lot=100.0, lot_step=0.01,
            profiles=(ProfileRiskConfig("scalper", 0.5, 3, 10, 2),),
        )
        candidate = _make_candidate(profile="swing_trader")
        result = engine.evaluate(candidate, scalper_only, healthy_account)
        assert result.approved is False
        assert result.rejection_code == REJECTED_PROFILE_NOT_FOUND
