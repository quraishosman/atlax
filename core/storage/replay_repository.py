"""Local replay snapshot persistence.

The repository stores validated Signal Replay snapshots beside the existing
local journal. It is deliberately independent from detector and Telegram code.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from core.models.signal_replay import (
    ReplayLifecycleStatus,
    ReplaySnapshot,
)


class SQLiteReplaySnapshotRepository:
    """Persist immutable formation evidence and append-only outcome evidence."""

    _TERMINAL_STATUSES = {
        ReplayLifecycleStatus.RESOLVED,
        ReplayLifecycleStatus.EXPIRED,
    }

    def __init__(self, db_path: str, *, initialize: bool = True) -> None:
        self.db_path = str(db_path)
        self._read_only = not initialize
        if initialize:
            parent = Path(self.db_path).parent
            if str(parent) not in {"", "."}:
                parent.mkdir(parents=True, exist_ok=True)
            self._initialize()

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        if self._read_only:
            connection = sqlite3.connect(
                f"{Path(self.db_path).resolve().as_uri()}?mode=ro",
                uri=True,
            )
        else:
            connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._connection() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS signal_replay_snapshots (
                    snapshot_id TEXT NOT NULL UNIQUE,
                    signal_id TEXT NOT NULL,
                    snapshot_version TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    source_timeframe TEXT NOT NULL,
                    source TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    captured_at TEXT NOT NULL,
                    formation_cutoff_at TEXT NOT NULL,
                    context_bars_before_parent INTEGER NOT NULL,
                    outcome_forward_bars INTEGER NOT NULL,
                    formation_context TEXT NOT NULL,
                    higher_timeframe_context TEXT NOT NULL,
                    outcome_context TEXT NOT NULL,
                    rule_context TEXT NOT NULL,
                    snapshot_hash TEXT NOT NULL,
                    availability TEXT NOT NULL,
                    lifecycle_status TEXT NOT NULL,
                    final_outcome TEXT NOT NULL,
                    outcome_at TEXT,
                    invalidation_reason TEXT NOT NULL DEFAULT '',
                    failure_reason TEXT NOT NULL DEFAULT '',
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (signal_id, snapshot_version)
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_replay_signal
                ON signal_replay_snapshots (signal_id, updated_at DESC)
                """
            )

    @staticmethod
    def _json(value: object) -> str:
        return json.dumps(value, sort_keys=True, separators=(",", ":"))

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _payload(snapshot: ReplaySnapshot) -> dict[str, object]:
        return snapshot.to_payload()

    @classmethod
    def _row_payload(cls, row: sqlite3.Row) -> dict[str, object]:
        def load_json(name: str) -> object:
            try:
                return json.loads(row[name])
            except (TypeError, json.JSONDecodeError) as exc:
                raise ValueError(f"invalid stored replay JSON: {name}") from exc

        return {
            "snapshot_id": row["snapshot_id"],
            "signal_id": row["signal_id"],
            "snapshot_version": row["snapshot_version"],
            "symbol": row["symbol"],
            "source_timeframe": row["source_timeframe"],
            "source": row["source"],
            "created_at": row["created_at"],
            "captured_at": row["captured_at"],
            "formation_cutoff_at": row["formation_cutoff_at"],
            "context_bars_before_parent": row["context_bars_before_parent"],
            "outcome_forward_bars": row["outcome_forward_bars"],
            "formation_candles": load_json("formation_context"),
            "higher_timeframe_candles": load_json("higher_timeframe_context"),
            "outcome_candles": load_json("outcome_context"),
            "rule_context": load_json("rule_context"),
            "snapshot_hash": row["snapshot_hash"],
            "availability": row["availability"],
            "lifecycle_status": row["lifecycle_status"],
            "final_outcome": row["final_outcome"],
            "outcome_at": row["outcome_at"],
            "invalidation_reason": row["invalidation_reason"],
            "failure_reason": row["failure_reason"],
        }

    @classmethod
    def _to_snapshot(cls, row: sqlite3.Row | None) -> ReplaySnapshot | None:
        if row is None:
            return None
        return ReplaySnapshot.from_payload(cls._row_payload(row))

    def get(
        self,
        signal_id: str,
        snapshot_version: str | None = None,
    ) -> ReplaySnapshot | None:
        query = """
            SELECT * FROM signal_replay_snapshots
            WHERE signal_id = ?
        """
        params: list[str] = [str(signal_id)]
        if snapshot_version is not None:
            query += " AND snapshot_version = ?"
            params.append(str(snapshot_version))
        query += " ORDER BY updated_at DESC LIMIT 1"
        with self._connection() as connection:
            row = connection.execute(query, tuple(params)).fetchone()
        return self._to_snapshot(row)

    def list(self) -> list[ReplaySnapshot]:
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT * FROM signal_replay_snapshots
                ORDER BY created_at DESC, signal_id
                """
            ).fetchall()
        return [self._to_snapshot(row) for row in rows if row is not None]

    def upsert(self, snapshot: ReplaySnapshot) -> ReplaySnapshot:
        """Insert once; identical retries return the stored snapshot."""
        if not isinstance(snapshot, ReplaySnapshot):
            raise TypeError("upsert requires a ReplaySnapshot")
        payload = self._payload(snapshot)
        with self._connection() as connection:
            existing = connection.execute(
                """
                SELECT * FROM signal_replay_snapshots
                WHERE signal_id = ? AND snapshot_version = ?
                """,
                (snapshot.signal_id, snapshot.snapshot_version),
            ).fetchone()
            if existing is not None:
                if existing["snapshot_hash"] != snapshot.snapshot_hash:
                    raise ValueError("immutable replay snapshot conflict")
                return self._to_snapshot(existing)  # type: ignore[return-value]

            try:
                connection.execute(
                    """
                    INSERT INTO signal_replay_snapshots (
                        snapshot_id, signal_id, snapshot_version, symbol,
                        source_timeframe, source, created_at, captured_at,
                        formation_cutoff_at, context_bars_before_parent,
                        outcome_forward_bars, formation_context,
                        higher_timeframe_context, outcome_context, rule_context,
                        snapshot_hash, availability, lifecycle_status,
                        final_outcome, outcome_at, invalidation_reason,
                        failure_reason, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                              ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        payload["snapshot_id"],
                        payload["signal_id"],
                        payload["snapshot_version"],
                        payload["symbol"],
                        payload["source_timeframe"],
                        payload["source"],
                        payload["created_at"],
                        payload["captured_at"],
                        payload["formation_cutoff_at"],
                        payload["context_bars_before_parent"],
                        payload["outcome_forward_bars"],
                        self._json(payload["formation_candles"]),
                        self._json(payload["higher_timeframe_candles"]),
                        self._json(payload["outcome_candles"]),
                        self._json(payload["rule_context"]),
                        payload["snapshot_hash"],
                        payload["availability"],
                        payload["lifecycle_status"],
                        payload["final_outcome"],
                        payload["outcome_at"],
                        payload["invalidation_reason"],
                        payload["failure_reason"],
                        self._now(),
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise ValueError("replay snapshot identity conflict") from exc
        return snapshot

    def update_outcome(self, snapshot: ReplaySnapshot) -> ReplaySnapshot:
        """Advance only outcome fields while preserving formation evidence."""
        if not isinstance(snapshot, ReplaySnapshot):
            raise TypeError("update_outcome requires a ReplaySnapshot")
        current = self.get(snapshot.signal_id, snapshot.snapshot_version)
        if current is None:
            raise KeyError(f"replay snapshot not found: {snapshot.signal_id}")
        if current.snapshot_hash != snapshot.snapshot_hash:
            raise ValueError("formation evidence cannot be changed")
        current_outcomes = [item.to_payload() for item in current.outcome_candles]
        next_outcomes = [item.to_payload() for item in snapshot.outcome_candles]
        if next_outcomes[: len(current_outcomes)] != current_outcomes:
            raise ValueError("outcome candles are append-only")
        if current.lifecycle_status in self._TERMINAL_STATUSES:
            if current.to_payload()["outcome_candles"] != snapshot.to_payload()["outcome_candles"]:
                raise ValueError("terminal replay outcome cannot be changed")
            if (
                current.lifecycle_status != snapshot.lifecycle_status
                or current.final_outcome != snapshot.final_outcome
            ):
                raise ValueError("terminal replay outcome cannot be changed")
            return current

        with self._connection() as connection:
            connection.execute(
                """
                UPDATE signal_replay_snapshots
                SET outcome_context = ?, lifecycle_status = ?, final_outcome = ?,
                    outcome_at = ?, invalidation_reason = ?, updated_at = ?
                WHERE signal_id = ? AND snapshot_version = ?
                """,
                (
                    self._json(next_outcomes),
                    snapshot.lifecycle_status.value,
                    snapshot.final_outcome.value,
                    snapshot.outcome_at.isoformat() if snapshot.outcome_at else None,
                    snapshot.invalidation_reason,
                    self._now(),
                    snapshot.signal_id,
                    snapshot.snapshot_version,
                ),
            )
        return snapshot
