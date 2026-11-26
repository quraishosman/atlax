"""Read-only, secret-free operations snapshot assembly."""

from __future__ import annotations

from typing import Any


def build_operations_snapshot(
    *,
    observed_at: str,
    scanner: dict[str, Any],
    account_mode: dict[str, Any],
    account: dict[str, Any] | None,
    profile: dict[str, Any] | None,
    loss_budget: dict[str, Any] | None,
    health: dict[str, Any],
    metrics: dict[str, Any] | None,
    circuit_breaker: dict[str, Any] | None = None,
    reconciliation: dict[str, Any] | None = None,
    prop_firm_profiles: dict[str, Any] | None = None,
    watchdog: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the stable Phase 1 snapshot contract from sanitized inputs."""

    binding = account_mode.get("binding") if isinstance(account_mode, dict) else None
    safe_mode = {
        "mode": str(account_mode.get("mode", "UNKNOWN")),
        "status": str(account_mode.get("status", "UNKNOWN")),
        "status_reason": str(account_mode.get("status_reason", ""))[:240],
        "selected_profile_id": str(account_mode.get("selected_profile_id", "")),
        "selected_profile_version": str(account_mode.get("selected_profile_version", "")),
        "binding": {
            "login": binding.get("login"),
            "server": str(binding.get("server", "")),
            "currency": str(binding.get("currency", "")),
            "account_class": str(binding.get("account_class", "")),
        } if isinstance(binding, dict) else None,
    }
    return {
        "schema_version": 1,
        "observed_at": str(observed_at),
        "scanner": dict(scanner),
        "account_mode": safe_mode,
        "account": dict(account) if isinstance(account, dict) else None,
        "profile": dict(profile) if isinstance(profile, dict) else None,
        "loss_budget": dict(loss_budget) if isinstance(loss_budget, dict) else None,
        "health": dict(health),
        "metrics": dict(metrics) if isinstance(metrics, dict) else {},
        "circuit_breaker": (
            dict(circuit_breaker) if isinstance(circuit_breaker, dict) else {}
        ),
        "reconciliation": (
            dict(reconciliation) if isinstance(reconciliation, dict) else {}
        ),
        "prop_firm_profiles": (
            dict(prop_firm_profiles) if isinstance(prop_firm_profiles, dict) else {}
        ),
        "watchdog": (
            dict(watchdog) if isinstance(watchdog, dict) else {}
        ),
    }
