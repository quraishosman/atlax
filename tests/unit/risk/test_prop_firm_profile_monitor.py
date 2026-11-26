from datetime import datetime, timedelta, timezone
from decimal import Decimal

from core.models.prop_firm import (
    AccountReferenceBasis,
    LossLimitMode,
    LossLimitSpec,
    PropFirmRuleProfile,
)
from core.risk.prop_firm_profile_monitor import (
    ProfileMonitorStatus,
    PropFirmProfileMonitor,
)


NOW = datetime(2026, 9, 20, 12, 0, tzinfo=timezone.utc)


class MemoryRuntime:
    def __init__(self):
        self.values = {}

    def get(self, key, default=None):
        return self.values.get(key, default)

    def set(self, key, value):
        self.values[key] = value


def _profile(*, version="1.0.0", content_hash="hash", retrieved_at=None):
    return PropFirmRuleProfile(
        profile_id="example_phase2",
        firm_name="Example Firm",
        program_name="Example Program",
        phase="PHASE_2",
        account_type="STANDARD",
        currency="USD",
        overall_loss=LossLimitSpec(
            mode=LossLimitMode.STATIC,
            basis=AccountReferenceBasis.INITIAL_BALANCE,
            amount=Decimal("160"),
        ),
        daily_loss=None,
        source_urls=("https://example.test/rules",),
        source_retrieved_at=retrieved_at or NOW - timedelta(hours=1),
        version=version,
        content_hash=content_hash,
    )


def test_first_source_backed_profile_is_recorded_as_baseline_approved():
    runtime = MemoryRuntime()
    report = PropFirmProfileMonitor(
        runtime, (_profile(),), max_age_hours=24
    ).evaluate(now=NOW)

    entry = report.entry_for("example_phase2", "1.0.0")
    assert entry is not None
    assert entry.status == ProfileMonitorStatus.APPROVED
    assert entry.live_approved is True
    assert "baseline" in entry.reason.lower()


def test_changed_profile_requires_review_and_keeps_prior_approval():
    runtime = MemoryRuntime()
    monitor = PropFirmProfileMonitor(runtime, (_profile(),), max_age_hours=24)
    monitor.evaluate(now=NOW)

    changed = PropFirmProfileMonitor(
        runtime,
        (_profile(version="1.1.0", content_hash="changed"),),
        max_age_hours=24,
    ).evaluate(now=NOW)
    entry = changed.entry_for("example_phase2", "1.1.0")

    assert entry is not None
    assert entry.status == ProfileMonitorStatus.REVIEW_REQUIRED
    assert entry.live_approved is False
    assert entry.approved_version == "1.0.0"
    assert entry.approved_content_hash == "hash"


def test_stale_profile_is_not_live_approved():
    runtime = MemoryRuntime()
    monitor = PropFirmProfileMonitor(runtime, (_profile(),), max_age_hours=24)
    monitor.evaluate(now=NOW)

    stale = PropFirmProfileMonitor(
        runtime,
        (_profile(retrieved_at=NOW - timedelta(days=2)),),
        max_age_hours=24,
    ).evaluate(now=NOW)
    entry = stale.entry_for("example_phase2", "1.0.0")

    assert entry is not None
    assert entry.status == ProfileMonitorStatus.STALE
    assert entry.live_approved is False


def test_missing_profile_is_blocked_but_previous_approval_is_retained():
    runtime = MemoryRuntime()
    monitor = PropFirmProfileMonitor(runtime, (_profile(),), max_age_hours=24)
    monitor.evaluate(now=NOW)

    missing = PropFirmProfileMonitor(runtime, (), max_age_hours=24).evaluate(now=NOW)
    entry = missing.entry_for("example_phase2", "1.0.0")

    assert entry is not None
    assert entry.status == ProfileMonitorStatus.MISSING
    assert entry.approved_version == "1.0.0"
    assert missing.is_live_approved("example_phase2", "1.0.0") is False


def test_runtime_state_failure_returns_unknown_without_permissive_approval():
    class BrokenRuntime:
        def get(self, key, default=None):
            raise OSError("storage unavailable")

        def set(self, key, value):
            raise OSError("storage unavailable")

    report = PropFirmProfileMonitor(
        BrokenRuntime(), (_profile(),), max_age_hours=24
    ).evaluate(now=NOW)

    assert report.overall_status == ProfileMonitorStatus.UNKNOWN
    assert report.is_live_approved("example_phase2", "1.0.0") is False
