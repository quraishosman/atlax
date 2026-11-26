"""Read-only local/PostgreSQL comparison for the ATLAX migration.

This command never initializes or mutates PostgreSQL.  It reads a frozen local
snapshot and the selected PostgreSQL database through approved repositories,
then reports counts, key coverage, and canonical fingerprints.  It does not
print record bodies.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from core.settings.storage_config import load_storage_config
from core.storage.frozen_snapshot import verify_snapshot
from core.storage.postgres import PostgresDatabase
from core.storage.repositories import PostgresRepositoryBundle
from core.models.signal_replay import ReplaySnapshot


_RUNTIME_FILES = {
    "active_predictions": "active_predictions.json",
    "daily_stats": "daily_stats.json",
    "fvg_watches": "fvg_watches.json",
    "heatmap": "heatmap.json",
    "correlation": "correlation.json",
    "last_alerted": "last_alerted.json",
    "telegram_offset": "telegram_offset.json",
}


def _json_file(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def _canonical(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _canonical(value[key]) for key in sorted(value)}
    if isinstance(value, (list, tuple)):
        return [_canonical(item) for item in value]
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, float):
        return round(value, 12)
    return str(value) if value is not None and not isinstance(value, (str, int, bool)) else value


def _fingerprint(value: Any) -> str:
    payload = json.dumps(_canonical(value), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _record_map(items: Iterable[tuple[str, Any]]) -> dict[str, Any]:
    return {str(key): value for key, value in items}


def _local_json_records(data_dir: Path, filename: str) -> dict[str, dict[str, Any]]:
    raw = _json_file(data_dir / filename, {})
    if not isinstance(raw, dict):
        return {}
    result: dict[str, dict[str, Any]] = {}
    for key, value in raw.items():
        record = dict(value) if isinstance(value, dict) else {}
        result[str(record.get("chat_id", key) if filename == "subscribers.json" else key)] = record
    return result


def _local_users(data_dir: Path) -> dict[str, dict[str, Any]]:
    path = data_dir / "users.sqlite"
    if not path.exists():
        return {}
    with sqlite3.connect(path) as connection:
        rows = connection.execute(
            "SELECT username, hashed_password, role, created_at FROM users"
        ).fetchall()
    return _record_map(
        (
            str(row[0]),
            {
                "username": row[0],
                "hashed_password": row[1],
                "role": row[2],
                "created_at": row[3],
            },
        )
        for row in rows
    )


def _local_journal(data_dir: Path) -> dict[str, dict[str, Any]]:
    path = data_dir / "journal.sqlite"
    if not path.exists():
        return {}
    with sqlite3.connect(path) as connection:
        connection.row_factory = sqlite3.Row
        columns = {row["name"] for row in connection.execute("PRAGMA table_info(journal_entries)")}
        rows = connection.execute("SELECT * FROM journal_entries").fetchall()
    result = {}
    for row in rows:
        def get(name: str, default: Any = None) -> Any:
            return row[name] if name in columns else default

        record = {
            "record_id": get("record_id", ""),
            "candidate_id": get("candidate_id", ""),
            "symbol": get("symbol", ""),
            "timeframe": get("timeframe", ""),
            "profile": get("profile", ""),
            "strategy_name": get("strategy_name", ""),
            "direction": get("direction", ""),
            "confidence_score": get("confidence_score", 0),
            "confidence_breakdown": json.loads(get("confidence_breakdown", "{}") or "{}"),
            "lot_size": get("lot_size"),
            "entry_price": get("entry_price"),
            "sl_price": get("sl_price"),
            "tp_price": get("tp_price"),
            "exit_price": get("exit_price"),
            "pnl": get("pnl"),
            "closed_at": get("closed_at"),
            "exit_reason": get("exit_reason", ""),
            "tags": json.loads(get("tags", "[]") or "[]"),
            "trader_notes": get("trader_notes", ""),
            "signal_status": get("signal_status", "UNKNOWN"),
            "invalidation_reason": get("invalidation_reason", ""),
            "final_outcome": get("final_outcome", "UNKNOWN"),
            "created_at": get("created_at"),
            "outcome_at": get("outcome_at"),
        }
        result[str(record["record_id"])] = record
    return result


def _local_replay_snapshots(data_dir: Path) -> dict[str, dict[str, Any]]:
    """Read replay rows without creating or mutating the local schema."""
    path = data_dir / "journal.sqlite"
    if not path.exists():
        return {}
    with sqlite3.connect(path) as connection:
        connection.row_factory = sqlite3.Row
        table = connection.execute(
            """
            SELECT name FROM sqlite_master
            WHERE type = 'table' AND name = 'signal_replay_snapshots'
            """
        ).fetchone()
        if table is None:
            return {}
        rows = connection.execute(
            "SELECT * FROM signal_replay_snapshots"
        ).fetchall()

    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        def load_json(name: str) -> Any:
            value = row[name]
            return json.loads(value) if isinstance(value, str) else value

        payload = {
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
        snapshot = ReplaySnapshot.from_payload(payload)
        key = f"{snapshot.signal_id}:{snapshot.snapshot_version}"
        result[key] = snapshot.to_payload()
    return result


def _local_audit(data_dir: Path) -> dict[str, dict[str, Any]]:
    path = data_dir / "admin_audit.jsonl"
    if not path.exists():
        return {}
    result = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        event_hash = hashlib.sha256(line.strip().encode("utf-8")).hexdigest()
        result[event_hash] = {
            "event_hash": event_hash,
            "timestamp": record.get("timestamp"),
            "actor_chat_id": record.get("actor_chat_id", ""),
            "action": record.get("action", ""),
            "target_chat_id": record.get("target_chat_id", ""),
            "result": record.get("result", "success"),
            "metadata": record.get("metadata") or {},
        }
    return result


def local_snapshot(data_dir: Path) -> dict[str, dict[str, Any]]:
    runtime = {}
    for state_key, filename in _RUNTIME_FILES.items():
        path = data_dir / filename
        if path.exists():
            runtime[state_key] = _json_file(path, {})
    return {
        "subscribers": _local_json_records(data_dir, "subscribers.json"),
        "invite_codes": _local_json_records(data_dir, "invite_codes.json"),
        "payment_intents": _local_json_records(data_dir, "payment_intents.json"),
        "users": _local_users(data_dir),
        "journal_entries": _local_journal(data_dir),
        "replay_snapshots": _local_replay_snapshots(data_dir),
        "audit_events": _local_audit(data_dir),
        "runtime_state": runtime,
    }


def postgres_snapshot(bundle: PostgresRepositoryBundle) -> dict[str, dict[str, Any]]:
    return {
        "subscribers": _record_map(
            (str(item.get("chat_id", "")), item) for item in bundle.subscribers.list()
        ),
        "invite_codes": _record_map(
            (str(item.get("code", "")), item) for item in bundle.invite_codes.list()
        ),
        "payment_intents": _record_map(
            (str(item.get("intent_id", "")), item) for item in bundle.payments.list()
        ),
        "users": _record_map(
            (str(item.get("username", "")), item) for item in bundle.auth.list()
        ),
        "journal_entries": _record_map(
            (str(item.get("record_id", "")), item) for item in bundle.journal.list()
        ),
        "replay_snapshots": _record_map(
            (
                f"{item.signal_id}:{item.snapshot_version}",
                item.to_payload(),
            )
            for item in bundle.replay.list()
        ),
        "audit_events": _record_map(
            (str(item.get("event_hash", "")), item) for item in bundle.audit.list_all()
        ),
        "runtime_state": bundle.runtime.list(),
    }


def _compare(name: str, local: dict[str, Any], remote: dict[str, Any]) -> dict[str, Any]:
    local_keys = set(local)
    remote_keys = set(remote)
    shared = local_keys & remote_keys
    mismatches = [key for key in sorted(shared) if _fingerprint(local[key]) != _fingerprint(remote[key])]
    missing_remote = sorted(local_keys - remote_keys)
    missing_local = sorted(remote_keys - local_keys)
    status = "MATCH" if not missing_remote and not missing_local and not mismatches else "MISMATCH"
    return {
        "local": len(local_keys),
        "postgres": len(remote_keys),
        "shared": len(shared),
        "missing_in_postgres": missing_remote[:20],
        "missing_in_local": missing_local[:20],
        "fingerprint_mismatches": mismatches[:20],
        "status": status,
    }


def compare(data_dir: Path, *, snapshot_dir: Path | None = None) -> dict[str, Any]:
    config = load_storage_config()
    if not config.uses_postgres:
        raise ValueError(
            "Set ATLAX_STORAGE_BACKEND=postgres and ATLAX_DATABASE_URL before dual-read verification."
        )
    database = PostgresDatabase(config)
    if not database.healthcheck():
        raise RuntimeError("PostgreSQL healthcheck failed; comparison did not run.")
    snapshot_evidence = None
    local_source = snapshot_dir or data_dir
    if snapshot_dir is not None:
        snapshot_evidence = verify_snapshot(snapshot_dir)
    local = local_snapshot(local_source)
    remote = postgres_snapshot(PostgresRepositoryBundle(database))
    comparisons = {
        name: _compare(name, local[name], remote[name])
        for name in sorted(local)
    }
    return {
        "status": "OK" if all(item["status"] == "MATCH" for item in comparisons.values()) else "MISMATCH",
        "read_only": True,
        "snapshot": snapshot_evidence,
        "comparisons": comparisons,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", default="data")
    parser.add_argument("--snapshot-dir", default=None)
    args = parser.parse_args()
    result = compare(
        Path(args.data_dir).resolve(),
        snapshot_dir=Path(args.snapshot_dir).resolve() if args.snapshot_dir else None,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    if result["status"] != "OK":
        raise SystemExit(1)


if __name__ == "__main__":
    main()

