from dataclasses import replace
from datetime import datetime, timezone

import pytest

from core.models.signal_replay import ReplayLifecycleStatus, ReplayOutcome
from core.storage.replay_repository import SQLiteReplaySnapshotRepository
from core.storage.repositories import (
    PostgresReplaySnapshotRepository,
    build_postgres_repositories,
)
from core.settings.storage_config import StorageConfig
from tests.unit.analytics.test_signal_replay import _snapshot


def test_local_repository_round_trip_and_idempotency(tmp_path):
    repository = SQLiteReplaySnapshotRepository(str(tmp_path / "journal.sqlite"))
    snapshot = _snapshot()

    first = repository.upsert(snapshot)
    second = repository.upsert(snapshot)
    loaded = repository.get(snapshot.signal_id, snapshot.snapshot_version)

    assert first.to_payload() == snapshot.to_payload()
    assert second.to_payload() == snapshot.to_payload()
    assert loaded is not None
    assert loaded.to_payload() == snapshot.to_payload()
    assert len(repository.list()) == 1


def test_local_repository_rejects_formation_conflict(tmp_path):
    repository = SQLiteReplaySnapshotRepository(str(tmp_path / "journal.sqlite"))
    repository.upsert(_snapshot())
    changed = replace(_snapshot(), rule_context={"rulebook_version": "changed"})

    with pytest.raises(ValueError, match="immutable replay snapshot conflict"):
        repository.upsert(changed)


def test_local_repository_updates_only_outcome_fields(tmp_path):
    repository = SQLiteReplaySnapshotRepository(str(tmp_path / "journal.sqlite"))
    pending = _snapshot(outcome_candles=())
    repository.upsert(pending)

    resolved = replace(
        pending,
        outcome_candles=_snapshot().outcome_candles,
        lifecycle_status=ReplayLifecycleStatus.RESOLVED,
        final_outcome=ReplayOutcome.WIN,
        outcome_at=datetime(2026, 8, 17, 17, 0, tzinfo=timezone.utc),
    )
    updated = repository.update_outcome(resolved)

    assert updated.snapshot_hash == pending.snapshot_hash
    assert repository.get(pending.signal_id).final_outcome is ReplayOutcome.WIN

    changed_terminal = replace(
        resolved,
        final_outcome=ReplayOutcome.LOSS,
        invalidation_reason="stop_loss_hit",
    )
    with pytest.raises(ValueError, match="terminal replay outcome cannot be changed"):
        repository.update_outcome(changed_terminal)


def test_postgres_bundle_exposes_replay_repository_without_connecting():
    config = StorageConfig(
        backend="postgres",
        database_url="postgresql://user:password@localhost/atlax",
        cutover_enabled=True,
    )
    bundle = build_postgres_repositories(config)

    assert isinstance(bundle.replay, PostgresReplaySnapshotRepository)


def test_postgres_schema_contains_replay_snapshot_contract():
    schema = open("db/postgres/schema.sql", encoding="utf-8").read()

    assert "CREATE TABLE IF NOT EXISTS signal_replay_snapshots" in schema
    assert "formation_context JSONB NOT NULL" in schema
    assert "PRIMARY KEY (signal_id, snapshot_version)" in schema
    assert "idx_replay_signal" in schema

