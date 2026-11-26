"""Explicit service storage factories for the staged PostgreSQL cutover."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from core.settings.storage_config import StorageConfig, load_storage_config
from core.storage.postgres import PostgresUnavailable
from core.storage.repositories import PostgresRepositoryBundle, build_postgres_repositories


class LocalRuntimeStateStore:
    """Compatibility key/value store retaining ATLAX's existing local files."""

    _PATHS = {
        "telegram.offset": "data/telegram_offset.json",
        "heatmap.state": "data/heatmap.json",
        "correlation.state": "data/correlation.json",
        "correlation.closes": "data/correlation_closes.json",
        "signal_tracker.predictions": "data/active_predictions.json",
        "signal_tracker.daily_stats": "data/daily_stats.json",
        "scanner.last_alerted": "data/last_alerted.json",
        "execution.partial_profit_watches": "data/partial_profit_watches.json",
        "detector.fvg_watches": "data/fvg_watches.json",
    }

    def _path(self, key: str) -> Path:
        return Path(self._PATHS.get(key, f"data/runtime_{key.replace('.', '_')}.json"))

    def get(self, key: str, default: Any = None) -> Any:
        path = self._path(key)
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, json.JSONDecodeError):
            return default
        if key == "telegram.offset" and isinstance(value, dict):
            return value.get("last_update_id", default)
        return value

    def set(self, key: str, value: Any) -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"last_update_id": value} if key == "telegram.offset" else value
        path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def _postgres_bundle(config: StorageConfig | None = None) -> PostgresRepositoryBundle | None:
    selected = config or load_storage_config()
    if not selected.uses_postgres:
        return None
    if not selected.cutover_enabled:
        raise PostgresUnavailable(
            "PostgreSQL is selected but cutover is disabled. "
            "Set ATLAX_STORAGE_CUTOVER_ENABLED=true after migration verification."
        )
    return build_postgres_repositories(selected)


def get_subscriber_db(
    codes_file: str = "data/invite_codes.json",
    subs_file: str = "data/subscribers.json",
) -> Any:
    bundle = _postgres_bundle()
    if bundle is not None:
        from core.storage.service_adapters import PostgresSubscriberDB

        return PostgresSubscriberDB(bundle.subscribers, bundle.invite_codes)
    from core.alerts.subscriber_db import SubscriberDB

    return SubscriberDB(codes_file, subs_file)


def get_payment_store(path: str = "data/payment_intents.json") -> Any:
    bundle = _postgres_bundle()
    if bundle is not None:
        from core.storage.service_adapters import PostgresPaymentStore

        return PostgresPaymentStore(bundle.payments)
    from core.payments.payment_store import PaymentStore

    return PaymentStore(path)


def get_journal(db_path: str = "data/journal.sqlite") -> Any:
    bundle = _postgres_bundle()
    if bundle is not None:
        from core.storage.service_adapters import PostgresJournal

        return PostgresJournal(bundle.journal)
    from core.analytics.journal import SQLiteJournal

    return SQLiteJournal(db_path)


def get_replay_repository(
    db_path: str = "data/journal.sqlite",
    *,
    initialize: bool = True,
) -> Any:
    """Return the replay repository for the selected storage backend."""
    bundle = _postgres_bundle()
    if bundle is not None:
        return bundle.replay
    from core.storage.replay_repository import SQLiteReplaySnapshotRepository

    return SQLiteReplaySnapshotRepository(db_path, initialize=initialize)


def get_audit_log(path: str = "data/admin_audit.jsonl") -> Any:
    bundle = _postgres_bundle()
    if bundle is not None:
        from core.storage.service_adapters import PostgresAuditLog

        return PostgresAuditLog(bundle.audit)
    from core.logging.audit_log import OperationalAuditLog

    return OperationalAuditLog(path)


def get_approval_engine(approval_config: Any) -> Any:
    bundle = _postgres_bundle()
    if bundle is not None:
        from core.storage.service_adapters import PostgresApprovalEngine

        return PostgresApprovalEngine(bundle.approvals, approval_config)
    from core.approval.approval_engine import ApprovalEngine

    return ApprovalEngine(approval_config)


def get_runtime_state() -> Any:
    bundle = _postgres_bundle()
    if bundle is not None:
        from core.storage.service_adapters import PostgresRuntimeStateStore

        return PostgresRuntimeStateStore(bundle.runtime)
    return LocalRuntimeStateStore()
