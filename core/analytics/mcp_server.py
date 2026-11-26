"""
ATLAX MCP Analytics Server.

Implements a secure, read-only Model Context Protocol (MCP) gateway that
exposes ATLAX analytics data to the AI Advisory Engine.

Per docs/16_SECURITY.md:
    - AI must access data ONLY through controlled, audited, least-privilege tools.
    - AI must never receive direct database credentials, filesystem access, or
      execution permissions.
    - Every MCP request must be validated, sanitized, and logged.
    - All responses are sanitized: no account numbers, broker IDs, or secrets.

Exposed Tools (read-only / compute-only):
    - get_daily_performance:     Aggregated stats for today's trades.
    - get_recent_trades:         Sanitized list of today's closed trades.
    - get_historical_performance: Aggregated stats over the last N days.
    - get_all_trades:            Sanitized trade list over the last N days.
    - get_weekly_breakdown:      Stats grouped by profile, timeframe, direction.
    - get_journal_notes:         Sanitized trader notes on recent trades.
    - get_market_context:        Pairs recent trades with nearby economic events.
    - get_rolling_baseline:      30-day baseline win rate and PnL averages.
    - get_public_signal_performance: Aggregate subscriber-safe signal outcomes.
    - get_heatmap_snapshot:       Sanitized configured heatmap state.
    - get_pair_correlation:       Compute correlation for configured pairs.
    - get_public_faq:              Public product FAQ without internal details.

All tools use parameterized queries only. No raw SQL passthrough.

Authority: docs/16_SECURITY.md
           docs/12_ANALYTICS_ENGINE.md
"""

from __future__ import annotations

import json
import logging
import math
import re
import sqlite3
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from core.analytics.faq_knowledge import load_public_faq
from core.analytics.signal_passport import get_signal_passport_performance
from core.analytics.signal_replay_reporting import public_signal_replay_report
from core.settings.storage_config import load_storage_config
from core.storage.analytics_snapshot import connect_journal

logger = logging.getLogger("atlax.analytics.mcp_server")

# Maximum number of trades returned per request (caps excessive queries)
_MAX_TRADE_LIMIT = 200
_PUBLIC_SYMBOL_RE = re.compile(r"^[A-Z0-9._-]{1,20}$")
_PUBLIC_TIMEFRAMES = {"M5", "M15", "M30", "H1", "H4", "D1", "W1", "MN"}


def _items_ok(items: list[dict[str, Any]]) -> dict[str, Any]:
    """Return a successful list-tool envelope."""
    return {"status": "OK", "items": items, "count": len(items)}


def _items_unknown(reason: str) -> dict[str, Any]:
    """Return an explicit unavailable result instead of a false empty report."""
    return {"status": "UNKNOWN", "items": [], "count": 0, "reason": reason}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_today_iso_range() -> tuple[str, str]:
    """Return UTC start and end ISO strings for the current calendar day."""
    today_utc = datetime.now(timezone.utc).date()
    start = datetime(today_utc.year, today_utc.month, today_utc.day, 0, 0, 0, tzinfo=timezone.utc)
    end   = datetime(today_utc.year, today_utc.month, today_utc.day, 23, 59, 59, tzinfo=timezone.utc)
    return start.isoformat(), end.isoformat()


def _get_range_for_days(days: int) -> tuple[str, str]:
    """Return UTC ISO range for the last `days` calendar days up to now."""
    now = datetime.now(timezone.utc)
    start = (now - timedelta(days=days)).replace(hour=0, minute=0, second=0, microsecond=0)
    return start.isoformat(), now.isoformat()


def _aggregate_rows(conn: sqlite3.Connection, start: str, end: str) -> dict[str, Any]:
    """Compute top-level aggregate metrics for a date range."""
    # Only count resolved outcomes (tp2_hit, sl_hit, closed trades)
    row = conn.execute(
        """
        SELECT
            COUNT(*) AS total,
            SUM(CASE WHEN CAST(pnl AS REAL) > 0 THEN 1 ELSE 0 END) AS winners,
            SUM(CASE WHEN CAST(pnl AS REAL) < 0 THEN 1 ELSE 0 END) AS losers,
            SUM(CAST(pnl AS REAL)) AS total_pnl,
            AVG(confidence_score) AS avg_conf
        FROM journal_entries
        WHERE closed_at >= ? AND closed_at <= ?
          AND exit_reason NOT IN ('signal_pending')
        """,
        (start, end),
    ).fetchone()
    # Also count signals sent (includes pending)
    signals_row = conn.execute(
        """
        SELECT
            COUNT(*) AS total_sent,
            SUM(CASE WHEN exit_reason = 'signal_pending' THEN 1 ELSE 0 END) AS open
        FROM journal_entries
        WHERE closed_at >= ? AND closed_at <= ?
        """,
        (start, end),
    ).fetchone()
    total    = row[0] or 0
    winners  = row[1] or 0
    losers   = row[2] or 0
    total_pnl = round(row[3] or 0.0, 2)
    avg_conf  = round(row[4] or 0.0, 1)
    win_rate  = round((winners / total * 100) if total > 0 else 0.0, 1)
    return {
        "total_trades": total,
        "winners": winners,
        "losers": losers,
        "win_rate_pct": win_rate,
        "total_pnl": total_pnl,
        "avg_confidence": avg_conf,
        "signals_sent": signals_row[0] or 0,
        "open_signals": signals_row[1] or 0,
    }


