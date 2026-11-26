import os
from datetime import datetime, timedelta, timezone

from core.operations.watchdog import ReliabilityWatchdog
from core.settings.watchdog_config import WatchdogConfig, load_watchdog_config


class MemoryRuntime:
    def __init__(self):
        self.values = {}

    def get(self, key, default=None):
        return self.values.get(key, default)

    def set(self, key, value):
        self.values[key] = value


class Clock:
    def __init__(self):
        self.value = datetime(2026, 9, 20, 7, 0, tzinfo=timezone.utc)

    def __call__(self):
        return self.value

    def advance(self, seconds):
        self.value += timedelta(seconds=seconds)


def _config(**overrides):
    values = {
        "enabled": True,
        "heartbeat_interval_seconds": 1,
        "failure_threshold": 2,
        "notification_cooldown_seconds": 300,
        "notification_ttl_seconds": 300,
        "market_data_stale_after_seconds": 180,
        "telegram_stale_after_seconds": 180,
    }
    values.update(overrides)
    return WatchdogConfig(**values)


def _components(status="HEALTHY"):
    return {
        "scanner": {"status": status, "reason": "test"},
        "mt5_ipc": {"status": status, "reason": "test"},
        "market_data": {"status": status, "reason": "test"},
    }


def test_healthy_heartbeat_is_persisted_and_reports_component_states():
    runtime = MemoryRuntime()
    clock = Clock()
    watchdog = ReliabilityWatchdog(runtime, _config(), clock=clock)

    snapshot = watchdog.heartbeat(_components())

    assert snapshot["status"] == "HEALTHY"
    assert snapshot["consecutive_failures"] == 0
    assert runtime.get("operations.watchdog")["status"] == "HEALTHY"
    assert runtime.get("operations.watchdog")["heartbeat_count"] == 1


def test_unknown_component_keeps_overall_health_unknown():
    runtime = MemoryRuntime()
    watchdog = ReliabilityWatchdog(runtime, _config(), clock=Clock())

    snapshot = watchdog.heartbeat(
        {
            "scanner": {"status": "HEALTHY", "reason": "running"},
            "mt5_ipc": {"status": "UNKNOWN", "reason": "not observed"},
        }
    )

    assert snapshot["status"] == "UNKNOWN"
    assert snapshot["consecutive_failures"] == 1


def test_failure_notification_waits_for_threshold_and_is_deduplicated():
    runtime = MemoryRuntime()
    clock = Clock()
    notifications = []
    watchdog = ReliabilityWatchdog(runtime, _config(), clock=clock)
    watchdog.set_notifier(notifications.append)
    failed = _components("DEGRADED")

    first = watchdog.heartbeat(failed)
    clock.advance(1)
    second = watchdog.heartbeat(failed)
    clock.advance(1)
    third = watchdog.heartbeat(failed)

    assert first["notification_sent"] is False
    assert second["notification_sent"] is True
    assert third["notification_sent"] is False
    assert len(notifications) == 1
    assert runtime.get("operations.watchdog")["notifications_sent"] == 1


def test_unchanged_failure_does_not_repeat_after_cooldown_by_default():
    runtime = MemoryRuntime()
    clock = Clock()
    notifications = []
    watchdog = ReliabilityWatchdog(
        runtime,
        _config(failure_threshold=1, notification_cooldown_seconds=1),
        clock=clock,
    )
    watchdog.set_notifier(notifications.append)
    failed = _components("DEGRADED")

    first = watchdog.heartbeat(failed)
    clock.advance(2)
    second = watchdog.heartbeat(failed)

    assert first["notification_sent"] is True
    assert second["notification_sent"] is False
    assert len(notifications) == 1


def test_repeat_notifications_can_be_explicitly_enabled():
    runtime = MemoryRuntime()
    clock = Clock()
    notifications = []
    watchdog = ReliabilityWatchdog(
        runtime,
        _config(
            failure_threshold=1,
            notification_cooldown_seconds=1,
            repeat_notifications=True,
        ),
        clock=clock,
    )
    watchdog.set_notifier(notifications.append)
    failed = _components("DEGRADED")

    watchdog.heartbeat(failed)
    clock.advance(2)
    repeated = watchdog.heartbeat(failed)

    assert repeated["notification_sent"] is True
    assert len(notifications) == 2


def test_healthy_recovery_clears_failure_signature():
    runtime = MemoryRuntime()
    clock = Clock()
    watchdog = ReliabilityWatchdog(runtime, _config(failure_threshold=1), clock=clock)
    watchdog.heartbeat(_components("DEGRADED"))
    assert runtime.get("operations.watchdog")["last_failure_signature"]

    clock.advance(1)
    snapshot = watchdog.heartbeat(_components("HEALTHY"))

    assert snapshot["status"] == "HEALTHY"
    assert runtime.get("operations.watchdog")["last_failure_signature"] == ""


def test_instance_lock_only_reports_healthy_for_current_process(tmp_path):
    lock = tmp_path / "scanner.instance.lock"
    lock.write_text(str(os.getpid()), encoding="utf-8")
    watchdog = ReliabilityWatchdog(
        MemoryRuntime(),
        _config(),
        instance_lock_path=str(lock),
    )

    assert watchdog.check_instance_lock()["status"] == "HEALTHY"
    assert watchdog.check_instance_lock(current_process_owns_lock=True)["status"] == "HEALTHY"
    lock.write_text("999999", encoding="utf-8")
    assert watchdog.check_instance_lock()["status"] == "DEGRADED"


def test_configuration_change_is_reported_without_reloading_live_config(tmp_path):
    config_path = tmp_path / "atlax.yaml"
    config_path.write_text("version: one\n", encoding="utf-8")
    runtime = MemoryRuntime()
    watchdog = ReliabilityWatchdog(
        runtime,
        _config(config_path=str(config_path)),
        instance_lock_path=str(tmp_path / "lock"),
    )

    initial = watchdog.check_configuration(str(config_path))
    config_path.write_text("version: two with a different size\n", encoding="utf-8")
    changed = watchdog.check_configuration(str(config_path))

    assert initial["status"] == "HEALTHY"
    assert changed["status"] == "DEGRADED"
    assert changed["reason"] == "configuration_changed_requires_restart"


def test_restart_rebaselines_configuration_fingerprint(tmp_path):
    config_path = tmp_path / "atlax.yaml"
    config_path.write_text("version: one\n", encoding="utf-8")
    runtime = MemoryRuntime()
    first = ReliabilityWatchdog(
        runtime,
        _config(config_path=str(config_path)),
        instance_lock_path=str(tmp_path / "lock"),
    )
    first.check_configuration(str(config_path))
    config_path.write_text("version: two with a different size\n", encoding="utf-8")

    restarted = ReliabilityWatchdog(
        runtime,
        _config(config_path=str(config_path)),
        instance_lock_path=str(tmp_path / "lock"),
    )

    assert restarted.check_configuration(str(config_path))["status"] == "HEALTHY"


def test_watchdog_config_loader_requires_all_operational_thresholds(tmp_path):
    config_path = tmp_path / "atlax.yaml"
    config_path.write_text(
        """watchdog:
  enabled: true
  heartbeat_interval_seconds: 30
  failure_threshold: 3
  notification_cooldown_seconds: 300
  notification_ttl_seconds: 300
  market_data_stale_after_seconds: 180
  telegram_stale_after_seconds: 180
""",
        encoding="utf-8",
    )

    config = load_watchdog_config(config_path)

    assert config.enabled is True
    assert config.failure_threshold == 3
    assert config.notification_ttl_seconds == 300
    assert config.config_path == str(config_path)
