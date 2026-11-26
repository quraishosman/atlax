from decimal import Decimal

from core.analytics.signal_tracker import SignalTracker


class _MemoryState:
    def __init__(self):
        self.values = {}

    def get(self, key, default=None):
        return self.values.get(key, default)

    def set(self, key, value):
        self.values[key] = value


class _Journal:
    def __init__(self):
        self.outcomes = []

    def update_signal_outcome(self, **kwargs):
        self.outcomes.append(kwargs)

    def mark_signal_expired(self, record_id):
        return None


def test_signal_pnl_r_uses_direction_and_entry_to_stop_distance():
    buy = {
        "direction": "BUY",
        "entry_price": "1.1000",
        "sl": "1.0900",
    }
    sell = {
        "direction": "SELL",
        "entry_price": "1.1000",
        "sl": "1.1100",
    }

    assert SignalTracker._calculate_signal_pnl_r(buy, Decimal("1.1200")) == Decimal("2.0000")
    assert SignalTracker._calculate_signal_pnl_r(sell, Decimal("1.0800")) == Decimal("2.0000")
    assert SignalTracker._calculate_signal_pnl_r(buy, Decimal("1.0900")) == Decimal("-1.0000")


def test_signal_pnl_r_returns_unknown_for_invalid_price_references():
    assert SignalTracker._calculate_signal_pnl_r(
        {"direction": "BUY", "entry_price": "1.1000", "sl": "1.1000"},
        Decimal("1.1200"),
    ) is None
    assert SignalTracker._calculate_signal_pnl_r(
        {"direction": "UNKNOWN", "entry_price": "1.1000", "sl": "1.0900"},
        Decimal("1.1200"),
    ) is None


def test_target_resolution_writes_calculated_pnl_r_not_placeholder():
    journal = _Journal()
    tracker = SignalTracker(
        runtime_store=_MemoryState(),
        journal=journal,
        db_path="unused.sqlite",
    )
    tracker.register_prediction(
        alert_id="signal-r-1",
        symbol="EURUSD",
        timeframe="H1",
        direction="BUY",
        target=Decimal("1.1200"),
        midpoint=Decimal("1.1100"),
        sl=Decimal("1.0900"),
        entry_price=Decimal("1.1000"),
        telegram_message_id=None,
        chat_id="admin",
    )

    tracker.check_market_data(
        symbol="EURUSD",
        current_high=1.1200,
        current_low=1.1005,
    )

    assert journal.outcomes[0]["record_id"] == "signal-r-1"
    assert journal.outcomes[0]["pnl"] == Decimal("2.0000")


def test_outcome_feedback_includes_signal_pnl_r():
    feedback = SignalTracker._outcome_feedback(
        {"feedback_metadata": {"signal_id": "signal-r-2"}},
        "WIN",
        "tp2_hit",
        pnl_r=Decimal("1.7500"),
    )

    assert feedback["pnl_r"] == "1.7500"
