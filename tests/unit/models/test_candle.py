"""
Tests for core.models.candle — Candle and CandleSequence.

Authority: docs/06_DATA_MODELS.md, CRT-DATA-001
"""

import pytest
from decimal import Decimal
from datetime import datetime, timezone

from core.models.candle import Candle, CandleSequence


class TestCandle:
    """Tests for the Candle dataclass."""

    def test_valid_candle_creation(self):
        """A candle with valid OHLC values should construct without error."""
        c = Candle(
            symbol="EURUSD", timeframe="H4",
            open_time=datetime(2026, 7, 20, 0, 0, tzinfo=timezone.utc),
            close_time=datetime(2026, 7, 20, 4, 0, tzinfo=timezone.utc),
            open=Decimal("1.1000"), high=Decimal("1.1050"),
            low=Decimal("1.0950"), close=Decimal("1.1020"),
            is_closed=True,
        )
        assert c.symbol == "EURUSD"
        assert c.is_closed is True

    def test_high_less_than_low_raises(self):
        """CRT-DATA-001: high < low violates candle invariant."""
        with pytest.raises(ValueError, match="high.*< low"):
            Candle(
                symbol="EURUSD", timeframe="H4",
                open_time=datetime(2026, 7, 20, 0, 0, tzinfo=timezone.utc),
                close_time=datetime(2026, 7, 20, 4, 0, tzinfo=timezone.utc),
                open=Decimal("1.1000"), high=Decimal("1.0900"),
                low=Decimal("1.0950"), close=Decimal("1.0920"),
                is_closed=True,
            )

    def test_high_less_than_close_raises(self):
        """High must be >= close."""
        with pytest.raises(ValueError, match="high.*not the highest"):
            Candle(
                symbol="EURUSD", timeframe="H4",
                open_time=datetime(2026, 7, 20, 0, 0, tzinfo=timezone.utc),
                close_time=datetime(2026, 7, 20, 4, 0, tzinfo=timezone.utc),
                open=Decimal("1.1000"), high=Decimal("1.1010"),
                low=Decimal("1.0950"), close=Decimal("1.1020"),
                is_closed=True,
            )

    def test_range_property(self):
        """CRT-PARENT-001: range = high - low."""
        c = Candle(
            symbol="EURUSD", timeframe="H4",
            open_time=datetime(2026, 7, 20, 0, 0, tzinfo=timezone.utc),
            close_time=datetime(2026, 7, 20, 4, 0, tzinfo=timezone.utc),
            open=Decimal("100"), high=Decimal("110"),
            low=Decimal("90"), close=Decimal("105"),
            is_closed=True,
        )
        assert c.range == Decimal("20")

    def test_body_size_property(self):
        """Body = |close - open|."""
        c = Candle(
            symbol="EURUSD", timeframe="H4",
            open_time=datetime(2026, 7, 20, 0, 0, tzinfo=timezone.utc),
            close_time=datetime(2026, 7, 20, 4, 0, tzinfo=timezone.utc),
            open=Decimal("100"), high=Decimal("110"),
            low=Decimal("90"), close=Decimal("105"),
            is_closed=True,
        )
        assert c.body_size == Decimal("5")

    def test_midpoint_property(self):
        """Midpoint = (high + low) / 2 — Mean Threshold."""
        c = Candle(
            symbol="EURUSD", timeframe="H4",
            open_time=datetime(2026, 7, 20, 0, 0, tzinfo=timezone.utc),
            close_time=datetime(2026, 7, 20, 4, 0, tzinfo=timezone.utc),
            open=Decimal("100"), high=Decimal("110"),
            low=Decimal("90"), close=Decimal("105"),
            is_closed=True,
        )
        assert c.midpoint == Decimal("100")

    def test_bullish_bearish(self):
        """is_bullish and is_bearish properties."""
        bull = Candle(
            symbol="EURUSD", timeframe="H4",
            open_time=datetime(2026, 7, 20, 0, 0, tzinfo=timezone.utc),
            close_time=datetime(2026, 7, 20, 4, 0, tzinfo=timezone.utc),
            open=Decimal("100"), high=Decimal("110"),
            low=Decimal("95"), close=Decimal("108"),
            is_closed=True,
        )
        assert bull.is_bullish is True
        assert bull.is_bearish is False

    def test_frozen_immutability(self):
        """Candle is frozen — attributes cannot be reassigned."""
        c = Candle(
            symbol="EURUSD", timeframe="H4",
            open_time=datetime(2026, 7, 20, 0, 0, tzinfo=timezone.utc),
            close_time=datetime(2026, 7, 20, 4, 0, tzinfo=timezone.utc),
            open=Decimal("100"), high=Decimal("110"),
            low=Decimal("90"), close=Decimal("105"),
            is_closed=True,
        )
        with pytest.raises(AttributeError):
            c.close = Decimal("999")  # type: ignore

    def test_str_representation(self):
        """__str__ should produce a readable summary."""
        c = Candle(
            symbol="EURUSD", timeframe="H4",
            open_time=datetime(2026, 7, 20, 0, 0, tzinfo=timezone.utc),
            close_time=datetime(2026, 7, 20, 4, 0, tzinfo=timezone.utc),
            open=Decimal("100"), high=Decimal("110"),
            low=Decimal("90"), close=Decimal("105"),
            is_closed=True,
        )
        s = str(c)
        assert "EURUSD" in s
        assert "H4" in s
        assert "closed" in s


class TestCandleSequence:
    """Tests for the CandleSequence dataclass."""

    def test_symbol_mismatch_raises(self):
        """All candles in a sequence must share the same symbol."""
        c = Candle(
            symbol="EURUSD", timeframe="H4",
            open_time=datetime(2026, 7, 20, 0, 0, tzinfo=timezone.utc),
            close_time=datetime(2026, 7, 20, 4, 0, tzinfo=timezone.utc),
            open=Decimal("100"), high=Decimal("110"),
            low=Decimal("90"), close=Decimal("105"),
            is_closed=True,
        )
        with pytest.raises(ValueError, match="symbol"):
            CandleSequence(candles=(c,), symbol="GBPUSD", timeframe="H4")

    def test_timeframe_mismatch_raises(self):
        """All candles in a sequence must share the same timeframe."""
        c = Candle(
            symbol="EURUSD", timeframe="H4",
            open_time=datetime(2026, 7, 20, 0, 0, tzinfo=timezone.utc),
            close_time=datetime(2026, 7, 20, 4, 0, tzinfo=timezone.utc),
            open=Decimal("100"), high=Decimal("110"),
            low=Decimal("90"), close=Decimal("105"),
            is_closed=True,
        )
        with pytest.raises(ValueError, match="timeframe"):
            CandleSequence(candles=(c,), symbol="EURUSD", timeframe="M15")

    def test_len_and_getitem(self):
        """CandleSequence supports len() and indexing."""
        c = Candle(
            symbol="EURUSD", timeframe="H4",
            open_time=datetime(2026, 7, 20, 0, 0, tzinfo=timezone.utc),
            close_time=datetime(2026, 7, 20, 4, 0, tzinfo=timezone.utc),
            open=Decimal("100"), high=Decimal("110"),
            low=Decimal("90"), close=Decimal("105"),
            is_closed=True,
        )
        seq = CandleSequence(candles=(c,), symbol="EURUSD", timeframe="H4")
        assert len(seq) == 1
        assert seq[0] is c
