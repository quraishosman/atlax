"""Persistence and verification helpers for the Telegram account-mode switch."""

from __future__ import annotations

import logging
from threading import RLock
from dataclasses import replace
from datetime import datetime, timezone
from typing import Any

from core.models.account_mode import (
    AccountBinding,
    AccountModeState,
    AccountModeStatus,
    MT5AccountIdentity,
    TradingAccountMode,
)

logger = logging.getLogger("atlax.risk.account_mode")

ACCOUNT_MODE_STATE_KEY = "account_mode.state"
_STATE_LOCK = RLock()


class ConcurrentAccountModeUpdate(RuntimeError):
    """Raised when a stale account-mode state would overwrite a newer state."""


def load_account_mode_state(runtime_store: Any) -> AccountModeState:
    """Load mode state with a safe Normal-mode fallback."""

    try:
        return AccountModeState.from_dict(runtime_store.get(ACCOUNT_MODE_STATE_KEY, {}))
    except Exception as exc:  # noqa: BLE001 - state must fail closed
        logger.error(
            '{"event":"account_mode_state_load_failed","reason":"%s"}',
            str(exc)[:160],
        )
        return AccountModeState(
            mode=TradingAccountMode.PROP_FIRM_ENFORCED,
            status=AccountModeStatus.UNKNOWN,
            status_reason="Persisted account-mode state is invalid; new entries are blocked",
        )


def _save(runtime_store: Any, state: AccountModeState, actor: str) -> AccountModeState:
    with _STATE_LOCK:
        raw = runtime_store.get(ACCOUNT_MODE_STATE_KEY, {})
        try:
            persisted = AccountModeState.from_dict(raw)
        except Exception as exc:  # noqa: BLE001 - never overwrite invalid state
            if state.revision != 0:
                raise ConcurrentAccountModeUpdate(
                    "Persisted account-mode state is invalid; repair it before updating"
                ) from exc
            logger.error(
                '{"event":"account_mode_state_repaired","reason":"%s"}',
                str(exc)[:160],
            )
            persisted = AccountModeState()
        if persisted.revision != state.revision:
            raise ConcurrentAccountModeUpdate(
                "Account-mode state changed since it was read; retry from the latest state"
            )
        now = datetime.now(timezone.utc)
        saved = replace(
            state,
            revision=state.revision + 1,
            updated_at=now,
            updated_by=str(actor)[:80],
        )
        runtime_store.set(ACCOUNT_MODE_STATE_KEY, saved.to_dict())
        return saved


def bind_current_account(
    runtime_store: Any,
    identity: MT5AccountIdentity,
    *,
    actor: str,
) -> AccountModeState:
    """Bind the currently connected account as a Prop-Firm account."""

    current = load_account_mode_state(runtime_store)
    binding = AccountBinding(
        login=identity.login,
        server=identity.server,
        currency=identity.currency,
        account_class="PROP_FIRM",
        terminal_instance_id=identity.terminal_instance_id,
        bound_at=datetime.now(timezone.utc),
        bound_by=str(actor)[:80],
    )
    return _save(
        runtime_store,
        replace(
            current,
            binding=binding,
            status=AccountModeStatus.VERIFIED,
            status_reason="Current MT5 account bound by administrator",
            last_verified_at=identity.observed_at,
        ),
        actor,
    )


def set_account_mode(
    runtime_store: Any,
    *,
    mode: TradingAccountMode,
    actor: str,
    selected_profile_id: str = "",
    selected_profile_version: str = "",
) -> AccountModeState:
    """Persist a mode selection without claiming runtime verification."""

    current = load_account_mode_state(runtime_store)
    if mode is TradingAccountMode.NORMAL_ACCOUNT:
        return _save(
            runtime_store,
            replace(
                current,
                mode=mode,
                status=AccountModeStatus.NOT_REQUIRED,
                status_reason="Normal account risk policy selected",
            ),
            actor,
        )
    profile_id = selected_profile_id or current.selected_profile_id
    profile_version = selected_profile_version or current.selected_profile_version
    status = (
        AccountModeStatus.UNKNOWN
        if current.binding is None or not profile_id or not profile_version
        else AccountModeStatus.UNKNOWN
    )
    return _save(
        runtime_store,
        replace(
            current,
            mode=mode,
            selected_profile_id=profile_id,
            selected_profile_version=profile_version,
            status=status,
            status_reason="Awaiting account, profile, and runtime verification",
        ),
        actor,
    )


def select_prop_firm_profile(
    runtime_store: Any,
    *,
    profile_id: str,
    profile_version: str,
    actor: str,
) -> AccountModeState:
    """Persist a source-backed profile selection without enabling the mode."""

    selected_id = str(profile_id).strip()
    selected_version = str(profile_version).strip()
    if not selected_id or not selected_version:
        raise ValueError("profile_id and profile_version are required")

    current = load_account_mode_state(runtime_store)
    if current.mode is TradingAccountMode.NORMAL_ACCOUNT:
        status = AccountModeStatus.NOT_REQUIRED
        reason = "Prop-Firm profile selected while Normal account risk policy remains active"
    else:
        status = AccountModeStatus.UNKNOWN
        reason = "Prop-Firm profile changed; account and runtime verification is required"
    return _save(
        runtime_store,
        replace(
            current,
            selected_profile_id=selected_id,
            selected_profile_version=selected_version,
            status=status,
            status_reason=reason,
        ),
        actor,
    )


def record_verification(
    runtime_store: Any,
    *,
    status: AccountModeStatus,
    reason: str,
    actor: str = "runtime",
    verified_at: datetime | None = None,
) -> AccountModeState:
    """Persist the latest account/profile verification result."""

    current = load_account_mode_state(runtime_store)
    timestamp = verified_at or datetime.now(timezone.utc)
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise ValueError("verified_at must be timezone-aware")
    return _save(
        runtime_store,
        replace(
            current,
            status=status,
            status_reason=str(reason)[:240],
            last_verified_at=timestamp if status is AccountModeStatus.VERIFIED else current.last_verified_at,
        ),
        actor,
    )


def verify_account_binding(
    state: AccountModeState,
    identity: MT5AccountIdentity | None,
) -> tuple[AccountModeStatus, str]:
    """Return a deterministic status without changing persisted state."""

    if state.mode is TradingAccountMode.NORMAL_ACCOUNT:
        return AccountModeStatus.NOT_REQUIRED, "Normal account risk policy is active"
    if state.binding is None:
        return AccountModeStatus.UNKNOWN, "No Prop-Firm MT5 account binding is configured"
    if identity is None:
        return AccountModeStatus.UNKNOWN, "Current MT5 account identity is unavailable"
    if not state.selected_profile_id or not state.selected_profile_version:
        return AccountModeStatus.UNKNOWN, "No verified Prop-Firm profile is selected"
    if not state.binding.matches(identity):
        return (
            AccountModeStatus.MISMATCH,
            "Connected MT5 login, server, currency, or terminal binding does not match",
        )
    return AccountModeStatus.VERIFIED, "Connected MT5 account matches the Prop-Firm binding"
