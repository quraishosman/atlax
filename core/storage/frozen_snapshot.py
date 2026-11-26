"""Create and verify immutable local snapshots for migration rehearsals."""

from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SNAPSHOT_MANIFEST = "snapshot_manifest.json"
SNAPSHOT_VERSION = "1.0"
SNAPSHOT_FILES = (
    "subscribers.json",
    "invite_codes.json",
    "payment_intents.json",
    "users.sqlite",
    "journal.sqlite",
    "admin_audit.jsonl",
    "active_predictions.json",
    "daily_stats.json",
    "fvg_watches.json",
    "heatmap.json",
    "correlation.json",
    "last_alerted.json",
    "telegram_offset.json",
)


class SnapshotVerificationError(RuntimeError):
    """Raised when a snapshot is missing or has changed."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _copy_sqlite(source: Path, destination: Path) -> None:
    source_connection = sqlite3.connect(str(source))
    destination_connection = sqlite3.connect(str(destination))
    try:
        source_connection.backup(destination_connection)
        destination_connection.commit()
    finally:
        destination_connection.close()
        source_connection.close()


def _copy_state_file(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if source.suffix.lower() in {".sqlite", ".db"}:
        _copy_sqlite(source, destination)
    else:
        shutil.copy2(source, destination)


def create_snapshot(source_dir: Path, snapshot_dir: Path) -> dict[str, Any]:
    """Copy known local stores into a new snapshot and write hash evidence."""
    source = source_dir.resolve()
    destination = snapshot_dir.resolve()
    if source == destination:
        raise ValueError("Snapshot destination must differ from the source directory")
    destination.mkdir(parents=True, exist_ok=True)

    files: dict[str, dict[str, Any]] = {}
    for filename in SNAPSHOT_FILES:
        source_path = source / filename
        if not source_path.exists():
            continue
        destination_path = destination / filename
        _copy_state_file(source_path, destination_path)
        files[filename] = {
            "sha256": _sha256(destination_path),
            "size": destination_path.stat().st_size,
        }

    manifest: dict[str, Any] = {
        "snapshotVersion": SNAPSHOT_VERSION,
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "sourceFileCount": len(files),
        "files": files,
    }
    manifest_path = destination / SNAPSHOT_MANIFEST
    temporary_path = destination / f".{SNAPSHOT_MANIFEST}.tmp"
    temporary_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    temporary_path.replace(manifest_path)
    return manifest


def verify_snapshot(snapshot_dir: Path) -> dict[str, Any]:
    """Verify snapshot hashes and return sanitized manifest evidence."""
    directory = snapshot_dir.resolve()
    manifest_path = directory / SNAPSHOT_MANIFEST
    if not manifest_path.exists():
        raise SnapshotVerificationError("snapshot_manifest_missing")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SnapshotVerificationError("snapshot_manifest_invalid") from exc
    if manifest.get("snapshotVersion") != SNAPSHOT_VERSION:
        raise SnapshotVerificationError("snapshot_version_unsupported")

    mismatches: list[str] = []
    files = manifest.get("files")
    if not isinstance(files, dict):
        raise SnapshotVerificationError("snapshot_files_invalid")
    for filename, metadata in files.items():
        path = directory / str(filename)
        if not path.exists() or not path.is_file():
            mismatches.append(f"missing:{filename}")
            continue
        if not isinstance(metadata, dict) or _sha256(path) != metadata.get("sha256"):
            mismatches.append(f"changed:{filename}")
    if mismatches:
        raise SnapshotVerificationError("snapshot_integrity_failed:" + ",".join(mismatches[:20]))
    return {
        "snapshotVersion": SNAPSHOT_VERSION,
        "createdAt": str(manifest.get("createdAt", "")),
        "sourceFileCount": int(manifest.get("sourceFileCount", len(files))),
        "verified": True,
    }
