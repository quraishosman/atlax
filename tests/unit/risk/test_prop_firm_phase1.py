from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from core.models.prop_firm import (
    AccountReferenceBasis,
    LossLimitMode,
    LossLimitSpec,
    PropFirmAccountSnapshot,
    PropFirmRuleProfile,
    ValidationStatus,
)
from core.risk.prop_firm_validation import (
    validate_profile,
    validate_snapshot_for_profile,
)


NOW = datetime(2026, 9, 19, 12, 0, tzinfo=timezone.utc)


def _limit(
    *,
    mode=LossLimitMode.STATIC,
    basis=AccountReferenceBasis.INITIAL_BALANCE,
    amount=None,
    percentage=None,
    reset_timezone=None,
):
    return LossLimitSpec(
        mode=mode,
        basis=basis,
        amount=amount,
        percentage=percentage,
        reset_timezone=reset_timezone,
    )


def _profile(*, overall_loss, daily_loss=None, nominal_balance=Decimal("2000")):
    return PropFirmRuleProfile(
        profile_id="firm-program-phase-account-2000",
        firm_name="Example Firm",
        program_name="Example Program",
        phase="FUNDED",
        account_type="STANDARD",
        currency="USD",
        nominal_balance=nominal_balance,
        overall_loss=overall_loss,
        daily_loss=daily_loss,
        source_urls=("https://example.test/rules",),
        source_retrieved_at=NOW - timedelta(hours=1),
        version="2026-09-19.1",
        content_hash="sha256:example",
    )


def _snapshot(**overrides):
    values = dict(
        profile_id="firm-program-phase-account-2000",
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


def test_static_absolute_profile_is_valid():
    profile = _profile(
        overall_loss=_limit(amount=Decimal("160")),
        daily_loss=_limit(
            amount=Decimal("80"),
            basis=AccountReferenceBasis.OPENING_EQUITY,
            reset_timezone="Africa/Dar_es_Salaam",
        ),
    )

    result = validate_profile(profile, as_of=NOW, max_age_hours=24)

    assert result.status is ValidationStatus.VALID
    assert result.usable is True


def test_trailing_equity_percentage_requires_high_watermark():
    profile = _profile(
        overall_loss=_limit(
            mode=LossLimitMode.TRAILING,
            basis=AccountReferenceBasis.HIGH_WATERMARK_EQUITY,
            percentage=Decimal("8"),
        )
    )

    result = validate_snapshot_for_profile(
        profile,
        _snapshot(high_watermark_equity=Decimal("2200")),
    )

    assert result.status is ValidationStatus.VALID


def test_end_of_day_trailing_requires_opening_reference_and_timezone():
    profile = _profile(
        overall_loss=_limit(
            mode=LossLimitMode.END_OF_DAY_TRAILING,
            basis=AccountReferenceBasis.OPENING_EQUITY,
            percentage=Decimal("5"),
            reset_timezone="Europe/Prague",
        ),
        daily_loss=_limit(
            amount=Decimal("100"),
            basis=AccountReferenceBasis.OPENING_BALANCE,
            reset_timezone="Europe/Prague",
        ),
    )

    result = validate_snapshot_for_profile(
        profile,
        _snapshot(opening_equity=Decimal("2100"), opening_balance=Decimal("2100")),
    )

    assert result.status is ValidationStatus.VALID


def test_equity_profile_is_unknown_when_required_reference_is_missing():
    profile = _profile(
        overall_loss=_limit(
            mode=LossLimitMode.TRAILING,
            basis=AccountReferenceBasis.HIGH_WATERMARK_EQUITY,
            percentage=Decimal("8"),
        )
    )

    result = validate_snapshot_for_profile(
        profile,
        _snapshot(high_watermark_equity=None),
    )

    assert result.status is ValidationStatus.UNKNOWN
    assert "reference value is unavailable" in result.unknown_reasons[0]


def test_stale_profile_is_unknown_not_implicitly_accepted():
    profile = _profile(
        overall_loss=_limit(amount=Decimal("160")),
    )
    stale = PropFirmRuleProfile(
        **{
            **profile.__dict__,
            "source_retrieved_at": NOW - timedelta(days=8),
        }
    )

    result = validate_profile(stale, as_of=NOW, max_age_hours=168)

    assert result.status is ValidationStatus.UNKNOWN


def test_profile_and_snapshot_mismatch_is_invalid():
    profile = _profile(overall_loss=_limit(amount=Decimal("160")))

    result = validate_snapshot_for_profile(
        profile,
        _snapshot(currency="EUR"),
    )

    assert result.status is ValidationStatus.INVALID
    assert "currency" in result.errors[0]


def test_loss_limit_rejects_ambiguous_amount_and_percentage():
    with pytest.raises(ValueError, match="exactly one"):
        _limit(amount=Decimal("160"), percentage=Decimal("8"))


def test_end_of_day_trailing_requires_timezone():
    with pytest.raises(ValueError, match="reset_timezone"):
        _limit(
            mode=LossLimitMode.END_OF_DAY_TRAILING,
            basis=AccountReferenceBasis.OPENING_EQUITY,
            percentage=Decimal("5"),
        )
