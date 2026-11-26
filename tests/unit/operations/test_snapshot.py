from core.operations.snapshot import build_operations_snapshot


def test_operations_snapshot_keeps_only_safe_account_binding_fields():
    snapshot = build_operations_snapshot(
        observed_at="2026-09-19T18:00:00+00:00",
        scanner={"running": True, "cycle": 4},
        account_mode={
            "mode": "AUTO_ENFORCED",
            "status": "VERIFIED",
            "status_reason": "ok",
            "selected_profile_id": "maven_standard_2step",
            "selected_profile_version": "1.1.0",
            "binding": {
                "login": 123,
                "server": "Broker",
                "currency": "USD",
                "account_class": "PROP_FIRM",
                "bound_by": "admin-secret-context",
            },
        },
        account={"balance": "2000"},
        profile={"profile_id": "maven_standard_2step"},
        loss_budget={"remaining_overall_buffer": "160"},
        health={"mt5": "CONNECTED"},
        metrics={"account_mode": {"blocked_entries": 0}},
    )

    assert snapshot["schema_version"] == 1
    assert snapshot["account_mode"]["binding"] == {
        "login": 123,
        "server": "Broker",
        "currency": "USD",
        "account_class": "PROP_FIRM",
    }
    assert "bound_by" not in snapshot["account_mode"]["binding"]
