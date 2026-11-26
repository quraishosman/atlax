"""Read-only readiness check for the coordinated PostgreSQL cutover."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from core.settings.storage_config import StorageConfig, load_storage_config
from core.storage.dual_read import compare
from core.storage.frozen_snapshot import SnapshotVerificationError, verify_snapshot


def configuration_checks(
    config: StorageConfig,
    *,
    schema_path: Path,
) -> list[dict[str, str]]:
    """Return deterministic checks that do not contact or mutate a database."""
    checks = [
        {
            "name": "postgres_backend_selected",
            "status": "PASS" if config.uses_postgres else "BLOCKED",
            "reason": "selected" if config.uses_postgres else "postgres_backend_not_selected",
        },
        {
            "name": "database_url_present",
            "status": "PASS" if bool(config.database_url) else "BLOCKED",
            "reason": "present" if config.database_url else "database_url_missing",
        },
        {
            "name": "schema_present",
            "status": "PASS" if schema_path.exists() else "BLOCKED",
            "reason": "present" if schema_path.exists() else "postgres_schema_missing",
        },
    ]
    return checks


def assess(
    data_dir: Path,
    *,
    config: StorageConfig | None = None,
    schema_path: Path | None = None,
    snapshot_dir: Path | None = None,
) -> dict[str, Any]:
    """Assess cutover readiness without enabling the cutover."""
    selected = config or load_storage_config()
    schema = schema_path or (
        Path(__file__).resolve().parents[2] / "db" / "postgres" / "schema.sql"
    )
    checks = configuration_checks(selected, schema_path=schema)
    if snapshot_dir is None:
        checks.append(
            {
                "name": "frozen_snapshot_present",
                "status": "BLOCKED",
                "reason": "frozen_snapshot_not_provided",
            }
        )
    else:
        try:
            verify_snapshot(snapshot_dir)
            checks.append(
                {
                    "name": "frozen_snapshot_present",
                    "status": "PASS",
                    "reason": "verified",
                }
            )
        except SnapshotVerificationError as exc:
            checks.append(
                {
                    "name": "frozen_snapshot_present",
                    "status": "BLOCKED",
                    "reason": str(exc),
                }
            )
    blocked = [item["reason"] for item in checks if item["status"] == "BLOCKED"]
    result: dict[str, Any] = {
        "status": "BLOCKED" if blocked else "PENDING_DATABASE_CHECK",
        "read_only": True,
        "cutover_enabled": selected.cutover_enabled,
        "checks": checks,
        "blockers": blocked,
    }
    if blocked:
        return result

    try:
        comparison = compare(data_dir, snapshot_dir=snapshot_dir)
    except Exception as exc:  # noqa: BLE001
        result["status"] = "BLOCKED"
        result["blockers"] = [f"database_check_failed:{type(exc).__name__}"]
        result["comparison"] = {"status": "UNKNOWN", "read_only": True}
        return result

    result["comparison"] = comparison
    if comparison.get("status") != "OK":
        result["status"] = "BLOCKED"
        result["blockers"] = ["dual_read_mismatch"]
    elif not selected.cutover_enabled:
        result["status"] = "VERIFIED_NOT_ACTIVATED"
        result["blockers"] = ["cutover_flag_disabled"]
    else:
        result["status"] = "READY_FOR_OPERATIONAL_SIGNOFF"
        result["blockers"] = []
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", default="data")
    parser.add_argument("--snapshot-dir", default=None)
    args = parser.parse_args()
    result = assess(
        Path(args.data_dir).resolve(),
        snapshot_dir=Path(args.snapshot_dir).resolve() if args.snapshot_dir else None,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    if result["status"] not in {"VERIFIED_NOT_ACTIVATED", "READY_FOR_OPERATIONAL_SIGNOFF"}:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
