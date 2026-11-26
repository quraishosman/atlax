from decimal import Decimal

from core.confidence.atr_quality import ATRQualityResult
from core.analytics.journal import SQLiteJournal
from core.analytics.signal_passport import (
    get_signal_passport_history,
    get_signal_passport_performance,
    render_signal_passport_daily,
    render_signal_passport_history,
    render_signal_passport_weekly,
)


def _add_signal(journal, signal_id, symbol, profile, atr_quality=None):
    journal.add_signal_entry(
        record_id=signal_id,
        candidate_id=f"candidate-{signal_id}",
        symbol=symbol,
        timeframe="H1",
        profile=profile,
        direction="BUY",
        confidence_score=80.0,
        confidence_breakdown={"quality": "80/100"},
        entry_price=Decimal("1.1000"),
        sl_price=Decimal("1.0900"),
        tp_price=Decimal("1.1200"),
        atr_quality=atr_quality,
    )


def _key_level_evaluation(zone=True):
    return {
        "status": "VALID",
        "parent_at_htf_zone": zone,
        "symbol": "EURUSD",
        "parent_timeframe": "H1",
        "parent_open_time": "2026-08-14T10:00:00+00:00",
        "tolerance": "0.0010",
        "tolerance_atr_multiplier": "0.10",
        "minimum_tolerance_pips": "5",
        "matched_level_count": 2,
        "matched_levels": [
            {"level_type": "PREVIOUS_DAY_HIGH", "price": "1.1050"},
            {"level_type": "H4_SWING_HIGH", "price": "1.1050"},
        ],
        "missing_sources": [],
        "configuration_snapshot_id": "key-level-snapshot-1",
        "explanation": "Parent matched 2 configured key level(s).",
    }


def _atr_quality() -> ATRQualityResult:
    return ATRQualityResult(
        status="VALID",
        grade="A",
        atr=Decimal("0.0010"),
        atr_period=14,
        method="SIMPLE_TR_MEAN",
        parent_range=Decimal("0.0008"),
        parent_atr_ratio=Decimal("0.8"),
        sweep_depth=Decimal("0.0002"),
        sweep_atr_ratio=Decimal("0.2"),
        configuration_snapshot_id="atr-snapshot-1",
        explanation="Parent range is 0.8000 ATR and sweep depth is 0.2000 ATR; quality grade A.",
    )


def test_passport_id_is_stable_and_outcome_is_idempotent(tmp_path):
    journal = SQLiteJournal(str(tmp_path / "journal.sqlite"))
    _add_signal(journal, "signal-1", "EURUSD", "day_trader")

    journal.update_signal_outcome(
        "signal-1", "sl_hit", Decimal("1.0900"), Decimal("-1")
    )
    journal.update_signal_outcome(
        "signal-1", "tp2_hit", Decimal("1.1200"), Decimal("2")
    )

    records = get_signal_passport_history(str(tmp_path / "journal.sqlite"))
    assert records[0]["signal_id"] == "signal-1"
    assert records[0]["status"] == "RESOLVED"
    assert records[0]["final_outcome"] == "LOSS"
    assert records[0]["invalidation_reason"] == "stop_loss_hit"


def test_passport_persists_atr_quality_metadata(tmp_path):
    db_path = str(tmp_path / "journal.sqlite")
    journal = SQLiteJournal(db_path)
    _add_signal(journal, "signal-atr-1", "EURUSD", "day_trader", _atr_quality())

    record = get_signal_passport_history(db_path, days=30)[0]
    atr = record["atr_quality"]

    assert record["schema_version"] == "1.2"
    assert atr["status"] == "VALID"
    assert atr["grade"] == "A"
    assert atr["method"] == "SIMPLE_TR_MEAN"
    assert atr["period"] == 14
    assert atr["parent_atr_ratio"] == "0.8"
    assert atr["sweep_atr_ratio"] == "0.2"
    assert atr["configuration_snapshot_id"] == "atr-snapshot-1"
    assert "ATR quality" in render_signal_passport_history([record])


