"""Verify that an imported PostgreSQL database matches local ATLAX state."""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path
from typing import Any

from core.settings.storage_config import load_storage_config
from core.storage.postgres import PostgresDatabase


def _json_count(path: Path) -> int:
    if not path.exists():
        return 0
    value = json.loads(path.read_text(encoding="utf-8"))
    return len(value) if isinstance(value, dict) else 0


def _sqlite_count(path: Path, table: str) -> int:
    if not path.exists():
        return 0
    with sqlite3.connect(path) as connection:
        row = connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
    return int(row[0] or 0)


def _audit_count(path: Path) -> int:
    if not path.exists():
        return 0
    return sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip())


def local_counts(data_dir: Path) -> dict[str, int]:
    runtime_files = (
        "active_predictions.json",
        "daily_stats.json",
        "fvg_watches.json",
        "heatmap.json",
        "correlation.json",
        "last_alerted.json",
        "telegram_offset.json",
    )
    return {
        "subscribers": _json_count(data_dir / "subscribers.json"),
        "invite_codes": _json_count(data_dir / "invite_codes.json"),
        "payment_intents": _json_count(data_dir / "payment_intents.json"),
        "users": _sqlite_count(data_dir / "users.sqlite", "users"),
        "journal_entries": _sqlite_count(data_dir / "journal.sqlite", "journal_entries"),
        "audit_events": _audit_count(data_dir / "admin_audit.jsonl"),
        "runtime_state": sum((data_dir / name).exists() for name in runtime_files),
    }


def postgres_counts(database: PostgresDatabase) -> dict[str, int]:
    tables = {
        "subscribers": "subscribers",
        "invite_codes": "invite_codes",
        "payment_intents": "payment_intents",
        "users": "auth_users",
        "journal_entries": "journal_entries",
        "audit_events": "audit_events",
        "runtime_state": "runtime_state",
    }
    with database.connection() as connection:
        return {
            label: int(
                connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] or 0
            )
            for label, table in tables.items()
        }


def verify(data_dir: Path) -> dict[str, Any]:
    config = load_storage_config()
    if not config.uses_postgres:
        raise ValueError(
            "Set ATLAX_STORAGE_BACKEND=postgres and ATLAX_DATABASE_URL before verifying."
        )
    database = PostgresDatabase(config)
    database.initialize_schema()
    local = local_counts(data_dir)
    remote = postgres_counts(database)
    comparisons = {}
    for label, local_count in local.items():
        remote_count = remote.get(label, 0)
        comparisons[label] = {
            "local": local_count,
            "postgres": remote_count,
            "status": "MATCH" if local_count == remote_count else "MISMATCH",
        }
    return {
        "status": (
            "OK"
            if all(item["status"] == "MATCH" for item in comparisons.values())
            else "MISMATCH"
        ),
        "comparisons": comparisons,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", default="data")
    args = parser.parse_args()
    result = verify(Path(args.data_dir).resolve())
    print(json.dumps(result, indent=2, sort_keys=True))
    if result["status"] != "OK":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
