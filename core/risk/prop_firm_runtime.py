"""Runtime adapter that turns a verified MT5 account into Prop-Firm context."""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Iterable, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from core.models.account_mode import (
    AccountModeState,
    AccountModeStatus,
    MT5AccountIdentity,
    TradingAccountMode,
)
from core.models.prop_firm import (
    PropFirmAccountSnapshot,
    PropFirmRecoveryState,
    PropFirmRuleProfile,
)
from core.models.risk import AccountState
from core.risk.account_mode import verify_account_binding
from core.risk.loss_budget import PropFirmRiskContext
from core.settings.account_mode_config import AccountModeConfig

logger = logging.getLogger("atlax.risk.prop_firm_runtime")


@dataclass(frozen=True)
class PropFirmRuntimeResult:
    """Result of resolving account mode and building a context."""

    status: AccountModeStatus
    reason: str
    context: Optional[PropFirmRiskContext] = None
    profile: Optional[PropFirmRuleProfile] = None


def _profile_id(profile: Any) -> str:
    return str(getattr(profile, "profile_id", "") or "").strip()


def _profile_version(profile: Any) -> str:
    return str(getattr(profile, "version", "") or "").strip()


def _recovery_state_key(
    identity: MT5AccountIdentity,
    profile: PropFirmRuleProfile,
) -> str:
    """Return an account/profile-scoped runtime key for recovery state."""

    material = "|".join(
        (
            identity.fingerprint,
            _profile_id(profile),
            _profile_version(profile),
        )
    ).encode("utf-8")
    digest = hashlib.sha256(material).hexdigest()[:24]
    return f"prop_firm.recovery_state.{digest}"




def _period_key(profile: PropFirmRuleProfile, now: datetime) -> str:
    limit = profile.daily_loss
    if limit is None or not limit.reset_timezone:
        return now.astimezone(timezone.utc).date().isoformat()
    try:
        zone = ZoneInfo(limit.reset_timezone)
    except (ZoneInfoNotFoundError, ValueError):
        return ""
    return now.astimezone(zone).date().isoformat()


def _decimal(value: Any) -> Optional[Decimal]:
    if value is None:
        return None
    try:
        parsed = Decimal(str(value))
    except (TypeError, ValueError, ArithmeticError):
        return None
    return parsed if parsed.is_finite() else None


def _snapshot(
    *,
    profile: PropFirmRuleProfile,
    identity: MT5AccountIdentity,
    account: AccountState,
    runtime_store: Any,
    now: datetime,
) -> PropFirmAccountSnapshot | None:
    """Build dynamic opening/high-watermark references without guessing."""

    period_key = _period_key(profile, now)
    if not period_key:
        return None
    history_key = f"prop_firm.account_history.{identity.fingerprint}.{profile.profile_id}"
    raw = runtime_store.get(history_key, {})
    history = raw if isinstance(raw, dict) else {}
    if history.get("period_key") != period_key:
        history = {
            "period_key": period_key,
            "opening_balance": str(account.balance),
            "opening_equity": str(account.equity),
            "initial_balance": history.get("initial_balance"),
            "high_watermark_balance": str(account.balance),
            "high_watermark_equity": str(account.equity),
        }
    else:
        current_high_balance = _decimal(history.get("high_watermark_balance"))
        current_high_equity = _decimal(history.get("high_watermark_equity"))
        history["high_watermark_balance"] = str(
            max(account.balance, current_high_balance or account.balance)
        )
        history["high_watermark_equity"] = str(
            max(account.equity, current_high_equity or account.equity)
        )
    if history.get("initial_balance") is None and profile.nominal_balance is not None:
        history["initial_balance"] = str(profile.nominal_balance)
    runtime_store.set(history_key, history)

    return PropFirmAccountSnapshot(
        profile_id=profile.profile_id,
        account_type=profile.account_type,
        currency=account.currency.upper(),
        balance=account.balance,
        equity=account.equity,
        snapshot_at=now,
        initial_balance=_decimal(history.get("initial_balance")),
        opening_balance=_decimal(history.get("opening_balance")),
        opening_equity=_decimal(history.get("opening_equity")),
        high_watermark_balance=_decimal(history.get("high_watermark_balance")),
        high_watermark_equity=_decimal(history.get("high_watermark_equity")),
    )