def test_passport_persists_key_level_metadata_without_exposing_raw_prices(tmp_path):
    db_path = str(tmp_path / "journal.sqlite")
    journal = SQLiteJournal(db_path)
    journal.add_signal_entry(
        record_id="signal-key-level-1",
        candidate_id="candidate-key-level-1",
        symbol="EURUSD",
        timeframe="H1",
        profile="day_trader",
        direction="BUY",
        confidence_score=82.0,
        confidence_breakdown={"htf_zone": "15/15"},
        entry_price=Decimal("1.1000"),
        sl_price=Decimal("1.0900"),
        tp_price=Decimal("1.1200"),
        key_level_evaluation=_key_level_evaluation(),
    )

    record = get_signal_passport_history(db_path, days=30)[0]
    assert record["schema_version"] == "1.2"
    assert record["key_level"]["status"] == "VALID"
    assert record["key_level"]["at_htf_zone"] is True
    assert record["key_level"]["matched_level_count"] == 2
    assert record["key_level"]["configuration_snapshot_id"] == "key-level-snapshot-1"
    rendered = render_signal_passport_history([record])
    assert "Major key level" in rendered
    assert "1.1050" not in rendered


def test_passport_aggregates_pair_style_and_unknown_outcomes(tmp_path):
    db_path = str(tmp_path / "journal.sqlite")
    journal = SQLiteJournal(db_path)
    _add_signal(journal, "signal-1", "EURUSD", "day_trader")
    _add_signal(journal, "signal-2", "GBPUSD", "swing_trader")
    _add_signal(journal, "signal-3", "EURUSD", "day_trader")

    journal.update_signal_outcome(
        "signal-1", "tp2_hit", Decimal("1.1200"), Decimal("2")
    )
    journal.mark_signal_expired("signal-2")

    report = get_signal_passport_performance(db_path, days=7)
    assert report["status"] == "OK"
    assert report["total_signals"] == 3
    assert report["open_signals"] == 1
    assert report["resolved_signals"] == 1
    assert report["wins"] == 1
    assert report["losses"] == 0
    assert report["unknown_outcomes"] == 2
    assert report["win_rate_pct"] == 100.0
    assert report["by_pair"]["EURUSD"]["total_signals"] == 2
    assert report["by_style"]["day_trader"]["open_signals"] == 1


def test_passport_renderers_are_subscriber_safe(tmp_path):
    db_path = str(tmp_path / "journal.sqlite")
    journal = SQLiteJournal(db_path)
    _add_signal(journal, "signal-1", "XAUUSD", "swing_trader")

    history = render_signal_passport_history(
        get_signal_passport_history(db_path, days=30)
    )
    weekly = render_signal_passport_weekly(
        get_signal_passport_performance(db_path, days=7)
    )

    assert "Signal Passport" in history
    assert "XAUUSD" in history
    assert "signal-1" not in history
    assert "Created:" in history
    assert "Weekly Signal Report" in weekly
    assert "Swing Trader" in weekly
    assert "_trader" not in weekly
    assert "Daily Signal Report" in render_signal_passport_daily(
        get_signal_passport_performance(db_path, days=1)
    )
    assert "total_pnl" not in history.lower()
    assert "broker" not in weekly.lower()


def test_passport_history_supports_ten_record_pages(tmp_path):
    db_path = str(tmp_path / "journal.sqlite")
    journal = SQLiteJournal(db_path)
    for index in range(12):
        _add_signal(journal, f"signal-{index}", "EURUSD", "day_trader")

    first_page = get_signal_passport_history(db_path, days=30, limit=10, offset=0)
    second_page = get_signal_passport_history(db_path, days=30, limit=10, offset=10)

    assert len(first_page) == 10
    assert len(second_page) == 2
    assert {item["signal_id"] for item in first_page}.isdisjoint(
        {item["signal_id"] for item in second_page}
    )


def test_passport_history_status_exposes_missing_storage(tmp_path):
    result = get_signal_passport_history(
        str(tmp_path / "missing.sqlite"),
        with_status=True,
    )

    assert result["status"] == "UNKNOWN"
    assert result["items"] == []
    assert result["reason"] == "Signal Passport is unavailable."
