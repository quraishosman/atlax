"""
Tests for Volume Profile passport serialization and journal storage (Phase 5).

Covers:
  - volume_profile_passport_fields(): VALID result → all 13 fields populated
  - volume_profile_passport_fields(): UNKNOWN result → safe defaults
  - volume_profile_passport_fields(): None → NOT_APPLICABLE defaults
  - Journal: VP fields written on add_signal_entry()
  - Journal: Backward compat — calling without VP still works
  - Journal: VP schema columns exist after DB init

Authority:
    docs/21_SIGNAL_PASSPORT.md
    docs/40_VOLUME_PROFILE_CONFLUENCE_PLAN.md §5 (Signal Passport)
    docs/decisions/ADR-017.md
"""

import json
import os
import tempfile
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from core.analytics.journal import SQLiteJournal
from core.analytics.volume_profile_passport import (
    VOLUME_PROFILE_PASSPORT_FIELDS,
    volume_profile_passport_fields,
)
from core.models.volume_profile_result import (
    VolumeProfileLevel,
    VolumeProfileLevels,
    VolumeProfileResult,
    VolumeProfileStatus,
)

UTC = timezone.utc


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _valid_result(
    poc="1.1000", vah="1.1050", val="1.0950"
) -> VolumeProfileResult:
    return VolumeProfileResult(
        status=VolumeProfileStatus.VALID,
        symbol="EURUSD",
        period_label="PREVIOUS_DAY",
        period_start_utc=datetime(2026, 8, 15, tzinfo=UTC),
        period_end_utc=datetime(2026, 8, 16, tzinfo=UTC),
        source_timeframe="M15",
        volume_source="MT5_TICK_VOLUME",
        volume_quality="BROKER_TICK_PROXY",
        allocation_method="RANGE_UNIFORM",
        value_area_percent=0.70,
        configuration_snapshot_id="snap123",
        levels=VolumeProfileLevels(
            poc=VolumeProfileLevel("PREVIOUS_DAY_POC", Decimal(poc), Decimal("5000")),
            vah=VolumeProfileLevel("PREVIOUS_DAY_VAH", Decimal(vah), Decimal("2000")),
            val=VolumeProfileLevel("PREVIOUS_DAY_VAL", Decimal(val), Decimal("2000")),
            total_volume=Decimal("10000"),
            bin_width=Decimal("0.0001"),
            source_bars=96,
            valid_volume_bars=90,
        ),
    )


def _unknown_result() -> VolumeProfileResult:
    return VolumeProfileResult(
        status=VolumeProfileStatus.UNKNOWN,
        symbol="EURUSD",
        period_label="PREVIOUS_DAY",
        period_start_utc=datetime(2026, 8, 15, tzinfo=UTC),
        period_end_utc=datetime(2026, 8, 16, tzinfo=UTC),
        source_timeframe="M15",
        volume_source="MT5_TICK_VOLUME",
        volume_quality="UNKNOWN",
        allocation_method="RANGE_UNIFORM",
        value_area_percent=0.70,
        configuration_snapshot_id="snap123",
        levels=None,
        unknown_reason="insufficient_source_bars",
    )


def _make_journal() -> tuple[SQLiteJournal, str]:
    db_path = tempfile.mktemp(suffix=".db")
    return SQLiteJournal(db_path), db_path


# ---------------------------------------------------------------------------
# Passport helper tests
# ---------------------------------------------------------------------------


class TestVolumeProfilePassportFields:
    def test_none_result_returns_not_applicable(self):
        fields = volume_profile_passport_fields(None)
        assert fields["volume_profile_status"] == "NOT_APPLICABLE"
        assert fields["volume_profile_poc"] is None
        assert fields["volume_profile_vah"] is None
        assert fields["volume_profile_val"] is None
        assert fields["volume_profile_evidence"] == "{}"
        assert fields["volume_profile_configuration_snapshot_id"] == ""

    def test_valid_result_populates_all_fields(self):
        fields = volume_profile_passport_fields(_valid_result())
        assert fields["volume_profile_status"] == "VALID"
        assert fields["volume_profile_period"] == "PREVIOUS_DAY"
        assert fields["volume_profile_source_timeframe"] == "M15"
        assert fields["volume_profile_volume_source"] == "MT5_TICK_VOLUME"
        assert fields["volume_profile_volume_quality"] == "BROKER_TICK_PROXY"
        assert fields["volume_profile_poc"] == "1.1000"
        assert fields["volume_profile_vah"] == "1.1050"
        assert fields["volume_profile_val"] == "1.0950"
        assert fields["volume_profile_total_volume"] == "10000"
        assert fields["volume_profile_source_bars"] == 96
        assert fields["volume_profile_configuration_snapshot_id"] == "snap123"
        assert fields["volume_profile_unknown_reason"] is None

    def test_valid_result_evidence_is_valid_json(self):
        fields = volume_profile_passport_fields(_valid_result())
        evidence = json.loads(fields["volume_profile_evidence"])
        assert evidence["status"] == "VALID"
        assert evidence["poc"] == "1.1000"
        assert evidence["vah"] == "1.1050"
        assert evidence["val"] == "1.0950"

    def test_unknown_result_status_is_unknown(self):
        fields = volume_profile_passport_fields(_unknown_result())
        assert fields["volume_profile_status"] == "UNKNOWN"
        assert fields["volume_profile_poc"] is None
        assert fields["volume_profile_vah"] is None
        assert fields["volume_profile_val"] is None
        assert fields["volume_profile_unknown_reason"] == "insufficient_source_bars"

    def test_all_field_names_present(self):
        fields = volume_profile_passport_fields(_valid_result())
        for field in VOLUME_PROFILE_PASSPORT_FIELDS:
            assert field in fields, f"Missing field: {field}"

    def test_prices_are_strings_preserving_precision(self):
        fields = volume_profile_passport_fields(_valid_result(poc="1.08500"))
        assert fields["volume_profile_poc"] == "1.08500"


