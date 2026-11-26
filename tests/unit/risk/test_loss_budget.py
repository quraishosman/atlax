from datetime import datetime, timedelta, timezone
from decimal import Decimal

from core.models.prop_firm import (
    AccountReferenceBasis,
    LossLimitMode,
    LossLimitSpec,
    PropFirmRecoveryState,
    PropFirmAccountSnapshot,
    PropFirmRuleProfile,
    ValidationStatus,
)
from core.risk.loss_budget import (
    PropFirmRiskContext,
    evaluate_loss_budget,
)
from core.settings.prop_firm_config import PropFirmRecoveryConfig


NOW = datetime(2026, 9, 19, 12, 0, tzinfo=timezone.utc)


def _profile(*, overall, daily=None):
    return PropFirmRuleProfile(
        profile_id="example-funded-usd",
        firm_name="Example Firm",
        program_name="Example Program",
        phase="FUNDED",
        account_type="STANDARD",
        currency="USD",
        nominal_balance=Decimal("2000"),
        overall_loss=overall,
        daily_loss=daily,
        source_urls=("https://example.test/rules",),
        source_retrieved_at=NOW - timedelta(hours=1),
        version="2026-09-19.1",
        content_hash="sha256:example",
    )


def _snapshot(**overrides):
    values = dict(
        profile_id="example-funded-usd",
        account_type="STANDARD",
        currency="USD",
        balance=Decimal("2000"),
        equity=Decimal("2000"),
        snapshot_at=NOW,
        initial_balance=Decimal("2000"),
        opening_balance=Decimal("2000"),
        opening_equity=Decimal("2000"),
        high_watermark_balance=Decimal("2000"),
        high_watermark_equity=Decimal("2000"),
    )
    values.update(overrides)
    return PropFirmAccountSnapshot(**values)


def _context(profile, snapshot, *, open_risk=Decimal("0")):
    return PropFirmRiskContext(
        profile=profile,
        snapshot=snapshot,
        open_risk=open_risk,
        max_open_risk_fraction=Decimal("0.5"),
        new_trade_buffer_fraction=Decimal("0.5"),
        cost_reserve=Decimal("0"),
        max_profile_age_hours=168,
    )


def test_daily_buffer_governs_when_smaller_than_overall_buffer():
    profile = _profile(
        overall=LossLimitSpec(
            mode=LossLimitMode.STATIC,
            basis=AccountReferenceBasis.INITIAL_BALANCE,
            percentage=Decimal("8"),
        ),
        daily=LossLimitSpec(
            mode=LossLimitMode.STATIC,
            basis=AccountReferenceBasis.OPENING_EQUITY,
            percentage=Decimal("3"),
            reset_timezone="Africa/Dar_es_Salaam",
        ),
    )

    decision = evaluate_loss_budget(_context(profile, _snapshot()), as_of=NOW)

    assert decision.status is ValidationStatus.VALID
    assert decision.overall_floor == Decimal("1840")
    assert decision.daily_floor == Decimal("1940")
    assert decision.remaining_overall_buffer == Decimal("160")
    assert decision.remaining_daily_buffer == Decimal("60")
    assert decision.max_open_risk_budget == Decimal("30.0")
    assert decision.allowed_new_trade_risk == Decimal("15.00")
    assert decision.blocked is False


def test_trailing_equity_budget_includes_open_risk_and_cost_reserve():
    profile = _profile(
        overall=LossLimitSpec(
            mode=LossLimitMode.TRAILING,
            basis=AccountReferenceBasis.HIGH_WATERMARK_EQUITY,
            percentage=Decimal("8"),
        )
    )

    context = PropFirmRiskContext(
        profile=profile,
        snapshot=_snapshot(
            equity=Decimal("2100"),
            high_watermark_equity=Decimal("2200"),
        ),
        open_risk=Decimal("10"),
        max_open_risk_fraction=Decimal("0.5"),
        new_trade_buffer_fraction=Decimal("0.5"),
        cost_reserve=Decimal("2"),
        max_profile_age_hours=168,
    )

    decision = evaluate_loss_budget(context, as_of=NOW)

    assert decision.overall_floor == Decimal("2024")
    assert decision.remaining_overall_buffer == Decimal("76")
    assert decision.max_open_risk_budget == Decimal("38.0")
    assert decision.available_budget == Decimal("26.0")
    assert decision.allowed_new_trade_risk == Decimal("13.00")


