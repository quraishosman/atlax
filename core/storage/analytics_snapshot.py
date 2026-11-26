"""Read-only journal snapshots for analytics and MCP consumers."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from typing import Iterator

from core.settings.storage_config import load_storage_config
from core.storage.postgres import PostgresUnavailable
from core.storage.repositories import build_postgres_repositories


@contextmanager
def connect_journal(db_path: str) -> Iterator[sqlite3.Connection]:
    """Expose a local SQLite journal or an in-memory PostgreSQL read snapshot."""
    config = load_storage_config()
    if not config.uses_postgres:
        connection = sqlite3.connect(db_path)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
        finally:
            connection.close()
        return

    if not config.cutover_enabled:
        raise PostgresUnavailable(
            "Analytics storage cannot read PostgreSQL while cutover is disabled."
        )

    records = build_postgres_repositories(config).journal.list()
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.execute(
        """
        CREATE TABLE journal_entries (
            record_id TEXT, candidate_id TEXT, symbol TEXT, timeframe TEXT,
            profile TEXT, strategy_name TEXT, direction TEXT,
            confidence_score REAL, confidence_breakdown TEXT, lot_size TEXT,
            entry_price TEXT, sl_price TEXT, tp_price TEXT, exit_price TEXT,
            pnl TEXT, closed_at TEXT, exit_reason TEXT, tags TEXT,
            trader_notes TEXT, signal_status TEXT, invalidation_reason TEXT,
            final_outcome TEXT, created_at TEXT, outcome_at TEXT,
            atr_method TEXT, atr_period INTEGER, atr_value TEXT,
            parent_range TEXT, parent_atr_ratio TEXT, sweep_depth TEXT,
            sweep_atr_ratio TEXT, atr_grade TEXT,
            atr_configuration_snapshot_id TEXT,
            atr_evaluation_status TEXT, atr_explanation TEXT,
            key_level_status TEXT, key_level_zone INTEGER,
            key_level_match_count INTEGER,
            key_level_configuration_snapshot_id TEXT,
            key_level_evidence TEXT, key_level_explanation TEXT,
            volume_profile_status TEXT, volume_profile_period TEXT,
            volume_profile_source_timeframe TEXT, volume_profile_volume_source TEXT,
            volume_profile_volume_quality TEXT, volume_profile_poc TEXT,
            volume_profile_vah TEXT, volume_profile_val TEXT,
            volume_profile_total_volume TEXT, volume_profile_source_bars TEXT,
            volume_profile_configuration_snapshot_id TEXT,
            volume_profile_evidence TEXT, volume_profile_unknown_reason TEXT
        )
        """
    )
    for record in records:
        connection.execute(
            """
            INSERT INTO journal_entries (
                record_id, candidate_id, symbol, timeframe, profile,
                strategy_name, direction, confidence_score,
                confidence_breakdown, lot_size, entry_price, sl_price,
                tp_price, exit_price, pnl, closed_at, exit_reason, tags,
                trader_notes, signal_status, invalidation_reason,
                final_outcome, created_at, outcome_at, atr_method, atr_period,
                atr_value, parent_range, parent_atr_ratio, sweep_depth,
                sweep_atr_ratio, atr_grade, atr_configuration_snapshot_id,
                atr_evaluation_status, atr_explanation, key_level_status,
                key_level_zone, key_level_match_count,
                key_level_configuration_snapshot_id, key_level_evidence,
                key_level_explanation, volume_profile_status, volume_profile_period,
                volume_profile_source_timeframe, volume_profile_volume_source,
                volume_profile_volume_quality, volume_profile_poc, volume_profile_vah,
                volume_profile_val, volume_profile_total_volume, volume_profile_source_bars,
                volume_profile_configuration_snapshot_id, volume_profile_evidence,
                volume_profile_unknown_reason
            ) VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?, ?, ?
            )
            """,
            (
                record.get("record_id"), record.get("candidate_id"), record.get("symbol"),
                record.get("timeframe"), record.get("profile"), record.get("strategy_name"),
                record.get("direction"), float(record.get("confidence_score") or 0),
                json.dumps(record.get("confidence_breakdown") or {}),
                record.get("lot_size") or "0", record.get("entry_price") or "0",
                record.get("sl_price") or "0", record.get("tp_price") or "0",
                record.get("exit_price") or "0", record.get("pnl") or "0",
                record.get("closed_at"), record.get("exit_reason"),
                json.dumps(record.get("tags") or []), record.get("trader_notes", ""),
                record.get("signal_status", "UNKNOWN"), record.get("invalidation_reason", ""),
                record.get("final_outcome", "UNKNOWN"), record.get("created_at"),
                record.get("outcome_at"),
                record.get("atr_method", ""), record.get("atr_period"),
                record.get("atr_value"), record.get("parent_range"),
                record.get("parent_atr_ratio"), record.get("sweep_depth"),
                record.get("sweep_atr_ratio"), record.get("atr_grade"),
                record.get("atr_configuration_snapshot_id", ""),
                record.get("atr_evaluation_status", "NOT_APPLICABLE"),
                record.get("atr_explanation", ""),
                record.get("key_level_status", "NOT_APPLICABLE"),
                record.get("key_level_zone"),
                record.get("key_level_match_count"),
                record.get("key_level_configuration_snapshot_id", ""),
                record.get("key_level_evidence", "{}")
                if isinstance(record.get("key_level_evidence", "{}"), str)
                else json.dumps(record.get("key_level_evidence") or {}),
                record.get("key_level_explanation", ""),
                record.get("volume_profile_status", "NOT_APPLICABLE"),
                record.get("volume_profile_period"),
                record.get("volume_profile_source_timeframe"),
                record.get("volume_profile_volume_source"),
                record.get("volume_profile_volume_quality"),
                record.get("volume_profile_poc"),
                record.get("volume_profile_vah"),
                record.get("volume_profile_val"),
                record.get("volume_profile_total_volume"),
                record.get("volume_profile_source_bars"),
                record.get("volume_profile_configuration_snapshot_id", ""),
                record.get("volume_profile_evidence", "{}")
                if isinstance(record.get("volume_profile_evidence", "{}"), str)
                else json.dumps(record.get("volume_profile_evidence") or {}),
                record.get("volume_profile_unknown_reason"),
            ),
        )
    connection.commit()
    try:
        yield connection
    finally:
        connection.close()