# ---------------------------------------------------------------------------
# Journal integration tests
# ---------------------------------------------------------------------------


class TestJournalVolumeProfileStorage:
    def test_vp_columns_exist_after_db_init(self):
        j, db = _make_journal()
        try:
            with j._get_connection() as conn:
                cols = {row[1] for row in conn.execute("PRAGMA table_info(journal_entries)")}
            for field in VOLUME_PROFILE_PASSPORT_FIELDS:
                assert field in cols, f"Missing journal column: {field}"
        finally:
            os.unlink(db)

    def test_add_signal_entry_with_valid_vp_stores_fields(self):
        j, db = _make_journal()
        try:
            j.add_signal_entry(
                record_id="sig-1",
                candidate_id="cand-1",
                symbol="EURUSD",
                timeframe="H1",
                profile="day_trader",
                direction="BUY",
                confidence_score=72.0,
                confidence_breakdown={},
                entry_price=Decimal("1.1000"),
                sl_price=Decimal("1.0980"),
                tp_price=Decimal("1.1040"),
                volume_profile=_valid_result(),
            )
            with j._get_connection() as conn:
                row = conn.execute(
                    "SELECT volume_profile_status, volume_profile_period, "
                    "volume_profile_poc, volume_profile_vah, volume_profile_val, "
                    "volume_profile_configuration_snapshot_id "
                    "FROM journal_entries WHERE record_id = ?",
                    ("sig-1",),
                ).fetchone()
            assert row[0] == "VALID"
            assert row[1] == "PREVIOUS_DAY"
            assert row[2] == "1.1000"
            assert row[3] == "1.1050"
            assert row[4] == "1.0950"
            assert row[5] == "snap123"
        finally:
            os.unlink(db)

    def test_add_signal_entry_without_vp_uses_not_applicable(self):
        """Backward compat: omitting volume_profile defaults to NOT_APPLICABLE."""
        j, db = _make_journal()
        try:
            j.add_signal_entry(
                record_id="sig-2",
                candidate_id="cand-2",
                symbol="EURUSD",
                timeframe="H1",
                profile="day_trader",
                direction="SELL",
                confidence_score=65.0,
                confidence_breakdown={},
                entry_price=Decimal("1.1000"),
                sl_price=Decimal("1.1020"),
                tp_price=Decimal("1.0960"),
            )
            with j._get_connection() as conn:
                row = conn.execute(
                    "SELECT volume_profile_status, volume_profile_poc "
                    "FROM journal_entries WHERE record_id = ?",
                    ("sig-2",),
                ).fetchone()
            assert row[0] == "NOT_APPLICABLE"
            assert row[1] is None
        finally:
            os.unlink(db)

    def test_add_signal_entry_with_unknown_vp(self):
        j, db = _make_journal()
        try:
            j.add_signal_entry(
                record_id="sig-3",
                candidate_id="cand-3",
                symbol="EURUSD",
                timeframe="H1",
                profile="day_trader",
                direction="BUY",
                confidence_score=70.0,
                confidence_breakdown={},
                entry_price=Decimal("1.1000"),
                sl_price=Decimal("1.0980"),
                tp_price=Decimal("1.1040"),
                volume_profile=_unknown_result(),
            )
            with j._get_connection() as conn:
                row = conn.execute(
                    "SELECT volume_profile_status, volume_profile_unknown_reason "
                    "FROM journal_entries WHERE record_id = ?",
                    ("sig-3",),
                ).fetchone()
            assert row[0] == "UNKNOWN"
            assert row[1] == "insufficient_source_bars"
        finally:
            os.unlink(db)

    def test_evidence_json_is_stored_and_parseable(self):
        j, db = _make_journal()
        try:
            j.add_signal_entry(
                record_id="sig-4",
                candidate_id="cand-4",
                symbol="EURUSD",
                timeframe="H1",
                profile="day_trader",
                direction="BUY",
                confidence_score=75.0,
                confidence_breakdown={},
                entry_price=Decimal("1.1000"),
                sl_price=Decimal("1.0980"),
                tp_price=Decimal("1.1040"),
                volume_profile=_valid_result(),
            )
            with j._get_connection() as conn:
                evidence_json = conn.execute(
                    "SELECT volume_profile_evidence FROM journal_entries WHERE record_id = ?",
                    ("sig-4",),
                ).fetchone()[0]
            evidence = json.loads(evidence_json)
            assert evidence["status"] == "VALID"
            assert "poc" in evidence
        finally:
            os.unlink(db)