def test_exhausted_daily_buffer_blocks_new_trade():
    profile = _profile(
        overall=LossLimitSpec(
            mode=LossLimitMode.STATIC,
            basis=AccountReferenceBasis.INITIAL_BALANCE,
            percentage=Decimal("8"),
        ),
        daily=LossLimitSpec(
            mode=LossLimitMode.STATIC,
            basis=AccountReferenceBasis.OPENING_EQUITY,
            percentage=Decimal("3"),
            reset_timezone="UTC",
        ),
    )

    decision = evaluate_loss_budget(
        _context(profile, _snapshot(equity=Decimal("1940"))),
        as_of=NOW,
    )

    assert decision.blocked is True
    assert decision.allowed_new_trade_risk == Decimal("0")
    assert "exhausted" in decision.reason


def test_missing_trailing_reference_is_unknown_and_blocked():
    profile = _profile(
        overall=LossLimitSpec(
            mode=LossLimitMode.TRAILING,
            basis=AccountReferenceBasis.HIGH_WATERMARK_EQUITY,
            percentage=Decimal("8"),
        )
    )

    decision = evaluate_loss_budget(
        _context(profile, _snapshot(high_watermark_equity=None)),
        as_of=NOW,
    )

    assert decision.status is ValidationStatus.UNKNOWN
    assert decision.blocked is True
    assert decision.allowed_new_trade_risk is None


def test_budget_decision_round_trips_for_audit_storage():
    profile = _profile(
        overall=LossLimitSpec(
            mode=LossLimitMode.STATIC,
            basis=AccountReferenceBasis.INITIAL_BALANCE,
            amount=Decimal("160"),
        )
    )
    decision = evaluate_loss_budget(_context(profile, _snapshot()), as_of=NOW)

    restored = decision.from_dict(decision.to_dict())

    assert restored == decision


def test_recovery_multiplier_caps_allowed_new_trade_risk():
    profile = _profile(
        overall=LossLimitSpec(
            mode=LossLimitMode.STATIC,
            basis=AccountReferenceBasis.INITIAL_BALANCE,
            percentage=Decimal("8"),
        )
    )
    context = PropFirmRiskContext(
        profile=profile,
        snapshot=_snapshot(equity=Decimal("1950")),
        open_risk=Decimal("0"),
        max_open_risk_fraction=Decimal("0.5"),
        new_trade_buffer_fraction=Decimal("0.5"),
        cost_reserve=Decimal("0"),
        max_profile_age_hours=168,
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
        recovery_state=PropFirmRecoveryState(),
    )

    decision = evaluate_loss_budget(context, as_of=NOW)

    assert decision.recovery_mode == "CAUTION"
    assert decision.risk_multiplier == Decimal("0.75")
    assert decision.allowed_new_trade_risk == Decimal("20.62500")
    assert decision.blocked is False
    assert decision.recovery_state is not None
    assert decision.recovery_state.mode.value == "CAUTION"


def test_recovery_state_is_required_when_enabled():
    profile = _profile(
        overall=LossLimitSpec(
            mode=LossLimitMode.STATIC,
            basis=AccountReferenceBasis.INITIAL_BALANCE,
            amount=Decimal("160"),
        )
    )
    context = _context(profile, _snapshot())
    context = PropFirmRiskContext(
        profile=context.profile,
        snapshot=context.snapshot,
        open_risk=context.open_risk,
        max_open_risk_fraction=context.max_open_risk_fraction,
        new_trade_buffer_fraction=context.new_trade_buffer_fraction,
        cost_reserve=context.cost_reserve,
        max_profile_age_hours=context.max_profile_age_hours,
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

    decision = evaluate_loss_budget(context, as_of=NOW)

    assert decision.status is ValidationStatus.UNKNOWN
    assert decision.blocked is True
    assert "Recovery state is required" in decision.reason


def test_transaction_and_slippage_reserves_reduce_budget_separately():
    profile = _profile(
        overall=LossLimitSpec(
            mode=LossLimitMode.STATIC,
            basis=AccountReferenceBasis.INITIAL_BALANCE,
            amount=Decimal("160"),
        )
    )
    context = PropFirmRiskContext(
        profile=profile,
        snapshot=_snapshot(),
        open_risk=Decimal("0"),
        max_open_risk_fraction=Decimal("0.5"),
        new_trade_buffer_fraction=Decimal("0.5"),
        cost_reserve=Decimal("2"),
        max_profile_age_hours=168,
        transaction_cost_reserve=Decimal("3"),
        slippage_reserve=Decimal("1"),
    )

    decision = evaluate_loss_budget(context, as_of=NOW)

    assert decision.transaction_cost_reserve == Decimal("3")
    assert decision.slippage_reserve == Decimal("1")
    assert decision.cost_reserve == Decimal("6")
    assert decision.available_budget == Decimal("74.0")
    assert decision.allowed_new_trade_risk == Decimal("37.00")
