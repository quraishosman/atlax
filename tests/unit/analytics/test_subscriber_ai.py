from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from core.analytics import ai_advisory
from core.analytics.mcp_server import get_pair_correlation, get_public_signal_performance
from core.alerts.subscriber_db import SubscriberDB


def _create_journal(path):
    import sqlite3

    now = datetime.now(timezone.utc)
    with sqlite3.connect(path) as conn:
        conn.execute(
            """
            CREATE TABLE journal_entries (
                symbol TEXT, timeframe TEXT, profile TEXT, pnl REAL,
                confidence_score REAL, closed_at TEXT, exit_reason TEXT
            )
            """
        )
        conn.executemany(
            "INSERT INTO journal_entries VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                ("EURUSD", "H1", "day_trader", 1.0, 80.0, (now - timedelta(hours=2)).isoformat(), "tp2_hit"),
                ("EURUSD", "H1", "day_trader", -1.0, 70.0, (now - timedelta(hours=1)).isoformat(), "sl_hit"),
            ],
        )


def test_public_signal_performance_excludes_private_pnl(tmp_path):
    db_path = tmp_path / "journal.sqlite"
    _create_journal(db_path)

    result = get_public_signal_performance(str(db_path), days=30)

    assert result["wins"] == 1
    assert result["losses"] == 1
    assert "total_pnl" not in result
    assert "pnl" not in str(result).lower()


def test_pair_correlation_requires_configured_symbols_and_computes_returns():
    def rates_reader(symbol, timeframe, periods):
        values = [float(value) for value in range(1, 60)]
        return values if symbol == "EURUSD" else [value * 1.5 for value in values]

    result = get_pair_correlation(
        "EURUSD",
        "GBPUSD",
        ["EURUSD", "GBPUSD"],
        rates_reader=rates_reader,
    )
    rejected = get_pair_correlation("EURUSD", "XAUUSD", ["EURUSD", "GBPUSD"], rates_reader=rates_reader)

    assert result["status"] == "OK"
    assert result["correlation"] == 1.0
    assert rejected["status"] == "UNKNOWN"


def test_subscriber_ai_uses_sanitized_context_and_daily_limit(monkeypatch, tmp_path):
    captured = {}
    config = SimpleNamespace(
        enabled=True,
        ai_subscriber_enabled=True,
        ai_subscriber_daily_limit=1,
        db_path=str(tmp_path / "journal.sqlite"),
        heatmap_path=str(tmp_path / "heatmap.json"),
        ai_model="test-model",
        ai_fallback_model="",
    )
    monkeypatch.setattr(ai_advisory, "get_public_signal_performance", lambda path, days: {
        "period_days": days,
        "wins": 1,
        "losses": 1,
        "win_rate_pct": 50.0,
        "open_signals": 0,
        "by_symbol": {},
        "by_profile": {},
        "by_timeframe": {},
    })
    monkeypatch.setattr(ai_advisory, "get_public_signal_replay_report", lambda path, days: {
        "status": "OK",
        "period_days": days,
        "passport_total_signals": 2,
        "replay_snapshot_count": 2,
        "replay_available_count": 2,
        "replay_coverage_pct": 100.0,
        "outcomes": {"open": 0, "resolved": 2, "wins": 1, "losses": 1, "breakevens": 0, "unknown": 0, "win_rate_pct": 50.0},
        "by_pair": {"EURUSD": {"snapshots": 2, "wins": 1, "losses": 1, "win_rate_pct": 50.0}},
        "by_timeframe": {},
        "by_style": {},
        "by_route": {},
        "by_grade": {},
    })
    monkeypatch.setattr(ai_advisory, "get_heatmap_snapshot", lambda path, symbols=None: {"pairs": []})
    monkeypatch.setattr(ai_advisory, "get_upcoming_high_impact_events", lambda hours: [])
    monkeypatch.setattr(ai_advisory, "_build_client", lambda config: object())
    monkeypatch.setattr(ai_advisory, "_build_fallback_client", lambda config: None)
    monkeypatch.setattr(
        ai_advisory,
        "_call_ai",
        lambda client, fallback, model, fallback_model, prompt: captured.setdefault("prompt", prompt) or "<b>Answer</b> <a href='unsafe'>link</a>",
    )

    engine = ai_advisory.AdvisoryEngine(config, allowed_symbols=["EURUSD", "GBPUSD"])
    answer = engine.answer_subscriber_question("What is ATLAX performance?", "123")
    limited = engine.answer_subscriber_question("Another question", "123")

    assert "<a " not in answer
    assert "Advisory only" in answer
    assert "total_pnl" not in captured["prompt"]
    assert "REPLAY COVERAGE" in captured["prompt"]
    assert "replay_coverage_pct" in captured["prompt"]
    assert "authorized_chat_ids" not in captured["prompt"]
    assert "entry_zone_high" not in captured["prompt"]
    assert "Answer only the question asked" in captured["prompt"]
    assert "question limit" in limited.lower()


