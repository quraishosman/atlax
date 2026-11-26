import sys
import types

from core.analytics.google_sheets import log_trade_to_sheets
from core.analytics.google_sheets_reporting import report_event
from core.analytics.signal_tracker import SignalTracker


def test_google_sheets_payload_includes_feedback_metadata(monkeypatch):
    calls = []

    class Response:
        status_code = 200
        text = "Success"

    def post(url, json, timeout):
        calls.append((url, json, timeout))
        return Response()

    monkeypatch.setitem(sys.modules, "requests", types.SimpleNamespace(post=post))

    assert log_trade_to_sheets(
        symbol="EURUSD",
        timeframe="M5",
        entry_price="1.1000",
        exit_price="1.1050",
        sl="1.0950",
        tp="1.1100",
        pips=None,
        result="WIN",
        webhook_url="https://example.test/webhook",
        metadata={
            "signal_id": "signal-1",
            "profile": "scalper",
            "atr_grade": "A",
            "sweep_atr_ratio": "0.21",
            "pnl_r": "2.0000",
            "final_outcome": "WIN",
            "exit_reason": "tp2_hit",
        },
    )

    payload = calls[0][1]
    assert payload["profile"] == "scalper"
    assert payload["atr_grade"] == "A"
    assert payload["sweep_atr_ratio"] == "0.21"
    assert payload["pnl_r"] == "2.0000"
    assert payload["schema_version"] == "3.0"
    assert payload["event_type"] == "signal_resolved"
    assert payload["correlation_id"] == "signal-1"
    assert payload["outcome"] == "WIN"
    assert payload["resolution_reason"] == "tp2_hit"
    for removed_column in ("signal_id", "date", "classification", "final_outcome", "exit_reason"):
        assert removed_column not in payload


def test_outcome_feedback_marks_resolution_without_inventing_setup_data():
    feedback = SignalTracker._outcome_feedback(
        {
            "timestamp": 1786700000,
            "feedback_metadata": {"signal_id": "signal-2", "atr_grade": "C"},
        },
        "LOSS",
        "sl_hit",
    )

    assert feedback["signal_id"] == "signal-2"
    assert feedback["atr_grade"] == "C"
    assert feedback["final_outcome"] == "LOSS"
    assert feedback["exit_reason"] == "sl_hit"
    assert feedback["signal_status"] == "RESOLVED"
    assert feedback["created_at"]
    assert feedback["resolved_at"]


def test_phase_two_h1_evidence_uses_existing_notes_column():
    notes = SignalTracker._sheet_notes(
        {
            "feedback_metadata": {
                "htf_bias": "BULLISH",
                "htf_relationship": "OPPOSING",
                "htf_run_length": 2,
                "htf_base_score": 58.0,
                "htf_proposed_score": 48.0,
                "htf_adjustment": -10.0,
            }
        },
        "Signal tracker stop loss hit",
    )

    assert "H1 bias=BULLISH" in notes
    assert "relationship=OPPOSING" in notes
    assert "base=58.0" in notes
    assert "proposed=48.0" in notes
    assert "adjustment=-10.0" in notes


def test_reporting_event_uses_signature_without_transmitting_shared_secret(monkeypatch):
    calls = []

    class Response:
        status_code = 200
        text = "ok"

    def post(url, json, timeout):
        calls.append(json)
        return Response()

    monkeypatch.setenv("ATLAX_GOOGLE_SHEETS_SHARED_SECRET", "local-only-secret")
    monkeypatch.setitem(sys.modules, "requests", types.SimpleNamespace(post=post))

    assert report_event(
        "signal_formed",
        {"symbol": "EURUSD"},
        webhook_url="https://example.test/report",
        event_id="signal_formed:1",
        correlation_id="1",
    )

    payload = calls[0]
    assert payload["event_type"] == "signal_formed"
    assert payload["signature"]
    assert "auth_token" not in payload
    assert "local-only-secret" not in str(payload)
