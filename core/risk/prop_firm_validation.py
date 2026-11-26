"""Fail-closed validation for Prop-Firm Mode Phase 1 models."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

from core.models.prop_firm import (
    AccountReferenceBasis,
    LossLimitMode,
    PropFirmAccountSnapshot,
    PropFirmRuleProfile,
    ValidationStatus,
)


@dataclass(frozen=True)
class ProfileValidationResult:
    """Structured validation result for audit logs and fail-closed callers."""

    status: ValidationStatus
    errors: tuple[str, ...] = ()
    unknown_reasons: tuple[str, ...] = ()

    @property
    def usable(self) -> bool:
        return self.status is ValidationStatus.VALID


def _source_url_errors(profile: PropFirmRuleProfile) -> list[str]:
    errors: list[str] = []
    seen: set[str] = set()
    for url in profile.source_urls:
        parsed = urlparse(url)
        if parsed.scheme != "https" or not parsed.netloc:
            errors.append("source_urls must contain HTTPS URLs")
        if url in seen:
            errors.append("source_urls must not contain duplicates")
        seen.add(url)
    return errors


def _profile_rule_errors(profile: PropFirmRuleProfile) -> list[str]:
    errors: list[str] = []
    for name, limit in (
        ("overall_loss", profile.overall_loss),
        ("daily_loss", profile.daily_loss),
    ):
        if limit is None:
            continue
        if name == "daily_loss" and limit.reset_timezone is None:
            errors.append("daily_loss requires reset_timezone")
        if (
            limit.mode == LossLimitMode.END_OF_DAY_TRAILING
            and limit.reset_timezone is None
        ):
            errors.append(f"{name} end-of-day trailing rule requires reset_timezone")
    return errors


def validate_profile(
    profile: PropFirmRuleProfile,
    *,
    as_of: datetime | None = None,
    max_age_hours: float | None = None,
) -> ProfileValidationResult:
    """Validate source completeness and freshness without applying rules."""

    errors = _source_url_errors(profile) + _profile_rule_errors(profile)
    if errors:
        return ProfileValidationResult(ValidationStatus.INVALID, tuple(errors))

    now = as_of or datetime.now(timezone.utc)
    if now.tzinfo is None or now.utcoffset() is None:
        return ProfileValidationResult(
            ValidationStatus.INVALID,
            ("as_of must be timezone-aware",),
        )
    if max_age_hours is None or max_age_hours <= 0:
        return ProfileValidationResult(
            ValidationStatus.INVALID,
            ("max_age_hours must be positive",),
        )
    if profile.source_retrieved_at > now:
        return ProfileValidationResult(
            ValidationStatus.INVALID,
            ("source_retrieved_at is in the future",),
        )
    if now - profile.source_retrieved_at > timedelta(hours=max_age_hours):
        return ProfileValidationResult(
            ValidationStatus.UNKNOWN,
            unknown_reasons=("rule profile source is stale",),
        )
    return ProfileValidationResult(ValidationStatus.VALID)


def _basis_value_available(
    profile: PropFirmRuleProfile,
    snapshot: PropFirmAccountSnapshot,
    basis: AccountReferenceBasis,
) -> bool:
    if basis is AccountReferenceBasis.INITIAL_BALANCE:
        return snapshot.initial_balance is not None or profile.nominal_balance is not None
    if basis is AccountReferenceBasis.OPENING_BALANCE:
        return snapshot.opening_balance is not None
    if basis is AccountReferenceBasis.OPENING_EQUITY:
        return snapshot.opening_equity is not None
    if basis is AccountReferenceBasis.HIGH_WATERMARK_BALANCE:
        return snapshot.high_watermark_balance is not None
    if basis is AccountReferenceBasis.HIGH_WATERMARK_EQUITY:
        return snapshot.high_watermark_equity is not None
    return False


def validate_snapshot_for_profile(
    profile: PropFirmRuleProfile,
    snapshot: PropFirmAccountSnapshot,
) -> ProfileValidationResult:
    """Check that a broker snapshot contains the references the profile needs."""

    errors: list[str] = []
    unknown: list[str] = []
    if snapshot.profile_id != profile.profile_id:
        errors.append("account snapshot profile_id does not match rule profile")
    if snapshot.account_type != profile.account_type:
        errors.append("account snapshot account_type does not match rule profile")
    if snapshot.currency != profile.currency:
        errors.append("account snapshot currency does not match rule profile")

    for name, limit in (
        ("overall_loss", profile.overall_loss),
        ("daily_loss", profile.daily_loss),
    ):
        if limit is None:
            continue
        if limit.percentage is not None and not _basis_value_available(
            profile, snapshot, limit.basis
        ):
            unknown.append(f"{name} reference value is unavailable")

    if errors:
        return ProfileValidationResult(ValidationStatus.INVALID, tuple(errors))
    if unknown:
        return ProfileValidationResult(
            ValidationStatus.UNKNOWN,
            unknown_reasons=tuple(unknown),
        )
    return ProfileValidationResult(ValidationStatus.VALID)
