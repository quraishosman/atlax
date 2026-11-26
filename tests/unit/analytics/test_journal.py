import pytest
import os
from datetime import datetime, timezone
from decimal import Decimal

from core.models.analytics import JournalEntry
from core.analytics.journal import SQLiteJournal
from core.analytics.metrics import MetricsCalculator


@pytest.fixture
def memory_journal(tmp_path):
    # Use physical file in tmp_path to ensure connections see the same tables
    db_file = tmp_path / "test_journal.sqlite"
    return SQLiteJournal(str(db_file))


def _make_entry(record_id="rec-1", profile="swing", pnl="10.5", closed_at=None):
    return JournalEntry(
        record_id=record_id,
        candidate_id="cand-1",
        symbol="EURUSD",
        timeframe="H4",
        profile=profile,
        strategy_name="CRTStrategy",
        direction="BUY",
        confidence_score=85.0,
        confidence_breakdown={"trend": 10},
        lot_size=Decimal("0.1"),
        entry_price=Decimal("1.1000"),
        sl_price=Decimal("1.0900"),
        tp_price=Decimal("1.1200"),
        exit_price=Decimal("1.1105"),
        pnl=Decimal(pnl),
        closed_at=closed_at or datetime.now(timezone.utc),
        exit_reason="TP_HIT",
        tags=["manual_close"]
    )


class TestJournalStorage:
    def test_add_and_retrieve_entry(self, memory_journal):
        entry = _make_entry()
        memory_journal.add_entry(entry)
        
        entries = memory_journal.get_entries()
        assert len(entries) == 1
        
        retrieved = entries[0]
        assert retrieved.record_id == "rec-1"
        assert retrieved.profile == "swing"
        assert retrieved.pnl == Decimal("10.5")
        assert retrieved.tags == ["manual_close"]
        assert retrieved.confidence_breakdown == {"trend": 10}

    def test_filter_by_profile(self, memory_journal):
        memory_journal.add_entry(_make_entry(record_id="1", profile="swing"))
        memory_journal.add_entry(_make_entry(record_id="2", profile="scalp"))
        
        swing = memory_journal.get_entries(profile="swing")
        assert len(swing) == 1
        assert swing[0].profile == "swing"
        

class TestMetricsCalculator:
    def test_empty_list_returns_zeroes(self):
        metrics = MetricsCalculator.calculate([])
        assert metrics.total_trades == 0
        assert metrics.win_rate == 0.0
        
    def test_win_rate_and_pnl(self):
        entries = [
            _make_entry(record_id="1", pnl="10.0"),  # Win
            _make_entry(record_id="2", pnl="10.0"),  # Win
            _make_entry(record_id="3", pnl="-5.0"),  # Loss
            _make_entry(record_id="4", pnl="-5.0")   # Loss
        ]
        
        metrics = MetricsCalculator.calculate(entries)
        assert metrics.total_trades == 4
        assert metrics.win_rate == 0.5
        assert metrics.total_pnl == 10.0
        assert metrics.profit_factor == 2.0  # 20 / 10
        assert metrics.expectancy == 2.5     # 10 / 4
        
    def test_max_drawdown(self):
        entries = [
            _make_entry(record_id="1", pnl="10.0"),  # Peak: 10
            _make_entry(record_id="2", pnl="-5.0"),  # Cum: 5, DD: 5
            _make_entry(record_id="3", pnl="-10.0"), # Cum: -5, DD: 15
            _make_entry(record_id="4", pnl="20.0")   # Cum: 15, DD: 0
        ]
        
        metrics = MetricsCalculator.calculate(entries)
        assert metrics.max_drawdown == 15.0