def _group_by_field(conn: sqlite3.Connection, field: str, start: str, end: str) -> dict[str, Any]:
    """Group performance metrics by a single field (profile, symbol, timeframe, etc.)."""
    result: dict[str, Any] = {}
    for r in conn.execute(
        f"""
        SELECT {field},
               COUNT(*) AS n,
               SUM(CASE WHEN CAST(pnl AS REAL) > 0 THEN 1 ELSE 0 END) AS w,
               SUM(CAST(pnl AS REAL)) AS pnl
        FROM journal_entries
        WHERE closed_at >= ? AND closed_at <= ?
          AND exit_reason NOT IN ('signal_pending')
        GROUP BY {field}
        """,
        (start, end),
    ):
        key = r[0] or "unknown"
        result[key] = {
            "trades": r[1],
            "winners": r[2],
            "win_rate_pct": round((r[2] / r[1] * 100) if r[1] > 0 else 0.0, 1),
            "pnl": round(r[3] or 0.0, 2),
        }
    return result


def _public_group_by_field(conn: sqlite3.Connection, field: str, start: str, end: str) -> dict[str, Any]:
    """Return aggregate signal results without account or PnL data."""
    result: dict[str, Any] = {}
    for row in conn.execute(
        f"""
        SELECT {field},
               COUNT(*) AS n,
               SUM(CASE WHEN CAST(pnl AS REAL) > 0 THEN 1 ELSE 0 END) AS w,
               SUM(CASE WHEN CAST(pnl AS REAL) < 0 THEN 1 ELSE 0 END) AS l
        FROM journal_entries
        WHERE closed_at >= ? AND closed_at <= ?
          AND exit_reason NOT IN ('signal_pending')
        GROUP BY {field}
        """,
        (start, end),
    ):
        key = str(row[0] or "UNKNOWN")
        trades = int(row[1] or 0)
        winners = int(row[2] or 0)
        result[key] = {
            "signals_resolved": trades,
            "wins": winners,
            "losses": int(row[3] or 0),
            "win_rate_pct": round((winners / trades * 100) if trades else 0.0, 1),
        }
    return result


def get_public_signal_performance(db_path: str, days: int = 30) -> dict[str, Any]:
    """Return public ATLAX signal outcomes without private account metrics."""
    logger.info('{"event":"mcp_tool_call","tool":"get_public_signal_performance","days":%d}', days)
    try:
        passport = get_signal_passport_performance(db_path, days=days)
        if passport.get("status") != "OK":
            return {
                "period_days": passport.get("period_days", days),
                "total_signals": 0,
                "resolved_signals": 0,
                "wins": 0,
                "losses": 0,
                "win_rate_pct": None,
                "open_signals": 0,
                "by_symbol": {},
                "by_profile": {},
                "by_timeframe": {},
                "status": "UNKNOWN",
                "reason": passport.get("reason", "Signal Passport is unavailable."),
            }
        result = {
            "status": "OK",
            "period_days": passport["period_days"],
            "total_signals": passport["total_signals"],
            "resolved_signals": passport["resolved_signals"],
            "wins": passport["wins"],
            "losses": passport["losses"],
            "win_rate_pct": passport["win_rate_pct"],
            "open_signals": passport["open_signals"],
            "unknown_outcomes": passport["unknown_outcomes"],
            "by_symbol": passport["by_pair"],
            "by_profile": passport["by_style"],
            "by_timeframe": passport["by_timeframe"],
        }
        logger.info(
            '{"event":"mcp_tool_result","tool":"get_public_signal_performance","resolved":%d}',
            result["resolved_signals"],
        )
        return result
    except Exception as exc:
        logger.error(
            '{"event":"mcp_tool_error","tool":"get_public_signal_performance","error":"%s"}',
            str(exc)[:200],
        )
        return {
            "status": "UNKNOWN",
            "period_days": min(max(1, int(days)), 365),
            "total_signals": 0,
            "resolved_signals": 0,
            "wins": 0,
            "losses": 0,
            "win_rate_pct": None,
            "open_signals": 0,
            "by_symbol": {},
            "by_profile": {},
            "by_timeframe": {},
            "reason": "Public signal performance is unavailable.",
        }


def get_public_signal_replay_report(db_path: str, days: int = 30) -> dict[str, Any]:
    """Return sanitized replay coverage for subscriber-safe ATLAX AI context."""
    bounded_days = min(max(int(days), 1), 365)
    logger.info(
        '{"event":"mcp_tool_call","tool":"get_public_signal_replay_report","days":%d}',
        bounded_days,
    )
    try:
        result = public_signal_replay_report(db_path, days=bounded_days)
        logger.info(
            '{"event":"mcp_tool_result","tool":"get_public_signal_replay_report","status":"%s"}',
            str(result.get("status", "UNKNOWN")),
        )
        return result
    except Exception as exc:  # noqa: BLE001 - MCP must fail closed
        logger.error(
            '{"event":"mcp_tool_error","tool":"get_public_signal_replay_report","error":"%s"}',
            str(exc)[:200],
        )
        return {
            "status": "UNKNOWN",
            "period_days": bounded_days,
            "reason": "Signal Replay coverage is unavailable.",
        }