def test_subscriber_ai_identity_answer_does_not_load_unrelated_context(monkeypatch, tmp_path):
    config = SimpleNamespace(
        enabled=True,
        ai_subscriber_enabled=True,
        ai_subscriber_max_question_chars=600,
        db_path=str(tmp_path / "journal.sqlite"),
    )
    monkeypatch.setattr(
        ai_advisory,
        "_call_ai",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("identity must not call provider")),
    )

    engine = ai_advisory.AdvisoryEngine(config, allowed_symbols=["EURUSD"])
    answer = engine.answer_subscriber_question("Who are you?", "123")

    assert "I am ATLAX's advisory assistant" in answer
    assert "signals today" not in answer.lower()
    assert "heatmap" not in answer.lower()
    assert "subscription" not in answer.lower()


def test_subscriber_ai_quota_persists_and_enforces_cooldown(tmp_path):
    db = SubscriberDB(
        codes_file=str(tmp_path / "codes.json"),
        subs_file=str(tmp_path / "subscribers.json"),
    )
    assert db.register("123", "tester", "day_trader", "INVITE", subscription_days=30)

    first = db.reserve_ai_request(
        "123", daily_limit=10, per_minute_limit=3,
        cooldown_seconds=10, active_timeout_seconds=120,
    )
    assert first["allowed"] is True
    db.release_ai_request("123")

    second = db.reserve_ai_request(
        "123", daily_limit=10, per_minute_limit=3,
        cooldown_seconds=10, active_timeout_seconds=120,
    )
    assert second["reason"] == "cooldown"

    reloaded = SubscriberDB(
        codes_file=str(tmp_path / "codes.json"),
        subs_file=str(tmp_path / "subscribers.json"),
    )
    assert reloaded.get_ai_usage("123")["daily_count"] == 1


def test_subscriber_ai_abuse_strikes_block_ai_only(tmp_path):
    db = SubscriberDB(
        codes_file=str(tmp_path / "codes.json"),
        subs_file=str(tmp_path / "subscribers.json"),
    )
    db.register("123", "tester", "day_trader", "INVITE", subscription_days=30)

    assert db.record_ai_abuse("123", "security_or_illegal", 3)["blocked"] is False
    assert db.record_ai_abuse("123", "security_or_illegal", 3)["blocked"] is False
    assert db.record_ai_abuse("123", "security_or_illegal", 3)["blocked"] is True
    assert db.is_registered("123") is True
    assert db.reserve_ai_request(
        "123", daily_limit=10, per_minute_limit=3,
        cooldown_seconds=0, active_timeout_seconds=120,
    )["reason"] == "blocked"

    assert db.reset_ai_usage("123") is True
    assert db.get_ai_usage("123")["blocked"] is True
    assert db.set_ai_blocked("123", False) is True
    assert db.get_ai_usage("123")["blocked"] is False


def test_ai_call_uses_fallback_when_primary_provider_fails():
    class _Completions:
        def __init__(self, result=None, error=None):
            self.result = result
            self.error = error

        def create(self, **kwargs):
            if self.error:
                raise self.error
            return self.result

    class _Client:
        def __init__(self, completions):
            self.chat = SimpleNamespace(completions=completions)

    response = SimpleNamespace(
        usage=SimpleNamespace(total_tokens=1),
        choices=[SimpleNamespace(message=SimpleNamespace(content='fallback answer'))],
    )
    primary = _Client(_Completions(error=RuntimeError('primary unavailable')))
    fallback = _Client(_Completions(result=response))

    result = ai_advisory._call_ai(primary, fallback, 'primary-model', 'fallback-model', 'health check')

    assert result == 'fallback answer'
