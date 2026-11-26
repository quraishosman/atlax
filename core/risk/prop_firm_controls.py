"""Persisted, admin-authorized control state for Prop-Firm Mode.

This module stores only the operator's control selection. It does not load
firm rules, calculate risk, or pause the global scanner. The Risk Engine and
execution guard remain the enforcement authorities.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger("atlax.risk.prop_firm_controls")

CONTROL_STATE_KEY = "prop_firm.control"


@dataclass(frozen=True)
class PropFirmControlState:
    """Restart-safe operator selection shown by Telegram."""

    enabled: bool = False
    paused: bool = False
    selected_profile_id: str = ""
    selected_profile_version: str = ""
    updated_at: Optional[str] = None
    updated_by: str = ""

    def __post_init__(self) -> None:
        for field_name in (
            "selected_profile_id",
            "selected_profile_version",
            "updated_by",
        ):
            if not isinstance(getattr(self, field_name), str):
                raise ValueError(f"{field_name} must be a string")
        if not isinstance(self.enabled, bool) or not isinstance(self.paused, bool):
            raise ValueError("enabled and paused must be booleans")
        if self.updated_at is not None and not isinstance(self.updated_at, str):
            raise ValueError("updated_at must be a string or None")

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "paused": self.paused,
            "selected_profile_id": self.selected_profile_id,
            "selected_profile_version": self.selected_profile_version,
            "updated_at": self.updated_at,
            "updated_by": self.updated_by,
        }

    @classmethod
    def from_dict(cls, raw: Any) -> "PropFirmControlState":
        if not isinstance(raw, dict):
            return cls()
        return cls(
            enabled=raw.get("enabled") is True,
            paused=raw.get("paused") is True,
            selected_profile_id=str(raw.get("selected_profile_id") or ""),
            selected_profile_version=str(raw.get("selected_profile_version") or ""),
            updated_at=(str(raw["updated_at"]) if raw.get("updated_at") else None),
            updated_by=str(raw.get("updated_by") or ""),
        )


def load_control_state(runtime_store: Any) -> PropFirmControlState:
    """Load state with a disabled fail-closed fallback."""
    try:
        return PropFirmControlState.from_dict(
            runtime_store.get(CONTROL_STATE_KEY, {})
        )
    except Exception as exc:  # noqa: BLE001 - controls must fail closed
        logger.error(
            '{"event":"prop_firm_control_state_load_failed","error":"%s"}',
            str(exc)[:160],
        )
        return PropFirmControlState()


def _save(
    runtime_store: Any,
    state: PropFirmControlState,
    actor: str,
) -> PropFirmControlState:
    updated = PropFirmControlState(
        enabled=state.enabled,
        paused=state.paused,
        selected_profile_id=state.selected_profile_id,
        selected_profile_version=state.selected_profile_version,
        updated_at=datetime.now(timezone.utc).isoformat(),
        updated_by=str(actor)[:80],
    )
    runtime_store.set(CONTROL_STATE_KEY, updated.to_dict())
    return updated


def select_profile(
    runtime_store: Any,
    *,
    profile_id: str,
    profile_version: str,
    actor: str,
) -> PropFirmControlState:
    """Persist a server-allow-listed profile selection."""
    profile_id = str(profile_id or "").strip()
    profile_version = str(profile_version or "").strip()
    if not profile_id or not profile_version:
        raise ValueError("profile_id and profile_version are required")
    current = load_control_state(runtime_store)
    return _save(
        runtime_store,
        PropFirmControlState(
            enabled=current.enabled,
            paused=current.paused,
            selected_profile_id=profile_id,
            selected_profile_version=profile_version,
        ),
        actor,
    )


def set_enabled(
    runtime_store: Any,
    *,
    enabled: bool,
    actor: str,
    profile_available: bool,
) -> PropFirmControlState:
    """Enable only when a verified profile is available and selected."""
    current = load_control_state(runtime_store)
    if enabled and (not current.selected_profile_id or not profile_available):
        raise ValueError("A verified selected profile is required before enabling")
    return _save(
        runtime_store,
        PropFirmControlState(
            enabled=bool(enabled),
            paused=current.paused if enabled else False,
            selected_profile_id=current.selected_profile_id,
            selected_profile_version=current.selected_profile_version,
        ),
        actor,
    )


def set_paused(
    runtime_store: Any,
    *,
    paused: bool,
    actor: str,
) -> PropFirmControlState:
    """Persist a Prop-Firm new-entry pause without touching global scanner pause."""
    current = load_control_state(runtime_store)
    return _save(
        runtime_store,
        PropFirmControlState(
            enabled=current.enabled,
            paused=bool(paused),
            selected_profile_id=current.selected_profile_id,
            selected_profile_version=current.selected_profile_version,
        ),
        actor,
    )
