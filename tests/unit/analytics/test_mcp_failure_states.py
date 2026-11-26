from core.analytics.mcp_server import (
    get_daily_performance,
    get_historical_performance,
    get_all_trades,
    get_weekly_breakdown,
    get_journal_notes,
    get_market_context,
    get_recent_trades,
    get_rolling_baseline,
)


def test_missing_journal_is_unknown_across_analytics_queries(tmp_path):
    missing = str(tmp_path / "missing.sqlite")

    assert get_daily_performance(missing)["status"] == "UNKNOWN"
    assert get_historical_performance(missing)["status"] == "UNKNOWN"
    assert get_all_trades(missing)["status"] == "UNKNOWN"
    assert get_weekly_breakdown(missing)["status"] == "UNKNOWN"
    assert get_journal_notes(missing)["status"] == "UNKNOWN"
    assert get_market_context(missing)["status"] == "UNKNOWN"
    assert get_recent_trades(missing)["status"] == "UNKNOWN"
    assert get_rolling_baseline(missing)["status"] == "UNKNOWN"


def test_missing_journal_does_not_claim_zero_performance(tmp_path):
    result = get_daily_performance(str(tmp_path / "missing.sqlite"))

    assert result["status"] == "UNKNOWN"
    assert result["total_trades"] == 0
    assert result["win_rate_pct"] == 0.0
    assert result["reason"] == "Signal journal is unavailable."
