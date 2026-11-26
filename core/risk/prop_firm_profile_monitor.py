"""Operational freshness and approval monitoring for Prop-Firm profiles.

The profile catalog is source-backed configuration.  This module does not
scrape rules or infer missing values; it only validates the typed catalog,
tracks the last approved identity, and blocks live use when the current
profile is stale, changed, invalid, or unavailable.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable

from core.models.prop_firm import PropFirmRuleProfile, ValidationStatus
from core.risk.prop_firm_validation import validate_profile

logger = logging.getLogger("atlax.risk.prop_firm_profile_monitor")


class ProfileMonitorStatus:
    """Stable status values persisted for operations and audit views."""

    APPROVED = "APPROVED"
    STALE = "STALE"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    INVALID = "INVALID"
    MISSING = "MISSING"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class ProfileMonitorEntry:
    """Safe operational metadata for one current or previously approved profile."""

    profile_id: str
    status: str
    reason: str
    version: str = ""
    content_hash: str = ""
    source_retrieved_at: str = ""
    source_urls: tuple[str, ...] = ()
    approved_version: str = ""
    approved_content_hash: str = ""
    observed_at: str = ""

    @property
    def live_approved(self) -> bool:
        return (
            self.status == ProfileMonitorStatus.APPROVED
            and bool(self.version)
            and self.version == self.approved_version
            and bool(self.content_hash)
            and self.content_hash == self.approved_content_hash
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "profile_id": self.profile_id,
            "status": self.status,
            "reason": self.reason[:240],
            "version": self.version,
            "content_hash": self.content_hash,
            "source_retrieved_at": self.source_retrieved_at,
            "source_urls": list(self.source_urls),
            "approved_version": self.approved_version,
            "approved_content_hash": self.approved_content_hash,
            "observed_at": self.observed_at,
            "live_approved": self.live_approved,
        }


@dataclass(frozen=True)
class PropFirmProfileMonitorReport:
    """Complete monitor result used by gates, snapshots, and audit logs."""

    observed_at: str
    overall_status: str
    entries: tuple[ProfileMonitorEntry, ...]
    transitions: tuple[dict[str, str], ...] = ()

    def entry_for(self, profile_id: str, version: str = "") -> ProfileMonitorEntry | None:
        selected = str(profile_id or "").strip()
        selected_version = str(version or "").strip()
        for entry in self.entries:
            if entry.profile_id == selected and (
                not selected_version or entry.version == selected_version
            ):
                return entry
        return None

    def is_live_approved(self, profile_id: str, version: str = "") -> bool:
        entry = self.entry_for(profile_id, version)
        return bool(entry and entry.live_approved)

    def reason_for(self, profile_id: str, version: str = "") -> str:
        entry = self.entry_for(profile_id, version)
        return entry.reason if entry else "Selected Prop-Firm profile is not present in the monitor"

    def to_dict(self) -> dict[str, Any]:
        return {
            "observed_at": self.observed_at,
            "overall_status": self.overall_status,
            "entries": [entry.to_dict() for entry in self.entries],
            "transitions": [dict(item) for item in self.transitions],
        }


def _profile_metadata(profile: PropFirmRuleProfile) -> dict[str, Any]:
    return {
        "profile_id": profile.profile_id,
        "version": profile.version,
        "content_hash": profile.content_hash,
        "source_retrieved_at": profile.source_retrieved_at.isoformat(),
        "source_urls": list(profile.source_urls),
    }


def _status_for_validation(result) -> tuple[str, str]:
    if result.status is ValidationStatus.VALID:
        return ProfileMonitorStatus.APPROVED, "Source-backed profile is current and approved"
    if result.status is ValidationStatus.INVALID:
        reason = "; ".join(result.errors) or "Source-backed profile is invalid"
        return ProfileMonitorStatus.INVALID, reason
    reason = "; ".join(result.unknown_reasons) or "Profile freshness is unknown"
    return ProfileMonitorStatus.STALE, reason


class PropFirmProfileMonitor:
    """Track source-backed profile freshness without changing rule contents."""

    STATE_KEY = "operations.prop_firm_profiles"
    STATE_VERSION = 1

    def __init__(
        self,
        runtime_store: Any,
        profiles: Iterable[PropFirmRuleProfile],
        *,
        max_age_hours: float,
    ) -> None:
        self._runtime_store = runtime_store
        self._profiles = tuple(profiles or ())
        self._max_age_hours = float(max_age_hours)

    def evaluate(self, *, now: datetime | None = None) -> PropFirmProfileMonitorReport:
        """Evaluate current profiles and persist only approval-state changes."""

        observed = now or datetime.now(timezone.utc)
        if observed.tzinfo is None or observed.utcoffset() is None:
            observed = observed.replace(tzinfo=timezone.utc)
        observed_iso = observed.astimezone(timezone.utc).isoformat()

        try:
            persisted = self._runtime_store.get(self.STATE_KEY, {}) or {}
            if not isinstance(persisted, dict):
                persisted = {}
        except Exception as exc:  # noqa: BLE001 - monitor must fail closed
            logger.error(
                '{"event":"prop_firm_profile_monitor_state_failed","error":"%s"}',
                str(exc)[:160],
            )
            return self._unknown_report(observed_iso, "Runtime profile-monitor state is unavailable")

        previous_profiles = persisted.get("profiles", {})
        if not isinstance(previous_profiles, dict):
            previous_profiles = {}

        current_by_id = {profile.profile_id: profile for profile in self._profiles}
        entries: list[ProfileMonitorEntry] = []
        transitions: list[dict[str, str]] = []

        for profile_id, profile in current_by_id.items():
            previous = previous_profiles.get(profile_id)
            previous_approved = (
                previous.get("approved") if isinstance(previous, dict) else None
            )
            if not isinstance(previous_approved, dict):
                previous_approved = None

            validation = validate_profile(
                profile,
                as_of=observed,
                max_age_hours=self._max_age_hours,
            )
            validation_status, validation_reason = _status_for_validation(validation)
            metadata = _profile_metadata(profile)

            if previous_approved is None:
                # The checked-in catalog is the initial governance baseline.
                status = (
                    ProfileMonitorStatus.APPROVED
                    if validation_status == ProfileMonitorStatus.APPROVED
                    else validation_status
                )
                approved = metadata if status == ProfileMonitorStatus.APPROVED else None
                reason = (
                    "Initial source-backed catalog baseline recorded"
                    if approved is not None
                    else validation_reason
                )
                previous_status = "UNSEEN"
            else:
                previous_status = str(previous.get("status", ProfileMonitorStatus.UNKNOWN))
                same_approved_identity = (
                    metadata["version"] == str(previous_approved.get("version", ""))
                    and metadata["content_hash"] == str(previous_approved.get("content_hash", ""))
                )
                if not same_approved_identity:
                    status = ProfileMonitorStatus.REVIEW_REQUIRED
                    reason = (
                        "Profile version or content hash changed; project-lead review is required"
                    )
                    approved = previous_approved
                elif validation_status == ProfileMonitorStatus.APPROVED:
                    status = ProfileMonitorStatus.APPROVED
                    reason = validation_reason
                    approved = previous_approved
                else:
                    status = validation_status
                    reason = validation_reason
                    approved = previous_approved

            entry = ProfileMonitorEntry(
                profile_id=profile_id,
                status=status,
                reason=reason,
                version=str(metadata["version"]),
                content_hash=str(metadata["content_hash"]),
                source_retrieved_at=str(metadata["source_retrieved_at"]),
                source_urls=tuple(str(url) for url in metadata["source_urls"]),
                approved_version=str((approved or {}).get("version", "")),
                approved_content_hash=str((approved or {}).get("content_hash", "")),
                observed_at=observed_iso,
            )
            entries.append(entry)
            if previous_status != status:
                transitions.append(
                    {
                        "profile_id": profile_id,
                        "from_status": previous_status,
                        "to_status": status,
                        "reason": reason[:240],
                    }
                )

        for profile_id, previous in previous_profiles.items():
            if profile_id in current_by_id or not isinstance(previous, dict):
                continue
            approved = previous.get("approved") if isinstance(previous.get("approved"), dict) else {}
            entry = ProfileMonitorEntry(
                profile_id=str(profile_id),
                status=ProfileMonitorStatus.MISSING,
                reason="Previously approved profile is missing from the loaded catalog",
                version=str(previous.get("version", "")),
                content_hash=str(previous.get("content_hash", "")),
                source_retrieved_at=str(previous.get("source_retrieved_at", "")),
                source_urls=tuple(str(url) for url in previous.get("source_urls", ()) or ()),
                approved_version=str(approved.get("version", "")),
                approved_content_hash=str(approved.get("content_hash", "")),
                observed_at=observed_iso,
            )
            entries.append(entry)
            previous_status = str(previous.get("status", ProfileMonitorStatus.UNKNOWN))
            if previous_status != ProfileMonitorStatus.MISSING:
                transitions.append(
                    {
                        "profile_id": str(profile_id),
                        "from_status": previous_status,
                        "to_status": ProfileMonitorStatus.MISSING,
                        "reason": entry.reason,
                    }
                )

        overall_status = (
            "APPROVED"
            if entries and all(entry.status == ProfileMonitorStatus.APPROVED for entry in entries)
            else ("UNKNOWN" if entries else ProfileMonitorStatus.UNKNOWN)
        )
        state_profiles: dict[str, Any] = {}
        for entry in entries:
            persisted_entry = entry.to_dict()
            persisted_entry.pop("observed_at", None)
            state_profiles[entry.profile_id] = {
                **persisted_entry,
                "approved": {
                    "version": entry.approved_version,
                    "content_hash": entry.approved_content_hash,
                }
                if entry.approved_version and entry.approved_content_hash
                else None,
            }
        next_state = {
            "version": self.STATE_VERSION,
            "observed_at": observed_iso,
            "profiles": state_profiles,
        }
        if next_state.get("profiles") != persisted.get("profiles"):
            try:
                self._runtime_store.set(self.STATE_KEY, next_state)
            except Exception as exc:  # noqa: BLE001 - report UNKNOWN at next read
                logger.error(
                    '{"event":"prop_firm_profile_monitor_save_failed","error":"%s"}',
                    str(exc)[:160],
                )
                return self._unknown_report(observed_iso, "Runtime profile-monitor state could not be persisted")

        return PropFirmProfileMonitorReport(
            observed_at=observed_iso,
            overall_status=overall_status,
            entries=tuple(entries),
            transitions=tuple(transitions),
        )

    @staticmethod
    def _unknown_report(observed_at: str, reason: str) -> PropFirmProfileMonitorReport:
        return PropFirmProfileMonitorReport(
            observed_at=observed_at,
            overall_status=ProfileMonitorStatus.UNKNOWN,
            entries=(),
            transitions=({"profile_id": "*", "from_status": "UNKNOWN", "to_status": "UNKNOWN", "reason": reason},),
        )
