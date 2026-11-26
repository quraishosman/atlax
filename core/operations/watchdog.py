"""Restart-safe, observational runtime health watchdog."""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Optional

from core.settings.watchdog_config import WatchdogConfig

logger = logging.getLogger("atlax.operations.watchdog")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def _parse_timestamp(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _component(
    status: str,
    reason: str,
    *,
    observed_at: str,
    last_success_at: Any = None,
) -> dict[str, str]:
    normalized = str(status or "UNKNOWN").upper()
    if normalized not in {"HEALTHY", "DEGRADED", "UNKNOWN"}:
        normalized = "UNKNOWN"
    result = {
        "status": normalized,
        "reason": str(reason or "")[:240],
        "observed_at": observed_at,
    }
    if last_success_at:
        result["last_success_at"] = str(last_success_at)
    return result


class ReliabilityWatchdog:
    """Aggregate runtime health without changing entry or position behavior."""

    STATE_KEY = "operations.watchdog"

    def __init__(
        self,
        runtime_store: Any,
        config: WatchdogConfig,
        *,
        clock: Callable[[], datetime] = _now,
        instance_lock_path: str = "data/scanner.instance.lock",
    ) -> None:
        self._runtime_store = runtime_store
        self._config = config
        self._clock = clock
        self._instance_lock_path = Path(instance_lock_path)
        self._notifier: Optional[Callable[[str], None]] = None
        self._state = self._load()
        # Configuration drift is evaluated relative to this scanner process.
        # A persisted fingerprint from an older process must not keep the
        # newly restarted scanner permanently degraded after it has loaded the
        # current configuration successfully.
        self._state["configuration_fingerprint"] = None
        self._state["configuration_baseline_at"] = None

    @property
    def enabled(self) -> bool:
        return self._config.enabled

    def set_notifier(self, notifier: Optional[Callable[[str], None]]) -> None:
        """Install an administrator notification callback after Telegram starts."""
        self._notifier = notifier

    def heartbeat(
        self,
        components: dict[str, dict[str, Any]],
        *,
        force: bool = False,
    ) -> dict[str, Any]:
        """Evaluate one bounded health snapshot and persist it when due."""
        now = self._clock().astimezone(timezone.utc)
        observed_at = _iso(now)
        if not self.enabled:
            snapshot = {
                "schema_version": 1,
                "status": "DISABLED",
                "observed_at": observed_at,
                "components": {},
                "consecutive_failures": 0,
                "notification_sent": False,
            }
            return snapshot

        safe_components = {
            str(name): _component(
                value.get("status", "UNKNOWN"),
                value.get("reason", ""),
                observed_at=observed_at,
                last_success_at=value.get("last_success_at"),
            )
            for name, value in (components or {}).items()
            if isinstance(value, dict)
        }
        statuses = {item["status"] for item in safe_components.values()}
        if not safe_components or "UNKNOWN" in statuses:
            overall = "UNKNOWN"
        elif "DEGRADED" in statuses:
            overall = "DEGRADED"
        else:
            overall = "HEALTHY"

        previous_status = str(self._state.get("status", "UNKNOWN"))
        previous_heartbeat = _parse_timestamp(self._state.get("last_heartbeat_at"))
        due = (
            previous_heartbeat is None
            or now - previous_heartbeat
            >= timedelta(seconds=self._config.heartbeat_interval_seconds)
        )
        status_changed = previous_status != overall
        unhealthy = overall != "HEALTHY"
        if unhealthy:
            self._state["consecutive_failures"] = int(
                self._state.get("consecutive_failures", 0)
            ) + 1
        else:
            self._state["consecutive_failures"] = 0
            self._state["last_failure_signature"] = ""

        failure_signature = "|".join(
            f"{name}:{value['status']}:{value['reason']}"
            for name, value in sorted(safe_components.items())
            if value["status"] != "HEALTHY"
        )[:800]
        notification_sent = False
        if (
            unhealthy
            and int(self._state["consecutive_failures"])
            >= self._config.failure_threshold
            and self._notification_allowed(now, failure_signature)
        ):
            notification_sent = self._notify(overall, safe_components, observed_at)
            self._state["last_notification_at"] = observed_at
            self._state["last_failure_signature"] = failure_signature
            self._state["notifications_sent"] = int(
                self._state.get("notifications_sent", 0)
            ) + 1

        self._state["status"] = overall
        self._state["last_heartbeat_at"] = observed_at
        self._state["heartbeat_count"] = int(self._state.get("heartbeat_count", 0)) + 1
        self._state["components"] = safe_components
        self._state["updated_at"] = observed_at
        if force or due or status_changed or notification_sent:
            self._save()

        event_name = "watchdog_heartbeat" if overall == "HEALTHY" else "watchdog_failure"
        if force or due or status_changed:
            logger.info(
                json.dumps(
                    {
                        "event": event_name,
                        "status": overall,
                        "consecutive_failures": int(
                            self._state["consecutive_failures"]
                        ),
                        "components": {
                            name: item["status"]
                            for name, item in safe_components.items()
                        },
                        "notification_sent": notification_sent,
                    },
                    sort_keys=True,
                )
            )
        return {
            "schema_version": 1,
            "status": overall,
            "observed_at": observed_at,
            "components": safe_components,
            "consecutive_failures": int(self._state["consecutive_failures"]),
            "notification_sent": notification_sent,
        }

    def check_instance_lock(
        self,
        *,
        current_process_owns_lock: Optional[bool] = None,
    ) -> dict[str, str]:
        """Verify the lock without attempting to acquire a second lock.

        Windows keeps the scanner lock handle exclusive, so the live scanner
        supplies the ownership fact it already established at startup.
        """
        if current_process_owns_lock is True:
            if self._instance_lock_path.exists():
                return {
                    "status": "HEALTHY",
                    "reason": "current_process_owns_lock",
                }
            return {"status": "UNKNOWN", "reason": "instance_lock_missing"}
        if current_process_owns_lock is False:
            return {
                "status": "DEGRADED",
                "reason": "current_process_does_not_own_lock",
            }
        try:
            value = self._instance_lock_path.read_text(encoding="utf-8").strip()
            if value and int(value) == os.getpid():
                return {"status": "HEALTHY", "reason": "current_process_owns_lock"}
            if value:
                return {"status": "DEGRADED", "reason": "lock_is_owned_by_another_pid"}
            return {"status": "UNKNOWN", "reason": "lock_pid_is_empty"}
        except FileNotFoundError:
            return {"status": "UNKNOWN", "reason": "instance_lock_missing"}
        except (OSError, TypeError, ValueError):
            return {"status": "UNKNOWN", "reason": "instance_lock_unreadable"}

    def check_configuration(self, config_path: str) -> dict[str, str]:
        """Detect missing or changed config without reloading live settings."""
        try:
            path = Path(config_path)
            stat = path.stat()
            fingerprint = f"{stat.st_mtime_ns}:{stat.st_size}"
        except OSError:
            return {"status": "UNKNOWN", "reason": "configuration_file_unavailable"}
        initial = self._state.get("configuration_fingerprint")
        if initial is None:
            self._state["configuration_fingerprint"] = fingerprint
            return {"status": "HEALTHY", "reason": "configuration_file_available"}
        if initial != fingerprint:
            return {
                "status": "DEGRADED",
                "reason": "configuration_changed_requires_restart",
            }
        return {"status": "HEALTHY", "reason": "configuration_unchanged"}

    def _notification_allowed(self, now: datetime, signature: str) -> bool:
        previous = _parse_timestamp(self._state.get("last_notification_at"))
        if previous is None:
            return True
        if self._state.get("last_failure_signature") != signature:
            return True
        # A persistent unchanged failure is already visible in the Operations
        # Snapshot. Repeating it indefinitely creates Telegram noise without
        # adding information. Re-notification is an explicit opt-in so the
        # default remains transition-oriented: first failure, changed failure,
        # then a new notification only after recovery clears the signature.
        if not self._config.repeat_notifications:
            return False
        return now - previous >= timedelta(
            seconds=self._config.notification_cooldown_seconds
        )

    def _notify(
        self,
        status: str,
        components: dict[str, dict[str, str]],
        observed_at: str,
    ) -> bool:
        failed = ", ".join(
            f"{name}: {item['reason']}"
            for name, item in sorted(components.items())
            if item["status"] != "HEALTHY"
        )
        message = (
            "🩺 ATLAX Reliability Watchdog\n\n"
            f"Status: {status}\n"
            f"Components: {failed[:900]}\n"
            f"Observed: {observed_at}\n\n"
            "New-entry fail-closed controls remain authoritative. "
            "Review the Operations Snapshot before taking action."
        )
        if self._notifier is None:
            logger.warning(
                '{"event":"watchdog_notification_unavailable","status":"%s"}',
                status,
            )
            return False
        try:
            self._notifier(message)
            return True
        except Exception as exc:  # noqa: BLE001 - notification cannot affect trading
            logger.warning(
                '{"event":"watchdog_notification_failed","error":"%s"}',
                str(exc)[:160],
            )
            return False

    def _load(self) -> dict[str, Any]:
        default = {
            "schema_version": 1,
            "status": "UNKNOWN",
            "updated_at": None,
            "last_heartbeat_at": None,
            "heartbeat_count": 0,
            "consecutive_failures": 0,
            "notifications_sent": 0,
            "last_notification_at": None,
            "last_failure_signature": "",
            "configuration_fingerprint": None,
            "components": {},
        }
        try:
            raw = self._runtime_store.get(self.STATE_KEY, None)
        except Exception as exc:  # noqa: BLE001 - unknown state stays unknown
            logger.warning(
                '{"event":"watchdog_state_load_failed","error":"%s"}',
                str(exc)[:160],
            )
            return default
        if not isinstance(raw, dict):
            return default
        default.update(
            {
                key: raw[key]
                for key in default
                if key in raw
            }
        )
        return default

    def _save(self) -> None:
        try:
            self._runtime_store.set(self.STATE_KEY, self._state)
        except Exception as exc:  # noqa: BLE001 - health persistence failure is logged
            logger.error(
                '{"event":"watchdog_state_save_failed","error":"%s"}',
                str(exc)[:160],
            )


__all__ = ["ReliabilityWatchdog"]
