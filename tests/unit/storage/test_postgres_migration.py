"""Dependency-free tests for the staged PostgreSQL migration boundary."""

from __future__ import annotations

import unittest
import json
import sqlite3
import tempfile
from pathlib import Path

from core.settings.storage_config import StorageConfig
from core.storage.cutover_preflight import assess, configuration_checks
from core.storage.dual_read import _compare
from core.storage.frozen_snapshot import (
    SnapshotVerificationError,
    create_snapshot,
    verify_snapshot,
)
from core.storage.repositories import build_postgres_repositories


class PostgresMigrationBoundaryTests(unittest.TestCase):
    def test_local_backend_rejects_explicit_postgres_repositories(self) -> None:
        with self.assertRaises(ValueError):
            build_postgres_repositories(StorageConfig())

    def test_postgres_backend_requires_a_database_url(self) -> None:
        with self.assertRaises(ValueError):
            StorageConfig(backend="postgres")

    def test_cutover_preflight_blocks_local_backend_without_contacting_database(self) -> None:
        config = StorageConfig()
        result = assess(
            Path("data"),
            config=config,
            schema_path=Path("db/postgres/schema.sql"),
        )
        self.assertEqual(result["status"], "BLOCKED")
        self.assertIn("postgres_backend_not_selected", result["blockers"])

    def test_configuration_checks_are_read_only_and_explicit(self) -> None:
        checks = configuration_checks(
            StorageConfig(),
            schema_path=Path("db/postgres/schema.sql"),
        )
        self.assertEqual(checks[0]["status"], "BLOCKED")
        self.assertEqual(checks[1]["status"], "BLOCKED")
        self.assertEqual(checks[2]["status"], "PASS")

    def test_matching_records_pass_comparison_without_exposing_bodies(self) -> None:
        result = _compare(
            "payments",
            {"pi_1": {"status": "confirmed", "reference": "ATLAX-1"}},
            {"pi_1": {"status": "confirmed", "reference": "ATLAX-1"}},
        )
        self.assertEqual(result["status"], "MATCH")
        self.assertNotIn("record", result)
        self.assertEqual(result["fingerprint_mismatches"], [])

    def test_changed_or_missing_records_fail_comparison(self) -> None:
        result = _compare(
            "subscribers",
            {"100": {"active": True}, "200": {"active": True}},
            {"100": {"active": False}, "300": {"active": True}},
        )
        self.assertEqual(result["status"], "MISMATCH")
        self.assertEqual(result["missing_in_postgres"], ["200"])
        self.assertEqual(result["missing_in_local"], ["300"])
        self.assertEqual(result["fingerprint_mismatches"], ["100"])

    def test_frozen_snapshot_copies_state_and_verifies_integrity(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "data"
            snapshot = Path(temporary) / "snapshot"
            source.mkdir()
            (source / "subscribers.json").write_text(
                json.dumps({"100": {"active": True}}), encoding="utf-8"
            )
            connection = sqlite3.connect(source / "users.sqlite")
            connection.execute(
                "CREATE TABLE users (username TEXT, hashed_password TEXT, role TEXT, created_at TEXT)"
            )
            connection.execute(
                "INSERT INTO users VALUES ('admin', 'hash', 'admin', '2026-08-15T00:00:00Z')"
            )
            connection.commit()
            connection.close()

            manifest = create_snapshot(source, snapshot)

            self.assertEqual(manifest["sourceFileCount"], 2)
            self.assertTrue(verify_snapshot(snapshot)["verified"])
            (snapshot / "subscribers.json").write_text("{}", encoding="utf-8")
            with self.assertRaises(SnapshotVerificationError):
                verify_snapshot(snapshot)


if __name__ == "__main__":
    unittest.main()

