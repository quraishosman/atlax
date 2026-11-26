"""Sanitized read-only operations data for administrator surfaces."""

from __future__ import annotations

from typing import Any


_MODE_LABELS = {
    "NORMAL_ACCOUNT": "Normal",
    "PROP_FIRM_SHADOW": "Shadow",
    "AUTO_ENFORCED": "Auto-Enforced",
    "BLOCKED": "Blocked",
}


def mode_label(value: Any) -> str:
    """Return a stable human-readable account-mode label."""

    raw = str(value or "UNKNOWN").upper()
    return _MODE_LABELS.get(raw, raw.replace("_", " ").title())


def _quality_event_view(event: Any) -> dict[str, Any]:
    """Whitelist operational fields; metadata is intentionally excluded."""

    if not isinstance(event, dict):
        return {}
    fields = (
        "event_id",
        "event_type",
        "execution_record_id",
        "profile",
        "symbol",
        "timeframe",
        "direction",
        "action",
        "status",
        "actual_price",
        "actual_volume",
        "broker_retcode",
        "latency_ms",
        "net_pnl",
        "reason",
        "occurred_at",
    )
    return {field: event.get(field) for field in fields}


def _audit_view(event: Any) -> dict[str, Any]:
    """Return the bounded, already-redacted audit fields used by the UI."""

    if not isinstance(event, dict):
        return {}
    return {
        "timestamp": event.get("timestamp"),
        "action": event.get("action"),
        "result": event.get("result"),
        "actor_chat_id": event.get("actor_chat_id"),
        "target_chat_id": event.get("target_chat_id"),
    }


def build_admin_operations_view(
    runtime_store: Any,
    journal: Any,
    audit: Any,
    *,
    limit: int = 20,
) -> dict[str, Any]:
    """Assemble a bounded read-only operations view from approved stores."""

    bounded_limit = max(1, min(int(limit), 50))
    snapshot = runtime_store.get("operations.snapshot", {})
    if not isinstance(snapshot, dict):
        snapshot = {}
    watchdog = runtime_store.get("operations.watchdog", {})
    if not isinstance(watchdog, dict):
        watchdog = {}
    health = snapshot.get("health")
    if not isinstance(health, dict):
        health = {}

    quality_events = journal.list_execution_quality_events(limit=bounded_limit)
    audit_events = audit.recent(bounded_limit)
    mode = snapshot.get("account_mode")
    if not isinstance(mode, dict):
        mode = {}
    breaker = snapshot.get("circuit_breaker")
    if not isinstance(breaker, dict):
        breaker = runtime_store.get("operations.circuit_breaker", {}) or {}
    reconciliation = snapshot.get("reconciliation")
    if not isinstance(reconciliation, dict):
        reconciliation = runtime_store.get("operations.reconciliation", {}) or {}
    metrics = snapshot.get("metrics")
    if not isinstance(metrics, dict):
        metrics = runtime_store.get("operations.metrics", {}) or {}

    return {
        "schema_version": 1,
        "snapshot_available": bool(snapshot),
        "observed_at": snapshot.get("observed_at"),
        "mode": {
            "raw": mode.get("mode", "UNKNOWN"),
            "label": mode_label(mode.get("mode", "UNKNOWN")),
            "status": mode.get("status", "UNKNOWN"),
            "status_reason": mode.get("status_reason", ""),
        },
        "snapshot": snapshot,
        "health": health,
        "watchdog": {
            "status": watchdog.get("status", "UNKNOWN"),
            "last_heartbeat_at": watchdog.get("last_heartbeat_at"),
            "consecutive_failures": watchdog.get("consecutive_failures", 0),
            "components": watchdog.get("components", {}),
        },
        "circuit_breaker": breaker,
        "reconciliation": reconciliation,
        "metrics": metrics,
        "execution_quality": [
            item for item in (_quality_event_view(event) for event in quality_events)
            if item
        ],
        "audit": [
            item for item in (_audit_view(event) for event in audit_events)
            if item
        ],
    }


__all__ = ["build_admin_operations_view", "mode_label"]