def get_public_faq(path: str, max_chars: int = 12000) -> dict[str, Any]:
    """Read the approved public FAQ for ATLAX AI context."""
    logger.info('{"event":"mcp_tool_call","tool":"get_public_faq"}')
    content = load_public_faq(path, max_chars=max_chars)
    if content.startswith("FAQ status: UNKNOWN"):
        logger.info('{"event":"mcp_faq_unavailable"}')
        return {"status": "UNKNOWN", "content": content}
    return {"status": "OK", "content": content}


# ---------------------------------------------------------------------------
# Prop-Firm advisory tools (read-only / sanitized)
# ---------------------------------------------------------------------------

def _prop_firm_value(source: object, key: str, default: Any = None) -> Any:
    if isinstance(source, dict):
        return source.get(key, default)
    return getattr(source, key, default)


def _prop_firm_scalar(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    enum_value = getattr(value, "value", None)
    if enum_value is not None:
        return str(enum_value)
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def _prop_firm_limit(value: object) -> dict[str, Any] | None:
    if value is None:
        return None
    mode = _prop_firm_scalar(_prop_firm_value(value, "mode"))
    basis = _prop_firm_scalar(_prop_firm_value(value, "basis"))
    amount = _prop_firm_scalar(_prop_firm_value(value, "amount"))
    percentage = _prop_firm_scalar(_prop_firm_value(value, "percentage"))
    reset_timezone = _prop_firm_scalar(_prop_firm_value(value, "reset_timezone"))
    if not mode or not basis or (amount is None and percentage is None):
        return None
    return {
        "mode": mode,
        "basis": basis,
        "amount": amount,
        "percentage": percentage,
        "reset_timezone": reset_timezone,
    }


def get_prop_firm_rule_profile(profile: object | None) -> dict[str, Any]:
    """Return only a verified, source-backed Prop-Firm rule profile."""
    logger.info('{"event":"mcp_tool_call","tool":"get_prop_firm_rule_profile"}')
    if profile is None:
        return {"status": "UNKNOWN", "reason": "No verified Prop-Firm profile is selected."}

    profile_id = str(_prop_firm_value(profile, "profile_id", "") or "").strip()
    version = str(_prop_firm_value(profile, "version", "") or "").strip()
    required = (
        profile_id,
        version,
        str(_prop_firm_value(profile, "firm_name", "") or "").strip(),
        str(_prop_firm_value(profile, "program_name", "") or "").strip(),
        str(_prop_firm_value(profile, "phase", "") or "").strip(),
        str(_prop_firm_value(profile, "account_type", "") or "").strip(),
        str(_prop_firm_value(profile, "currency", "") or "").strip(),
    )
    source_urls = tuple(
        str(item).strip()
        for item in (_prop_firm_value(profile, "source_urls", ()) or ())
        if str(item).strip().lower().startswith(("https://", "http://"))
    )
    overall_loss = _prop_firm_limit(_prop_firm_value(profile, "overall_loss"))
    daily_loss = _prop_firm_limit(_prop_firm_value(profile, "daily_loss"))
    retrieved_at = _prop_firm_scalar(_prop_firm_value(profile, "source_retrieved_at"))
    if not all(required) or not source_urls or not overall_loss or not retrieved_at:
        logger.warning(
            '{"event":"mcp_prop_firm_profile_unknown","reason":"incomplete_or_unverified"}'
        )
        return {
            "status": "UNKNOWN",
            "reason": "The selected Prop-Firm profile is incomplete or not source-verified.",
        }

    result = {
        "status": "OK",
        "profile": {
            "profile_id": profile_id,
            "firm_name": required[2],
            "program_name": required[3],
            "phase": required[4],
            "account_type": required[5],
            "currency": required[6].upper(),
            "version": version,
            "content_hash": str(_prop_firm_value(profile, "content_hash", "") or ""),
            "nominal_balance": _prop_firm_scalar(_prop_firm_value(profile, "nominal_balance")),
            "overall_loss": overall_loss,
            "daily_loss": daily_loss,
            "minimum_trading_days": _prop_firm_scalar(
                _prop_firm_value(profile, "minimum_trading_days")
            ),
            "max_position_size": _prop_firm_scalar(
                _prop_firm_value(profile, "max_position_size")
            ),
            "source_urls": list(source_urls),
            "source_retrieved_at": retrieved_at,
            "effective_at": _prop_firm_scalar(_prop_firm_value(profile, "effective_at")),
        },
    }
    logger.info(
        '{"event":"mcp_tool_result","tool":"get_prop_firm_rule_profile","profile_id":"%s","version":"%s"}',
        profile_id[:80],
        version[:80],
    )
    return result


def get_prop_firm_risk_budget(
    control_state: object | None,
    snapshot: object | None = None,
) -> dict[str, Any]:
    """Return a sanitized read-only protection-budget snapshot."""
    logger.info('{"event":"mcp_tool_call","tool":"get_prop_firm_risk_budget"}')
    selected_profile_id = str(
        _prop_firm_value(control_state, "selected_profile_id", "") or ""
    ).strip()
    selected_profile_version = str(
        _prop_firm_value(control_state, "selected_profile_version", "") or ""
    ).strip()
    if not selected_profile_id or not selected_profile_version:
        return {"status": "UNKNOWN", "reason": "No verified Prop-Firm profile is selected."}

    snapshot_values = snapshot if isinstance(snapshot, dict) else {}
    allowed_snapshot_fields = (
        "status",
        "blocked",
        "reason",
        "remaining_overall_buffer",
        "remaining_daily_buffer",
        "recovery_mode",
        "risk_multiplier",
        "evaluated_at",
    )
    budget = {
        key: _prop_firm_scalar(snapshot_values[key])
        for key in allowed_snapshot_fields
        if key in snapshot_values
    }
    result = {
        "status": "OK" if budget else "UNKNOWN",
        "enabled": bool(_prop_firm_value(control_state, "enabled", False)),
        "paused": bool(_prop_firm_value(control_state, "paused", False)),
        "selected_profile_id": selected_profile_id,
        "selected_profile_version": selected_profile_version,
        "budget": budget,
    }
    if not budget:
        result["reason"] = "No validated runtime protection snapshot is available."
    logger.info(
        '{"event":"mcp_tool_result","tool":"get_prop_firm_risk_budget","status":"%s"}',
        result["status"],
    )
    return result


def get_heatmap_snapshot(path: str, symbols: list[str] | None = None) -> dict[str, Any]:
    """Read the configured, sanitized heatmap snapshot for subscriber context."""
    logger.info('{"event":"mcp_tool_call","tool":"get_heatmap_snapshot"}')
    allowed = {str(symbol).strip().upper() for symbol in (symbols or []) if str(symbol).strip()}
    try:
        with open(path, "r", encoding="utf-8") as handle:
            raw = json.load(handle)
        raw_items = raw.items() if isinstance(raw, dict) else []
        pairs = []
        for raw_symbol, raw_info in raw_items:
            symbol = str(raw_symbol).strip().upper()
            if not _PUBLIC_SYMBOL_RE.fullmatch(symbol) or (allowed and symbol not in allowed):
                continue
            info = raw_info if isinstance(raw_info, dict) else {}
            try:
                distance = round(float(info.get("distance_pips", 0.0)), 1)
            except (TypeError, ValueError):
                distance = None
            pairs.append({
                "symbol": symbol,
                "timeframe": str(info.get("timeframe", "UNKNOWN")),
                "status": str(info.get("status", "UNKNOWN")),
                "bias": str(info.get("bias", "UNKNOWN")),
                "distance_pips": distance,
            })
        return {"status": "OK", "pairs": pairs[:100]}
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        logger.info('{"event":"mcp_heatmap_unavailable","reason":"%s"}', str(exc)[:120])
        return {"pairs": [], "status": "UNKNOWN"}


def get_pair_correlation(
    symbol_a: str,
    symbol_b: str,
    allowed_symbols: list[str],
    timeframe: str = "H1",
    periods: int = 50,
    rates_reader=None,
) -> dict[str, Any]:
    """Compute Pearson correlation over sanitized close-price returns."""
    first = str(symbol_a or "").strip().upper()
    second = str(symbol_b or "").strip().upper()
    allowed = {str(symbol).strip().upper() for symbol in allowed_symbols}
    timeframe = str(timeframe or "").strip().upper()
    periods = min(max(int(periods), 20), 200)
    if (
        not _PUBLIC_SYMBOL_RE.fullmatch(first)
        or not _PUBLIC_SYMBOL_RE.fullmatch(second)
        or first not in allowed
        or second not in allowed
        or timeframe not in _PUBLIC_TIMEFRAMES
    ):
        return {"status": "UNKNOWN", "reason": "Pair or timeframe is not configured."}
    if first == second:
        return {
            "status": "OK", "symbol_a": first, "symbol_b": second,
            "timeframe": timeframe, "correlation": 1.0, "observations": periods - 1,
        }
    if not callable(rates_reader):
        return {"status": "UNKNOWN", "reason": "Live market history is unavailable."}

    try:
        first_prices = [float(value) for value in rates_reader(first, timeframe, periods)]
        second_prices = [float(value) for value in rates_reader(second, timeframe, periods)]
        count = min(len(first_prices), len(second_prices))
        if count < 21:
            return {"status": "UNKNOWN", "reason": "At least 21 aligned closes are required."}
        first_returns = [first_prices[index] / first_prices[index - 1] - 1.0 for index in range(1, count)]
        second_returns = [second_prices[index] / second_prices[index - 1] - 1.0 for index in range(1, count)]
        mean_a = sum(first_returns) / len(first_returns)
        mean_b = sum(second_returns) / len(second_returns)
        covariance = sum((a - mean_a) * (b - mean_b) for a, b in zip(first_returns, second_returns))
        variance_a = sum((a - mean_a) ** 2 for a in first_returns)
        variance_b = sum((b - mean_b) ** 2 for b in second_returns)
        if variance_a <= 0 or variance_b <= 0:
            return {"status": "UNKNOWN", "reason": "Price history has no measurable variation."}
        correlation = covariance / math.sqrt(variance_a * variance_b)
        return {
            "status": "OK",
            "symbol_a": first,
            "symbol_b": second,
            "timeframe": timeframe,
            "correlation": round(max(-1.0, min(1.0, correlation)), 3),
            "observations": len(first_returns),
        }
    except Exception as exc:
        logger.warning('{"event":"mcp_correlation_failed","error":"%s"}', str(exc)[:160])
        return {"status": "UNKNOWN", "reason": "Live market history is unavailable."}


# ---------------------------------------------------------------------------
# Tool 1: get_daily_performance
# ---------------------------------------------------------------------------

def get_daily_performance(db_path: str) -> dict[str, Any]:
    """
    Compute aggregated performance stats for today's closed trades.
    No account IDs, broker data, lot sizes, or credentials in response.
    """
    logger.info('{"event":"mcp_tool_call","tool":"get_daily_performance"}')

    if not load_storage_config().uses_postgres and not Path(db_path).exists():
        logger.warning('{"event":"mcp_journal_not_found","db_path":"%s"}', db_path)
        return {
            "status": "UNKNOWN",
            "date": date.today().isoformat(),
            "total_trades": 0, "winners": 0, "losers": 0,
            "win_rate_pct": 0.0, "total_pnl": 0.0, "avg_confidence": 0.0,
            "best_symbol": None, "worst_symbol": None,
            "by_profile": {}, "by_symbol": {},
            "reason": "Signal journal is unavailable.",
        }

    start_iso, end_iso = _get_today_iso_range()

    try:
        with connect_journal(db_path) as conn:
            conn.row_factory = sqlite3.Row
            agg        = _aggregate_rows(conn, start_iso, end_iso)
            by_profile = _group_by_field(conn, "profile", start_iso, end_iso)
            by_symbol  = _group_by_field(conn, "symbol",  start_iso, end_iso)
            best   = max(by_symbol, key=lambda s: by_symbol[s]["pnl"]) if by_symbol else None
            worst  = min(by_symbol, key=lambda s: by_symbol[s]["pnl"]) if by_symbol else None

        result = {
            "status": "OK",
            "date": date.today().isoformat(),
            **agg,
            "best_symbol": best,
            "worst_symbol": worst,
            "by_profile": by_profile,
            "by_symbol": by_symbol,
        }
        logger.info(
            '{"event":"mcp_tool_result","tool":"get_daily_performance","total_trades":%d}',
            agg["total_trades"],
        )
        return result

    except Exception as exc:
        logger.error(
            '{"event":"mcp_tool_error","tool":"get_daily_performance","error":"%s"}', str(exc)[:200]
        )
        return {
            "status": "UNKNOWN",
            "error": "Query failed.",
            "reason": "Signal journal query failed.",
            "date": date.today().isoformat(),
        }


# ---------------------------------------------------------------------------
# Tool 2: get_recent_trades
# ---------------------------------------------------------------------------

def get_recent_trades(db_path: str, limit: int = 20) -> dict[str, Any]:
    """Return a sanitized list of today's closed trades."""
    logger.info('{"event":"mcp_tool_call","tool":"get_recent_trades","limit":%d}', limit)
    limit = min(max(1, limit), _MAX_TRADE_LIMIT)

    if not load_storage_config().uses_postgres and not Path(db_path).exists():
        return _items_unknown("Signal journal is unavailable.")

    start_iso, end_iso = _get_today_iso_range()

    try:
        with connect_journal(db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """
                SELECT symbol, timeframe, profile, strategy_name, direction,
                       confidence_score, entry_price, sl_price, tp_price,
                       exit_price, pnl, closed_at, exit_reason, tags
                FROM journal_entries
                WHERE closed_at >= ? AND closed_at <= ?
                ORDER BY closed_at DESC
                LIMIT ?
                """,
                (start_iso, end_iso, limit),
            ).fetchall()

        trades = [dict(r) for r in rows]
        logger.info(
            '{"event":"mcp_tool_result","tool":"get_recent_trades","returned":%d}', len(trades)
        )
        return _items_ok(trades)

    except Exception as exc:
        logger.error(
            '{"event":"mcp_tool_error","tool":"get_recent_trades","error":"%s"}', str(exc)[:200]
        )
        return _items_unknown("Signal journal query failed.")


# ---------------------------------------------------------------------------
# Tool 3: get_historical_performance
# ---------------------------------------------------------------------------

def get_historical_performance(db_path: str, days: int = 7) -> dict[str, Any]:
    """
    Aggregated performance statistics for the last `days` calendar days.
    Used by the Natural Language Chatbot to answer historical questions.
    """
    logger.info(
        '{"event":"mcp_tool_call","tool":"get_historical_performance","days":%d}', days
    )
    days = min(max(1, days), 365)

    if not load_storage_config().uses_postgres and not Path(db_path).exists():
        return {
            "status": "UNKNOWN",
            "days": days,
            "total_trades": None,
            "reason": "Signal journal is unavailable.",
        }

    start_iso, end_iso = _get_range_for_days(days)

    try:
        with connect_journal(db_path) as conn:
            conn.row_factory = sqlite3.Row
            agg        = _aggregate_rows(conn, start_iso, end_iso)
            by_symbol  = _group_by_field(conn, "symbol",    start_iso, end_iso)
            by_profile = _group_by_field(conn, "profile",   start_iso, end_iso)
            by_tf      = _group_by_field(conn, "timeframe", start_iso, end_iso)

        result = {
            "status": "OK",
            "period_days": days,
            "from_date": start_iso[:10],
            "to_date":   end_iso[:10],
            **agg,
            "by_symbol":    by_symbol,
            "by_profile":   by_profile,
            "by_timeframe": by_tf,
        }
        logger.info(
            '{"event":"mcp_tool_result","tool":"get_historical_performance","total_trades":%d}',
            agg["total_trades"],
        )
        return result

    except Exception as exc:
        logger.error(
            '{"event":"mcp_tool_error","tool":"get_historical_performance","error":"%s"}',
            str(exc)[:200],
        )
        return {
            "status": "UNKNOWN",
            "error": "Query failed.",
            "reason": "Signal journal query failed.",
            "days": days,
        }


# ---------------------------------------------------------------------------
# Tool 4: get_all_trades
# ---------------------------------------------------------------------------

def get_all_trades(db_path: str, days: int = 30, limit: int = 100) -> dict[str, Any]:
    """
    Sanitized list of closed trades over the last `days` days.
    Used by the chatbot and weekly optimizer.
    """
    logger.info(
        '{"event":"mcp_tool_call","tool":"get_all_trades","days":%d,"limit":%d}', days, limit
    )
    days  = min(max(1, days), 365)
    limit = min(max(1, limit), _MAX_TRADE_LIMIT)

    if not load_storage_config().uses_postgres and not Path(db_path).exists():
        return _items_unknown("Signal journal is unavailable.")

    start_iso, end_iso = _get_range_for_days(days)

    try:
        with connect_journal(db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """
                SELECT symbol, timeframe, profile, direction, confidence_score,
                       entry_price, sl_price, tp_price, exit_price, pnl,
                       closed_at, exit_reason, tags
                FROM journal_entries
                WHERE closed_at >= ? AND closed_at <= ?
                ORDER BY closed_at DESC
                LIMIT ?
                """,
                (start_iso, end_iso, limit),
            ).fetchall()

        trades = [dict(r) for r in rows]
        logger.info(
            '{"event":"mcp_tool_result","tool":"get_all_trades","returned":%d}', len(trades)
        )
        return _items_ok(trades)

    except Exception as exc:
        logger.error(
            '{"event":"mcp_tool_error","tool":"get_all_trades","error":"%s"}', str(exc)[:200]
        )
        return _items_unknown("Signal journal query failed.")


# ---------------------------------------------------------------------------
# Tool 5: get_weekly_breakdown
# ---------------------------------------------------------------------------

def get_weekly_breakdown(db_path: str) -> dict[str, Any]:
    """
    7-day breakdown grouped by profile, timeframe, direction, and symbol.
    Used by the Weekly Strategy Optimizer to find statistical patterns
    and generate config suggestions.
    """
    logger.info('{"event":"mcp_tool_call","tool":"get_weekly_breakdown"}')

    if not load_storage_config().uses_postgres and not Path(db_path).exists():
        return {
            "status": "UNKNOWN",
            "period_days": 7,
            "total_trades": None,
            "reason": "Signal journal is unavailable.",
        }

    start_iso, end_iso = _get_range_for_days(7)

    try:
        with connect_journal(db_path) as conn:
            conn.row_factory = sqlite3.Row
            agg        = _aggregate_rows(conn, start_iso, end_iso)
            by_symbol  = _group_by_field(conn, "symbol",    start_iso, end_iso)
            by_profile = _group_by_field(conn, "profile",   start_iso, end_iso)
            by_tf      = _group_by_field(conn, "timeframe", start_iso, end_iso)
            by_dir     = _group_by_field(conn, "direction", start_iso, end_iso)

            # Avg confidence for winners vs losers
            conf_row = conn.execute(
                """
                SELECT
                    AVG(CASE WHEN CAST(pnl AS REAL) > 0 THEN confidence_score END) AS win_conf,
                    AVG(CASE WHEN CAST(pnl AS REAL) <= 0 THEN confidence_score END) AS loss_conf
                FROM journal_entries
                WHERE closed_at >= ? AND closed_at <= ?
                """,
                (start_iso, end_iso),
            ).fetchone()

        result = {
            "status": "OK",
            "period_days": 7,
            "from_date": start_iso[:10],
            "to_date":   end_iso[:10],
            **agg,
            "by_symbol":    by_symbol,
            "by_profile":   by_profile,
            "by_timeframe": by_tf,
            "by_direction": by_dir,
            "avg_confidence_winners": round(conf_row[0] or 0.0, 1),
            "avg_confidence_losers":  round(conf_row[1] or 0.0, 1),
        }
        logger.info(
            '{"event":"mcp_tool_result","tool":"get_weekly_breakdown","total_trades":%d}',
            agg["total_trades"],
        )
        return result

    except Exception as exc:
        logger.error(
            '{"event":"mcp_tool_error","tool":"get_weekly_breakdown","error":"%s"}', str(exc)[:200]
        )
        return {
            "status": "UNKNOWN",
            "error": "Query failed.",
            "reason": "Signal journal query failed.",
        }


# ---------------------------------------------------------------------------
# Tool 6: get_journal_notes
# ---------------------------------------------------------------------------

def get_journal_notes(db_path: str, days: int = 7) -> dict[str, Any]:
    """
    Return sanitized trader notes attached to recent trades.
    Used by the AI to analyse psychological patterns.
    """
    logger.info(
        '{"event":"mcp_tool_call","tool":"get_journal_notes","days":%d}', days
    )
    days = min(max(1, days), 90)

    if not load_storage_config().uses_postgres and not Path(db_path).exists():
        return _items_unknown("Signal journal is unavailable.")

    start_iso, _ = _get_range_for_days(days)

    try:
        with connect_journal(db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """
                SELECT symbol, timeframe, profile, direction, pnl,
                       exit_reason, closed_at, trader_notes
                FROM journal_entries
                WHERE trader_notes IS NOT NULL
                  AND TRIM(trader_notes) != ''
                  AND closed_at >= ?
                ORDER BY closed_at DESC
                LIMIT 50
                """,
                (start_iso,),
            ).fetchall()

        notes = [dict(r) for r in rows]
        logger.info(
            '{"event":"mcp_tool_result","tool":"get_journal_notes","returned":%d}', len(notes)
        )
        return _items_ok(notes)

    except Exception as exc:
        logger.error(
            '{"event":"mcp_tool_error","tool":"get_journal_notes","error":"%s"}', str(exc)[:200]
        )
        return _items_unknown("Signal journal query failed.")


# ---------------------------------------------------------------------------
# Tool 7: get_market_context
# ---------------------------------------------------------------------------

def get_market_context(db_path: str, days: int = 1) -> dict[str, Any]:
    """
    Pair recent closed trades with nearby high-impact economic events.
    Uses the ForexFactory free calendar feed. Falls back gracefully if offline.
    """
    logger.info('{"event":"mcp_tool_call","tool":"get_market_context"}')

    if not load_storage_config().uses_postgres and not Path(db_path).exists():
        return _items_unknown("Signal journal is unavailable.")

    start_iso, end_iso = _get_range_for_days(days)

    try:
        from core.analytics.economic_calendar import get_events_near_time

        with connect_journal(db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """
                SELECT symbol, direction, pnl, closed_at, exit_reason
                FROM journal_entries
                WHERE closed_at >= ? AND closed_at <= ?
                ORDER BY closed_at DESC
                LIMIT 50
                """,
                (start_iso, end_iso),
            ).fetchall()

        results: list[dict[str, Any]] = []
        skipped_rows = 0
        for r in rows:
            try:
                closed_dt = datetime.fromisoformat(r["closed_at"])
                if closed_dt.tzinfo is None:
                    closed_dt = closed_dt.replace(tzinfo=timezone.utc)

                # Extract the pair currencies for context filtering
                sym = r["symbol"].upper()
                currencies = [sym[:3], sym[3:6]] if len(sym) >= 6 else None

                nearby = get_events_near_time(
                    closed_dt, window_minutes=30, currencies=currencies
                )
                results.append({
                    "symbol":      r["symbol"],
                    "direction":   r["direction"],
                    "pnl":         r["pnl"],
                    "closed_at":   r["closed_at"],
                    "exit_reason": r["exit_reason"],
                    "nearby_events": nearby,
                })
            except Exception as exc:
                skipped_rows += 1
                logger.warning(
                    '{"event":"mcp_market_context_row_unknown",'
                    '"error":"%s"}',
                    str(exc)[:160],
                )
                continue

        logger.info(
            '{"event":"mcp_tool_result","tool":"get_market_context","trades_enriched":%d}',
            len(results),
        )
        if skipped_rows:
            return {
                "status": "UNKNOWN",
                "items": results,
                "count": len(results),
                "skipped": skipped_rows,
                "reason": "One or more journal rows could not be enriched.",
            }
        return _items_ok(results)

    except Exception as exc:
        logger.error(
            '{"event":"mcp_tool_error","tool":"get_market_context","error":"%s"}', str(exc)[:200]
        )
        return _items_unknown("Market context query failed.")


# ---------------------------------------------------------------------------
# Tool 8: get_rolling_baseline
# ---------------------------------------------------------------------------

def get_rolling_baseline(db_path: str, days: int = 30) -> dict[str, Any]:
    """
    Compute the rolling N-day baseline win rate and PnL averages.
    Used by the Anomaly Detection system to compare today vs historical norms.
    """
    logger.info(
        '{"event":"mcp_tool_call","tool":"get_rolling_baseline","days":%d}', days
    )
    days = min(max(7, days), 365)

    if not load_storage_config().uses_postgres and not Path(db_path).exists():
        return {
            "status": "UNKNOWN",
            "days": days,
            "baseline_win_rate_pct": None,
            "baseline_avg_pnl": None,
            "total_trades": None,
            "reason": "Signal journal is unavailable.",
        }

    start_iso, end_iso = _get_range_for_days(days)

    try:
        with connect_journal(db_path) as conn:
            agg = _aggregate_rows(conn, start_iso, end_iso)
            avg_pnl_row = conn.execute(
                """
                SELECT AVG(CAST(pnl AS REAL))
                FROM journal_entries
                WHERE closed_at >= ? AND closed_at <= ?
                """,
                (start_iso, end_iso),
            ).fetchone()

        avg_pnl = round(avg_pnl_row[0] or 0.0, 2)

        result = {
            "status": "OK",
            "period_days": days,
            "from_date": start_iso[:10],
            "to_date":   end_iso[:10],
            "baseline_win_rate_pct": agg["win_rate_pct"],
            "baseline_avg_pnl": avg_pnl,
            "total_trades": agg["total_trades"],
        }
        logger.info(
            '{"event":"mcp_tool_result","tool":"get_rolling_baseline","baseline_wr":%.1f}',
            agg["win_rate_pct"],
        )
        return result

    except Exception as exc:
        logger.error(
            '{"event":"mcp_tool_error","tool":"get_rolling_baseline","error":"%s"}', str(exc)[:200]
        )
        return {
            "status": "UNKNOWN",
            "error": "Query failed.",
            "reason": "Signal journal query failed.",
            "days": days,
        }


# ---------------------------------------------------------------------------
# get_heatmap_snapshot — read-only view of active CRT heatmap zones
# ---------------------------------------------------------------------------

def _legacy_get_heatmap_snapshot(heatmap_path: str = "data/heatmap.json") -> dict[str, Any]:
    """
    Return the current snapshot of active CRT heatmap zones.
    Each zone is a symbol/timeframe that has a valid, non-invalidated CRT sweep
    forming (parent closed, sweep candle has not violated 65% of parent range).
    """
    logger.info('{"event":"mcp_tool_call","tool":"get_heatmap_snapshot"}')
    result: dict[str, Any] = {"zones": [], "total_zones": 0}
    from core.storage.factory import get_runtime_state
    state = get_runtime_state().get("heatmap.state", {})
    if not isinstance(state, dict) or not state:
        logger.info('{"event":"mcp_tool_result","tool":"get_heatmap_snapshot","zones":0}')
        return result
    try:
        zones = []
        for key, info in state.items():
            zones.append({
                "symbol":        info.get("symbol", ""),
                "timeframe":     info.get("timeframe", ""),
                "bias":          info.get("bias", ""),
                "status":        info.get("status", ""),
                "distance_pips": info.get("distance_pips", 0),
            })
        result["zones"] = zones
        result["total_zones"] = len(zones)
        logger.info(
            '{"event":"mcp_tool_result","tool":"get_heatmap_snapshot","zones":%d}', len(zones)
        )
    except Exception as exc:
        logger.error(
            '{"event":"mcp_tool_error","tool":"get_heatmap_snapshot","error":"%s"}', str(exc)[:200]
        )
        result["error"] = "Could not read heatmap."
    return result


# ---------------------------------------------------------------------------
# get_pair_correlation — compute bias-based pair correlations from heatmap
# ---------------------------------------------------------------------------

def _legacy_get_pair_correlation(heatmap_path: str = "data/heatmap.json") -> dict[str, Any]:
    """
    Return real Pearson price correlation data computed from D1 close history.

    Reads from data/correlation.json which is updated by CorrelationEngine every
    scan cycle from actual MT5 D1 close prices.

    A POSITIVE coefficient (≥+0.70) means two pairs tend to move in the same direction.
    A NEGATIVE coefficient (≤-0.70) means two pairs tend to move inversely.

    This is advisory only and does not guarantee future co-movement.
    """
    logger.info('{"event":"mcp_tool_call","tool":"get_pair_correlation"}')
    result: dict[str, Any] = {
        "positive_correlations": [],
        "negative_correlations": [],
        "uncorrelated": [],
        "note": "Real Pearson correlation from D1 close prices via MT5.",
    }

    from core.storage.factory import get_runtime_state
    data = get_runtime_state().get("correlation.state", {})
    if not isinstance(data, dict) or not data:
        result["note"] = "Correlation data not yet available — collecting D1 price history."
        logger.info('{"event":"mcp_tool_result","tool":"get_pair_correlation","status":"no_data"}')
        return result

    try:
        pairs = data.get("pairs", [])
        result["note"] = data.get("note", result["note"])

        for p in pairs:
            coef  = p.get("coefficient", 0)
            entry = {
                "pair_a":      p.get("pair_a", ""),
                "pair_b":      p.get("pair_b", ""),
                "coefficient": coef,
                "label":       p.get("label", ""),
                "periods":     p.get("periods", 0),
            }
            if coef >= 0.30:
                result["positive_correlations"].append(entry)
            elif coef <= -0.30:
                result["negative_correlations"].append(entry)
            else:
                result["uncorrelated"].append(entry)

        logger.info(
            '{"event":"mcp_tool_result","tool":"get_pair_correlation",'
            '"positive":%d,"negative":%d,"uncorrelated":%d}',
            len(result["positive_correlations"]),
            len(result["negative_correlations"]),
            len(result["uncorrelated"]),
        )
    except Exception as exc:
        logger.error(
            '{"event":"mcp_tool_error","tool":"get_pair_correlation","error":"%s"}', str(exc)[:200]
        )
        result["error"] = "Could not read correlation data."
    return result
