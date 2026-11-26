"""Import ATLAX local JSON/SQLite state into PostgreSQL.

The importer is intentionally idempotent. It never deletes local files and
does not activate PostgreSQL application writes by itself.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from core.settings.storage_config import load_storage_config
from core.storage.postgres import PostgresDatabase, PostgresUnavailable


def _json_file(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def _timestamp(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _decimal(value: Any) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value))
    except (TypeError, ValueError):
        return None


def _jsonb(value: Any):
    try:
        from psycopg.types.json import Jsonb
    except ImportError as exc:
        raise PostgresUnavailable(
            "Install psycopg[binary] before running the local data importer."
        ) from exc
    return Jsonb(value)


def _import_subscribers(connection, data_dir: Path) -> int:
    records = _json_file(data_dir / "subscribers.json", {})
    count = 0
    for chat_id, record in records.items():
        record = dict(record or {})
        expires_at = _timestamp(record.get("subscription_expires_at"))
        updated_at = _timestamp(record.get("updated_at")) or datetime.now(timezone.utc)
        connection.execute(
            """
            INSERT INTO subscribers (
                chat_id, username, active, subscription_status, style,
                subscription_expires_at, record, updated_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (chat_id) DO UPDATE SET
                username = EXCLUDED.username,
                active = EXCLUDED.active,
                subscription_status = EXCLUDED.subscription_status,
                style = EXCLUDED.style,
                subscription_expires_at = EXCLUDED.subscription_expires_at,
                record = EXCLUDED.record,
                updated_at = EXCLUDED.updated_at
            """,
            (
                str(chat_id),
                str(record.get("username", "")),
                bool(record.get("active", False)),
                str(record.get("subscription_status", "inactive")),
                str(record.get("style", "")),
                expires_at,
                _jsonb(record),
                updated_at,
            ),
        )
        count += 1
    return count


def _import_invite_codes(connection, data_dir: Path) -> int:
    records = _json_file(data_dir / "invite_codes.json", {})
    count = 0
    for code, value in records.items():
        record = {"code": code, **dict(value or {})}
        connection.execute(
            """
            INSERT INTO invite_codes (code, used_by, expires_at, record, updated_at)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (code) DO UPDATE SET
                used_by = EXCLUDED.used_by,
                expires_at = EXCLUDED.expires_at,
                record = EXCLUDED.record,
                updated_at = EXCLUDED.updated_at
            """,
            (
                str(code),
                record.get("used_by"),
                _timestamp(record.get("expires")),
                _jsonb(record),
                datetime.now(timezone.utc),
            ),
        )
        count += 1
    return count


def _import_payment_intents(connection, data_dir: Path) -> int:
    records = _json_file(data_dir / "payment_intents.json", {})
    count = 0
    for intent_id, record in records.items():
        record = dict(record or {})
        created_at = _timestamp(record.get("created_at")) or datetime.now(timezone.utc)
        updated_at = _timestamp(record.get("updated_at")) or created_at
        connection.execute(
            """
            INSERT INTO payment_intents (
                intent_id, reference, chat_id, status, method, style, amount_usd,
                created_at, expires_at, updated_at, record
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (intent_id) DO UPDATE SET
                reference = EXCLUDED.reference,
                chat_id = EXCLUDED.chat_id,
                status = EXCLUDED.status,
                method = EXCLUDED.method,
                style = EXCLUDED.style,
                amount_usd = EXCLUDED.amount_usd,
                created_at = EXCLUDED.created_at,
                expires_at = EXCLUDED.expires_at,
                updated_at = EXCLUDED.updated_at,
                record = EXCLUDED.record
            """,
            (
                str(record.get("intent_id", intent_id)),
                str(record.get("reference", intent_id)),
                str(record.get("chat_id", "")),
                str(record.get("status", "unknown")),
                str(record.get("method", "")),
                str(record.get("style", "")),
                _decimal(record.get("amount_usd")) or Decimal("0"),
                created_at,
                _timestamp(record.get("expires_at")),
                updated_at,
                _jsonb(record),
            ),
        )
        count += 1
    return count


def _import_users(connection, data_dir: Path) -> int:
    path = data_dir / "users.sqlite"
    if not path.exists():
        return 0
    count = 0
    with sqlite3.connect(path) as local:
        local.row_factory = sqlite3.Row
        for row in local.execute(
            "SELECT username, hashed_password, role, created_at FROM users"
        ):
            connection.execute(
                """
                INSERT INTO auth_users (username, hashed_password, role, created_at)
                VALUES (%s, %s, %s, COALESCE(%s, now()))
                ON CONFLICT (username) DO UPDATE SET
                    hashed_password = EXCLUDED.hashed_password,
                    role = EXCLUDED.role
                """,
                (
                    row["username"],
                    row["hashed_password"],
                    row["role"],
                    _timestamp(row["created_at"]),
                ),
            )
            count += 1
    return count


def _import_journal(connection, data_dir: Path) -> int:
    path = data_dir / "journal.sqlite"
    if not path.exists():
        return 0
    count = 0
    with sqlite3.connect(path) as local:
        local.row_factory = sqlite3.Row
        columns = {
            row["name"] for row in local.execute("PRAGMA table_info(journal_entries)")
        }
        rows = local.execute("SELECT * FROM journal_entries").fetchall()
        for row in rows:
            get = lambda name, default=None: row[name] if name in columns else default
            closed_at = _timestamp(get("closed_at")) or datetime.now(timezone.utc)
            key_level_evidence = get("key_level_evidence", {})
            if isinstance(key_level_evidence, str):
                try:
                    key_level_evidence = json.loads(key_level_evidence or "{}")
                except (TypeError, ValueError):
                    key_level_evidence = {}
            if not isinstance(key_level_evidence, dict):
                key_level_evidence = {}
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
                    key_level_explanation
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s
                )
                ON CONFLICT (record_id) DO UPDATE SET
                    candidate_id = EXCLUDED.candidate_id,
                    symbol = EXCLUDED.symbol,
                    timeframe = EXCLUDED.timeframe,
                    profile = EXCLUDED.profile,
                    strategy_name = EXCLUDED.strategy_name,
                    direction = EXCLUDED.direction,
                    confidence_score = EXCLUDED.confidence_score,
                    confidence_breakdown = EXCLUDED.confidence_breakdown,
                    lot_size = EXCLUDED.lot_size,
                    entry_price = EXCLUDED.entry_price,
                    sl_price = EXCLUDED.sl_price,
                    tp_price = EXCLUDED.tp_price,
                    exit_price = EXCLUDED.exit_price,
                    pnl = EXCLUDED.pnl,
                    closed_at = EXCLUDED.closed_at,
                    exit_reason = EXCLUDED.exit_reason,
                    tags = EXCLUDED.tags,
                    trader_notes = EXCLUDED.trader_notes,
                    signal_status = EXCLUDED.signal_status,
                    invalidation_reason = EXCLUDED.invalidation_reason,
                    final_outcome = EXCLUDED.final_outcome,
                    created_at = EXCLUDED.created_at,
                    outcome_at = EXCLUDED.outcome_at,
                    atr_method = EXCLUDED.atr_method,
                    atr_period = EXCLUDED.atr_period,
                    atr_value = EXCLUDED.atr_value,
                    parent_range = EXCLUDED.parent_range,
                    parent_atr_ratio = EXCLUDED.parent_atr_ratio,
                    sweep_depth = EXCLUDED.sweep_depth,
                    sweep_atr_ratio = EXCLUDED.sweep_atr_ratio,
                    atr_grade = EXCLUDED.atr_grade,
                    atr_configuration_snapshot_id = EXCLUDED.atr_configuration_snapshot_id,
                    atr_evaluation_status = EXCLUDED.atr_evaluation_status,
                    atr_explanation = EXCLUDED.atr_explanation,
                    key_level_status = EXCLUDED.key_level_status,
                    key_level_zone = EXCLUDED.key_level_zone,
                    key_level_match_count = EXCLUDED.key_level_match_count,
                    key_level_configuration_snapshot_id = EXCLUDED.key_level_configuration_snapshot_id,
                    key_level_evidence = EXCLUDED.key_level_evidence,
                    key_level_explanation = EXCLUDED.key_level_explanation
                """,
                (
                    str(get("record_id", "")),
                    str(get("candidate_id", "")),
                    str(get("symbol", "")),
                    str(get("timeframe", "")),
                    str(get("profile", "")),
                    str(get("strategy_name", "")),
                    str(get("direction", "")),
                    float(get("confidence_score") or 0),
                    _jsonb(json.loads(get("confidence_breakdown") or "{}")),
                    _decimal(get("lot_size")),
                    _decimal(get("entry_price")),
                    _decimal(get("sl_price")),
                    _decimal(get("tp_price")),
                    _decimal(get("exit_price")),
                    _decimal(get("pnl")),
                    closed_at,
                    str(get("exit_reason", "")),
                    _jsonb(json.loads(get("tags") or "[]")),
                    str(get("trader_notes", "")),
                    str(get("signal_status", "UNKNOWN")),
                    str(get("invalidation_reason", "")),
                    str(get("final_outcome", "UNKNOWN")),
                    _timestamp(get("created_at")) or closed_at,
                    _timestamp(get("outcome_at")),
                    str(get("atr_method", "")),
                    get("atr_period"),
                    _decimal(get("atr_value")),
                    _decimal(get("parent_range")),
                    _decimal(get("parent_atr_ratio")),
                    _decimal(get("sweep_depth")),
                    _decimal(get("sweep_atr_ratio")),
                    get("atr_grade"),
                    str(get("atr_configuration_snapshot_id", "")),
                    str(get("atr_evaluation_status", "NOT_APPLICABLE")),
                    str(get("atr_explanation", "")),
                    str(get("key_level_status", "NOT_APPLICABLE")),
                    get("key_level_zone"),
                    get("key_level_match_count"),
                    str(get("key_level_configuration_snapshot_id", "")),
                    _jsonb(key_level_evidence),
                    str(get("key_level_explanation", "")),
                ),
            )
            count += 1
    return count


def _import_audit(connection, data_dir: Path) -> int:
    path = data_dir / "admin_audit.jsonl"
    if not path.exists():
        return 0
    count = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        connection.execute(
            """
            INSERT INTO audit_events (
                event_hash, timestamp, actor_chat_id, action, target_chat_id,
                result, metadata
            ) VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (event_hash) DO NOTHING
            """,
            (
                hashlib.sha256(line.strip().encode("utf-8")).hexdigest(),
                _timestamp(record.get("timestamp")) or datetime.now(timezone.utc),
                str(record.get("actor_chat_id", "")),
                str(record.get("action", "")),
                str(record.get("target_chat_id", "")),
                str(record.get("result", "success")),
                _jsonb(record.get("metadata") or {}),
            ),
        )
        count += 1
    return count


def _import_runtime_state(connection, data_dir: Path) -> int:
    files = {
        "active_predictions": "active_predictions.json",
        "daily_stats": "daily_stats.json",
        "fvg_watches": "fvg_watches.json",
        "heatmap": "heatmap.json",
        "correlation": "correlation.json",
        "last_alerted": "last_alerted.json",
        "telegram_offset": "telegram_offset.json",
    }
    count = 0
    for state_key, filename in files.items():
        path = data_dir / filename
        if not path.exists():
            continue
        connection.execute(
            """
            INSERT INTO runtime_state (state_key, state_value, updated_at)
            VALUES (%s, %s, %s)
            ON CONFLICT (state_key) DO UPDATE SET
                state_value = EXCLUDED.state_value,
                updated_at = EXCLUDED.updated_at
            """,
            (
                state_key,
                _jsonb(_json_file(path, {})),
                datetime.now(timezone.utc),
            ),
        )
        count += 1
    return count


def migrate(data_dir: Path) -> dict[str, int]:
    config = load_storage_config()
    if not config.uses_postgres:
        raise ValueError(
            "Set ATLAX_STORAGE_BACKEND=postgres and ATLAX_DATABASE_URL before importing."
        )
    database = PostgresDatabase(config)
    database.initialize_schema()
    with database.connection() as connection:
        result = {
            "subscribers": _import_subscribers(connection, data_dir),
            "invite_codes": _import_invite_codes(connection, data_dir),
            "payment_intents": _import_payment_intents(connection, data_dir),
            "users": _import_users(connection, data_dir),
            "journal_entries": _import_journal(connection, data_dir),
            "audit_events": _import_audit(connection, data_dir),
            "runtime_state": _import_runtime_state(connection, data_dir),
        }
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--data-dir",
        default="data",
        help="Local ATLAX data directory to import (default: data)",
    )
    args = parser.parse_args()
    result = migrate(Path(args.data_dir).resolve())
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
