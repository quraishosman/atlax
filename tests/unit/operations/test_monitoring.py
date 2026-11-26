from core.operations.monitoring import OperationalMetricsLedger


class MemoryRuntime:
    def __init__(self):
        self.values = {}

    def get(self, key, default=None):
        return self.values.get(key, default)

    def set(self, key, value):
        self.values[key] = value


def test_operations_ledger_persists_delivery_counters_and_reasons():
    runtime = MemoryRuntime()
    first = OperationalMetricsLedger(runtime)
    first.observe_alert_suppressed("below_threshold(42.0<60)")
    first.observe_subscriber_suppressed("key_level_only")
    first.observe_subscriber_suppressed("key_level_only")
    first.observe_subscriber_delivered()
    first.observe_standalone_noise("VALID", candidate=True)
    first.observe_standalone_noise("UNKNOWN", candidate=False)
    first.observe_crt_quality_tightening(
        "VALID",
        would_suppress=True,
        reasons=("parent_body_below_threshold", "sweep_depth_below_threshold"),
    )
    first.observe_htf_momentum(
        "OPPOSING",
        run_length=2,
        base_score=58.0,
        proposed_score=48.0,
    )
    first.observe_account_mode_change("PROP_FIRM_ENFORCED")
    first.observe_account_mode_verification("UNKNOWN", "profile_stale")
    first.observe_prop_firm_blocked("PROP_FIRM_CONTEXT_UNKNOWN")
    first.observe_protection_context_refresh()
    first.observe_circuit_breaker("BLOCKED", "DAILY_LOSS_HIT")
    first.observe_circuit_breaker("BLOCKED", "DAILY_LOSS_HIT")
    first.observe_reconciliation("MATCHED")
    first.observe_reconciliation("CONFLICT")
    first.observe_prop_firm_profile("REVIEW_REQUIRED")

    second = OperationalMetricsLedger(runtime)
    report = second.report()

    assert report["alert_suppressed"] == 1
    assert report["alert_suppressed_by_reason"] == {"below_threshold(42.0<60)": 1}
    assert report["subscriber_delivery"]["suppressed"] == 2
    assert report["subscriber_delivery"]["suppressed_by_reason"] == {"key_level_only": 2}
    assert report["subscriber_delivery"]["delivered"] == 1
    assert report["standalone_noise"] == {
        "evaluated": 2,
        "unknown": 1,
        "candidate": 1,
        "would_suppress": 0,
    }
    assert report["crt_quality_tightening"] == {
        "evaluated": 1,
        "unknown": 0,
        "would_suppress": 1,
        "by_reason": {
            "parent_body_below_threshold": 1,
            "sweep_depth_below_threshold": 1,
        },
    }
    assert report["htf_momentum"]["opposing"] == 1
    assert report["htf_momentum"]["strong_runs"] == 1
    assert report["htf_momentum_scores"] == {
        "base_score_total": 58.0,
        "proposed_score_total": 48.0,
        "adjustment_total": -10.0,
    }
    assert report["updated_at"]
    assert report["account_mode"] == {
        "mode_changes": 1,
        "verification_failures": 1,
        "unknown_contexts": 1,
        "blocked_entries": 1,
        "resized_entries": 0,
        "protective_actions": 0,
        "protection_context_refreshes": 1,
        "by_reason": {
            "mode:PROP_FIRM_ENFORCED": 1,
            "profile_stale": 1,
            "PROP_FIRM_CONTEXT_UNKNOWN": 1,
        },
    }
    assert report["circuit_breaker"] == {
        "transitions": 2,
        "blocked": 2,
        "paused": 0,
        "unknown": 0,
        "by_reason": {"DAILY_LOSS_HIT": 2},
    }
    assert report["reconciliation"] == {
        "evaluated": 2,
        "blocked": 1,
        "matched": 1,
        "unknown": 0,
        "by_status": {"MATCHED": 1, "CONFLICT": 1},
    }
    assert report["prop_firm_profiles"] == {
        "evaluated": 1,
        "approved": 0,
        "stale": 0,
        "review_required": 1,
        "invalid": 0,
        "missing": 0,
        "unknown": 0,
        "by_status": {"REVIEW_REQUIRED": 1},
    }