class PropFirmRuntimeAdapter:
    """Resolve the persisted mode into a validated loss-budget context."""

    def __init__(
        self,
        runtime_store: Any,
        config: AccountModeConfig,
        profiles: Iterable[Any] = (),
    ) -> None:
        self._runtime_store = runtime_store
        self._config = config
        self._profiles = tuple(profiles or ())

    def _selected_profile(self, state: AccountModeState) -> PropFirmRuleProfile | None:
        for profile in self._profiles:
            if not isinstance(profile, PropFirmRuleProfile):
                continue
            if (
                profile.profile_id == state.selected_profile_id
                and profile.version == state.selected_profile_version
            ):
                return profile
        return None

    def resolve(
        self,
        *,
        state: AccountModeState,
        identity: MT5AccountIdentity | None,
        account: AccountState,
        open_risk: Decimal,
        now: datetime | None = None,
    ) -> PropFirmRuntimeResult:
        current_time = now or datetime.now(timezone.utc)
        if current_time.tzinfo is None or current_time.utcoffset() is None:
            return PropFirmRuntimeResult(
                AccountModeStatus.UNKNOWN,
                "Runtime evaluation time is not timezone-aware",
            )
        if state.mode is TradingAccountMode.NORMAL_ACCOUNT:
            return PropFirmRuntimeResult(
                AccountModeStatus.NOT_REQUIRED,
                "Normal account risk policy is active",
            )
        if not self._config.enabled:
            return PropFirmRuntimeResult(
                AccountModeStatus.UNKNOWN,
                "Account-mode runtime integration is disabled in configuration",
            )
        binding_status, binding_reason = verify_account_binding(state, identity)
        if binding_status is not AccountModeStatus.VERIFIED:
            return PropFirmRuntimeResult(binding_status, binding_reason)
        profile = self._selected_profile(state)
        if profile is None:
            return PropFirmRuntimeResult(
                AccountModeStatus.UNKNOWN,
                "Selected Prop-Firm profile is not loaded as a typed source-backed profile",
            )
        if account.currency.upper() != profile.currency:
            return PropFirmRuntimeResult(
                AccountModeStatus.MISMATCH,
                "MT5 account currency does not match the selected Prop-Firm profile",
                profile=profile,
            )
        if self._config.max_open_risk_fraction is None or self._config.new_trade_buffer_fraction is None:
            return PropFirmRuntimeResult(
                AccountModeStatus.UNKNOWN,
                "Prop-Firm budget fractions are not configured",
                profile=profile,
            )
        if not isinstance(open_risk, Decimal) or not open_risk.is_finite() or open_risk < 0:
            return PropFirmRuntimeResult(
                AccountModeStatus.UNKNOWN,
                "Open risk is unavailable or invalid",
                profile=profile,
            )
        snapshot = _snapshot(
            profile=profile,
            identity=identity,
            account=account,
            runtime_store=self._runtime_store,
            now=current_time,
        )
        if snapshot is None:
            return PropFirmRuntimeResult(
                AccountModeStatus.UNKNOWN,
                "Profile reset timezone is unavailable or invalid",
                profile=profile,
            )
        recovery_state_key = _recovery_state_key(identity, profile)
        raw_recovery_state = self._runtime_store.get(recovery_state_key, None)
        if raw_recovery_state is None:
            legacy_state = self._runtime_store.get("prop_firm.recovery_state", None)
            legacy_binding = self._runtime_store.get(
                "prop_firm.recovery_state_binding", None
            )
            legacy_matches = (
                isinstance(legacy_state, dict)
                and isinstance(legacy_binding, dict)
                and legacy_binding.get("account_fingerprint") == identity.fingerprint
                and legacy_binding.get("profile_id") == profile.profile_id
                and legacy_binding.get("profile_version") == profile.version
            )
            raw_recovery_state = legacy_state if legacy_matches else None

        if raw_recovery_state is None:
            recovery_state = PropFirmRecoveryState()
            raw_recovery_state = recovery_state.to_dict()
            logger.info(
                '{"event":"prop_recovery_state_initialized",'
                '"account_fingerprint":"%s","profile_id":"%s",'
                '"profile_version":"%s","mode":"%s"}',
                identity.fingerprint,
                profile.profile_id,
                profile.version,
                recovery_state.mode.value,
            )
        else:
            try:
                recovery_state = PropFirmRecoveryState.from_dict(raw_recovery_state)
            except (TypeError, ValueError, ArithmeticError) as exc:
                return PropFirmRuntimeResult(
                    AccountModeStatus.UNKNOWN,
                    f"Persisted Prop-Firm recovery state is invalid: {str(exc)[:120]}",
                    profile=profile,
                )

        recovery_state_payload = recovery_state.to_dict()
        try:
            self._runtime_store.set(recovery_state_key, recovery_state_payload)
            self._runtime_store.set("prop_firm.recovery_state", recovery_state_payload)
            self._runtime_store.set(
                "prop_firm.recovery_state_binding",
                {
                    "account_fingerprint": identity.fingerprint,
                    "profile_id": profile.profile_id,
                    "profile_version": profile.version,
                },
            )
        except Exception as exc:  # noqa: BLE001 - recovery persistence must fail closed
            return PropFirmRuntimeResult(
                AccountModeStatus.UNKNOWN,
                f"Prop-Firm recovery state could not be persisted: {str(exc)[:120]}",
                profile=profile,
            )
        context = PropFirmRiskContext(
            profile=profile,
            snapshot=snapshot,
            open_risk=open_risk,
            max_open_risk_fraction=self._config.max_open_risk_fraction,
            new_trade_buffer_fraction=self._config.new_trade_buffer_fraction,
            cost_reserve=self._config.cost_reserve,
            max_profile_age_hours=self._config.max_profile_age_hours,
            recovery_config=self._config.recovery_config,
            recovery_state=recovery_state,
            recovery_state_key=recovery_state_key,
            transaction_cost_reserve=self._config.transaction_cost_reserve,
            slippage_reserve=self._config.slippage_reserve,
        )
        self._runtime_store.set(
            "prop_firm.snapshot",
            {
                "status": "READY",
                "profile_id": profile.profile_id,
                "profile_version": profile.version,
                "account_fingerprint": identity.fingerprint,
                "login": identity.login,
                "server": identity.server,
                "currency": identity.currency,
                "balance": str(snapshot.balance),
                "equity": str(snapshot.equity),
                "remaining_overall_buffer": None,
                "remaining_daily_buffer": None,
                "recovery_mode": "UNKNOWN",
                "risk_multiplier": None,
                "observed_at": current_time.isoformat(),
            },
        )
        return PropFirmRuntimeResult(
            AccountModeStatus.VERIFIED,
            "Prop-Firm account, profile, and runtime context are verified",
            context=context,
            profile=profile,
        )
