"""Read-only Signal Passport history and performance queries.

Authority: docs/21_SIGNAL_PASSPORT.md

This module reads the existing SQLite journal. It does not create signals,
make trading decisions, or expose execution-side account data.
"""

from __future__ import annotations

import html
import logging
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable
from core.settings.storage_config import load_storage_config
from core.storage.analytics_snapshot import connect_journal


logger = logging.getLogger("atlax.analytics.signal_passport")

PASSPORT_STATUSES = {"OPEN", "RESOLVED", "EXPIRED", "UNKNOWN"}
FINAL_OUTCOMES = {"WIN", "LOSS", "UNKNOWN"}


def _format_public_datetime(value: object, fallback: str = "UNKNOWN") -> str:
    """Render Passport timestamps without UUID-like or ISO implementation noise."""
    if value in (None, ""):
        return fallback
    raw = str(value).strip()
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return raw or fallback
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _human_label(value: object) -> str:
    raw = str(value or "UNKNOWN").replace("_", " ").strip()
    if raw.isupper() and " " not in raw:
        return raw
    return raw.title()


def _public_invalidation_reason(outcome: str, reason: object) -> str:
    if outcome == "WIN":
        return ""
    normalized = str(reason or "").strip().lower()
    if outcome == "LOSS" or normalized == "stop_loss_hit":
        return "Stop loss reached"
    if normalized in {"", "unknown", "none"}:
        return "No final invalidation reason recorded"
    return _human_label(normalized)


def _period(days: int) -> tuple[int, str, str]:
    bounded_days = min(max(int(days), 1), 365)
    now = datetime.now(timezone.utc)
    start = (now - timedelta(days=bounded_days)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    return bounded_days, start.isoformat(), now.isoformat()


def _empty_bucket() -> dict[str, Any]:
    return {
        "total_signals": 0,
        "open_signals": 0,
        "resolved_signals": 0,
        "wins": 0,
        "losses": 0,
        "unknown_outcomes": 0,
        "win_rate_pct": None,
    }


def _add_record(bucket: dict[str, Any], record: dict[str, Any]) -> None:
    bucket["total_signals"] += 1
    status = record["status"]
    outcome = record["final_outcome"]
    if status == "OPEN":
        bucket["open_signals"] += 1
    if outcome in {"WIN", "LOSS"}:
        bucket["resolved_signals"] += 1
        bucket["wins" if outcome == "WIN" else "losses"] += 1
    else:
        bucket["unknown_outcomes"] += 1

    resolved = bucket["resolved_signals"]
    bucket["win_rate_pct"] = (
        round(bucket["wins"] / resolved * 100, 1) if resolved else None
    )


def _status_and_outcome(row: sqlite3.Row) -> tuple[str, str]:
    status = str(row["signal_status"] or "").upper()
    outcome = str(row["final_outcome"] or "").upper()
    exit_reason = str(row["exit_reason"] or "").lower()

    # Legacy rows predate Passport columns. Derive only from existing journal
    # outcome values and keep anything ambiguous UNKNOWN.
    if status not in PASSPORT_STATUSES:
        if exit_reason == "signal_pending":
            status = "OPEN"
        elif exit_reason in {"tp2_hit", "sl_hit"}:
            status = "RESOLVED"
        else:
            status = "UNKNOWN"
    if outcome not in FINAL_OUTCOMES:
        if exit_reason == "tp2_hit":
            outcome = "WIN"
        elif exit_reason == "sl_hit":
            outcome = "LOSS"
        else:
            outcome = "UNKNOWN"
    return status, outcome


def _public_record(row: sqlite3.Row) -> dict[str, Any]:
    status, outcome = _status_and_outcome(row)
    invalidation_reason = str(row["invalidation_reason"] or "").strip()
    atr_status = str(row["atr_evaluation_status"] or "NOT_APPLICABLE")
    atr_quality = {
        "status": atr_status,
        "grade": row["atr_grade"] or None,
        "method": row["atr_method"] or None,
        "period": row["atr_period"],
        "atr": row["atr_value"] or None,
        "parent_range": row["parent_range"] or None,
        "parent_atr_ratio": row["parent_atr_ratio"] or None,
        "sweep_depth": row["sweep_depth"] or None,
        "sweep_atr_ratio": row["sweep_atr_ratio"] or None,
        "configuration_snapshot_id": row["atr_configuration_snapshot_id"] or None,
        "explanation": row["atr_explanation"] or "",
    }
    key_level_zone = row["key_level_zone"]
    key_level = {
        "status": str(row["key_level_status"] or "NOT_APPLICABLE").upper(),
        "at_htf_zone": (
            True if key_level_zone in (True, 1, "1", "true", "True")
            else False if key_level_zone in (False, 0, "0", "false", "False")
            else None
        ),
        "matched_level_count": row["key_level_match_count"],
        "configuration_snapshot_id": row["key_level_configuration_snapshot_id"] or None,
    }
    return {
        "schema_version": "1.2",
        "signal_id": str(row["record_id"]),
        "pair": str(row["symbol"] or "UNKNOWN"),
        "style": str(row["profile"] or "UNKNOWN"),
        "timeframe": str(row["timeframe"] or "UNKNOWN"),
        "created_at": str(row["created_at"] or row["closed_at"] or "UNKNOWN"),
        "status": status,
        "invalidation_reason": invalidation_reason or "UNKNOWN",
        "final_outcome": outcome,
        "outcome_at": str(row["outcome_at"] or "UNKNOWN"),
        "atr_quality": atr_quality,
        "key_level": key_level,
    }


def _passport_select() -> str:
    return """
        SELECT record_id, symbol, profile, timeframe,
               COALESCE(NULLIF(created_at, ''), closed_at) AS created_at,
               closed_at, exit_reason, signal_status, invalidation_reason,
               final_outcome, outcome_at, atr_method, atr_period, atr_value,
               parent_range, parent_atr_ratio, sweep_depth, sweep_atr_ratio,
               atr_grade, atr_configuration_snapshot_id,
               atr_evaluation_status, atr_explanation,
               key_level_status, key_level_zone, key_level_match_count,
               key_level_configuration_snapshot_id
        FROM journal_entries
    """


def _passport_query_parts(conn: sqlite3.Connection) -> tuple[str, str]:
    """Build a compatible read query for migrated and legacy journals."""
    columns = {row[1] for row in conn.execute("PRAGMA table_info(journal_entries)")}
    created_expr = (
        "COALESCE(NULLIF(created_at, ''), closed_at)"
        if "created_at" in columns
        else "closed_at"
    )
    record_expr = "record_id" if "record_id" in columns else "CAST(rowid AS TEXT)"
    status_expr = "signal_status" if "signal_status" in columns else "''"
    reason_expr = "invalidation_reason" if "invalidation_reason" in columns else "''"
    outcome_expr = "final_outcome" if "final_outcome" in columns else "''"
    outcome_at_expr = "outcome_at" if "outcome_at" in columns else "''"
    atr_method_expr = "atr_method" if "atr_method" in columns else "''"
    atr_period_expr = "atr_period" if "atr_period" in columns else "NULL"
    atr_value_expr = "atr_value" if "atr_value" in columns else "NULL"
    parent_range_expr = "parent_range" if "parent_range" in columns else "NULL"
    parent_ratio_expr = "parent_atr_ratio" if "parent_atr_ratio" in columns else "NULL"
    sweep_depth_expr = "sweep_depth" if "sweep_depth" in columns else "NULL"
    sweep_ratio_expr = "sweep_atr_ratio" if "sweep_atr_ratio" in columns else "NULL"
    atr_grade_expr = "atr_grade" if "atr_grade" in columns else "NULL"
    atr_snapshot_expr = (
        "atr_configuration_snapshot_id"
        if "atr_configuration_snapshot_id" in columns
        else "''"
    )
    atr_status_expr = (
        "atr_evaluation_status"
        if "atr_evaluation_status" in columns
        else "'NOT_APPLICABLE'"
    )
    atr_explanation_expr = "atr_explanation" if "atr_explanation" in columns else "''"
    key_level_status_expr = (
        "key_level_status" if "key_level_status" in columns else "'NOT_APPLICABLE'"
    )
    key_level_zone_expr = "key_level_zone" if "key_level_zone" in columns else "NULL"
    key_level_match_count_expr = (
        "key_level_match_count" if "key_level_match_count" in columns else "NULL"
    )
    key_level_snapshot_expr = (
        "key_level_configuration_snapshot_id"
        if "key_level_configuration_snapshot_id" in columns
        else "''"
    )
    select = f"""
        SELECT {record_expr} AS record_id, symbol, profile, timeframe,
               {created_expr} AS created_at, closed_at, exit_reason,
               {status_expr} AS signal_status,
               {reason_expr} AS invalidation_reason,
               {outcome_expr} AS final_outcome,
               {outcome_at_expr} AS outcome_at,
               {atr_method_expr} AS atr_method,
               {atr_period_expr} AS atr_period,
               {atr_value_expr} AS atr_value,
               {parent_range_expr} AS parent_range,
               {parent_ratio_expr} AS parent_atr_ratio,
               {sweep_depth_expr} AS sweep_depth,
               {sweep_ratio_expr} AS sweep_atr_ratio,
               {atr_grade_expr} AS atr_grade,
               {atr_snapshot_expr} AS atr_configuration_snapshot_id,
               {atr_status_expr} AS atr_evaluation_status,
               {atr_explanation_expr} AS atr_explanation,
               {key_level_status_expr} AS key_level_status,
               {key_level_zone_expr} AS key_level_zone,
               {key_level_match_count_expr} AS key_level_match_count,
               {key_level_snapshot_expr} AS key_level_configuration_snapshot_id
        FROM journal_entries
    """
    return select, created_expr


def get_signal_passport_history(
    db_path: str,
    days: int = 30,
    limit: int = 10,
    profile: str | None = None,
    symbol: str | None = None,
    offset: int = 0,
    *,
    with_status: bool = False,
) -> list[dict[str, Any]] | dict[str, Any]:
    """Return recent Passport records, optionally with storage status.

    The default list contract is retained for existing callers. Telegram and
    other user-facing callers can request ``with_status`` so a storage failure
    is not rendered as a legitimate empty history.
    """
    bounded_days, start, end = _period(days)
    bounded_limit = min(max(int(limit), 1), 50)
    bounded_offset = min(max(int(offset), 0), 1000)
    if not load_storage_config().uses_postgres and not Path(db_path).exists():
        logger.warning('{"event":"signal_passport_query_unknown","reason":"journal_missing"}')
        result = {
            "status": "UNKNOWN",
            "items": [],
            "count": 0,
            "reason": "Signal Passport is unavailable.",
        }
        return result if with_status else []

    try:
        with connect_journal(db_path) as conn:
            select_sql, created_expr = _passport_query_parts(conn)
            params: list[Any] = [start, end]
            filters = [
                f"{created_expr} >= ?",
                f"{created_expr} <= ?",
            ]
            if profile:
                filters.append("profile = ?")
                params.append(str(profile).strip())
            if symbol:
                filters.append("symbol = ?")
                params.append(str(symbol).strip().upper())
            params.extend((bounded_limit, bounded_offset))
            rows = conn.execute(
                select_sql
                + " WHERE "
                + " AND ".join(filters)
                + " ORDER BY created_at DESC LIMIT ? OFFSET ?",
                params,
            ).fetchall()
        records = [_public_record(row) for row in rows]
        logger.info(
            '{"event":"signal_passport_history_viewed","days":%d,"returned":%d}',
            bounded_days,
            len(records),
        )
        if with_status:
            return {"status": "OK", "items": records, "count": len(records)}
        return records
    except Exception as exc:
        logger.error(
            '{"event":"signal_passport_query_failed","query":"history","error":"%s"}',
            str(exc)[:160],
        )
        result = {
            "status": "UNKNOWN",
            "items": [],
            "count": 0,
            "reason": "Signal Passport is unavailable.",
        }
        return result if with_status else []


def get_signal_passport_performance(db_path: str, days: int = 7) -> dict[str, Any]:
    """Return public Passport totals grouped by pair and style."""
    bounded_days, start, end = _period(days)
    empty = {
        "status": "UNKNOWN",
        "period_days": bounded_days,
        "total_signals": 0,
        "open_signals": 0,
        "resolved_signals": 0,
        "wins": 0,
        "losses": 0,
        "unknown_outcomes": 0,
        "win_rate_pct": None,
        "by_pair": {},
        "by_style": {},
        "by_timeframe": {},
    }
    if not load_storage_config().uses_postgres and not Path(db_path).exists():
        return {**empty, "reason": "Signal Passport journal is unavailable."}

    try:
        with connect_journal(db_path) as conn:
            select_sql, created_expr = _passport_query_parts(conn)
            rows = conn.execute(
                select_sql
                + f" WHERE {created_expr} >= ?"
                + f" AND {created_expr} <= ?",
                (start, end),
            ).fetchall()
        overall = _empty_bucket()
        by_pair: dict[str, dict[str, Any]] = {}
        by_style: dict[str, dict[str, Any]] = {}
        by_timeframe: dict[str, dict[str, Any]] = {}
        for row in rows:
            record = _public_record(row)
            _add_record(overall, record)
            _add_record(by_pair.setdefault(record["pair"], _empty_bucket()), record)
            _add_record(by_style.setdefault(record["style"], _empty_bucket()), record)
            _add_record(
                by_timeframe.setdefault(record["timeframe"], _empty_bucket()),
                record,
            )

        result = {
            "status": "OK",
            "period_days": bounded_days,
            **overall,
            "by_pair": dict(sorted(by_pair.items())),
            "by_style": dict(sorted(by_style.items())),
            "by_timeframe": dict(sorted(by_timeframe.items())),
        }
        logger.info(
            '{"event":"signal_passport_performance_viewed","days":%d,"total":%d}',
            bounded_days,
            overall["total_signals"],
        )
        return result
    except Exception as exc:
        logger.error(
            '{"event":"signal_passport_query_failed","query":"performance","error":"%s"}',
            str(exc)[:160],
        )
        return {**empty, "reason": "Signal Passport performance is unavailable."}


def _outcome_icon(outcome: str) -> str:
    return {"WIN": "\U0001F7E2", "LOSS": "\U0001F534", "UNKNOWN": "\u26AA"}.get(
        outcome, "\\u26AA"
    )


def render_signal_passport_history(records: Iterable[dict[str, Any]]) -> str:
    """Render public Passport records for Telegram HTML."""
    records = list(records)
    if not records:
        return (
            "\U0001F4D8 <b>Signal Passport</b>\n\n"
            "No stored signal records are available yet."
        )

    lines = ["\U0001F4D8 <b>Signal Passport</b>", "", "Recent stored signal records:", ""]
    for record in records:
        pair = html.escape(str(record.get("pair", "UNKNOWN")))
        style = html.escape(_human_label(record.get("style", "UNKNOWN")))
        timeframe = html.escape(str(record.get("timeframe", "UNKNOWN")))
        created_at = html.escape(_format_public_datetime(record.get("created_at")))
        status = html.escape(_human_label(record.get("status", "UNKNOWN")))
        outcome = str(record.get("final_outcome", "UNKNOWN")).upper()
        reason = html.escape(_public_invalidation_reason(outcome, record.get("invalidation_reason")))
        lines.extend(
            [
                f"{_outcome_icon(outcome)} <b>{pair}</b> · {style} · {timeframe}",
                f"🕒 Created: <code>{created_at}</code>",
                f"📌 Status: <b>{status}</b> · Outcome: <b>{html.escape(_human_label(outcome))}</b>",
                "",
            ]
        )
        replay = record.get("replay") or {}
        if replay:
            replay_status = str(replay.get("status") or "UNKNOWN").upper()
            replay_label = {
                "AVAILABLE": "Available",
                "PENDING": "Pending",
                "UNAVAILABLE": "Unavailable",
                "UNKNOWN": "Unknown",
            }.get(replay_status, "Unknown")
            lines.insert(-1, f"📼 Replay: <b>{html.escape(replay_label)}</b>")
        atr_quality = record.get("atr_quality") or {}
        if atr_quality.get("status") != "NOT_APPLICABLE":
            grade = html.escape(str(atr_quality.get("grade") or "UNKNOWN"))
            atr_status = html.escape(str(atr_quality.get("status") or "UNKNOWN"))
            lines.insert(-1, f"📏 ATR quality: <b>{grade}</b> · {atr_status}")
        key_level = record.get("key_level") or {}
        key_status = str(key_level.get("status") or "NOT_APPLICABLE").upper()
        if key_status != "NOT_APPLICABLE":
            if key_status == "VALID" and key_level.get("at_htf_zone") is True:
                key_label = "Major key level"
            elif key_status == "VALID":
                key_label = "Open structure"
            else:
                key_label = "Unavailable"
            lines.insert(-1, f"🏛️ Location: <b>{key_label}</b>")
        if outcome != "WIN":
            lines.insert(-1, f"⚠️ Invalidation: <i>{reason}</i>")
    lines.append("Results are stored signal outcomes, not a promise of future performance.")
    return "\n".join(lines)


def render_signal_passport_weekly(performance: dict[str, Any]) -> str:
    """Render the public Passport aggregate for Telegram HTML."""
    return render_signal_passport_report(performance, "Weekly Signal Report", "last 7 days")


def render_signal_passport_daily(performance: dict[str, Any]) -> str:
    """Render the public one-day Passport aggregate for Telegram HTML."""
    return render_signal_passport_report(performance, "Daily Signal Report", "today")


def render_signal_passport_report(
    performance: dict[str, Any],
    title: str,
    period_label: str,
) -> str:
    """Render pair and style metrics as readable blocks for Telegram."""
    if performance.get("status") != "OK":
        return (
            f"📊 <b>{html.escape(title)}</b>\n\n"
            "Status: <b>UNKNOWN</b>\n"
            "The Signal Passport data is temporarily unavailable."
        )

    def metric_block(icon: str, label: str, bucket: dict[str, Any]) -> list[str]:
        rate = bucket.get("win_rate_pct")
        rate_text = f"{rate:.1f}%" if isinstance(rate, (int, float)) else "UNKNOWN"
        return [
            f"{icon} <b>{html.escape(_human_label(label))}:</b>",
            f"{bucket.get('total_signals', 0)} signals · {bucket.get('wins', 0)} wins · {bucket.get('losses', 0)} losses · {rate_text} resolved win rate",
        ]

    lines = [
        f"📊 <b>{html.escape(title)}</b>",
        f"🗓️ Performance for <b>{html.escape(period_label)}</b>",
        "",
        f"📡 Signals recorded: <b>{performance.get('total_signals', 0)}</b>",
        f"🟡 Open: <b>{performance.get('open_signals', 0)}</b> · "
        f"✅ Resolved: <b>{performance.get('resolved_signals', 0)}</b> · "
        f"⚪ Unknown: <b>{performance.get('unknown_outcomes', 0)}</b>",
        "",
        "💱 <b>By Pair</b>",
    ]
    by_pair = performance.get("by_pair", {})
    for pair, bucket in by_pair.items():
        lines.extend(metric_block("💱", pair, bucket))
        lines.append("")
    if not by_pair:
        lines.append("No pair records in this period.")
    lines.extend(["", "🎯 <b>By Trading Style</b>"])
    by_style = performance.get("by_style", {})
    for style, bucket in by_style.items():
        lines.extend(metric_block("🎯", style, bucket))
        lines.append("")
    if not by_style:
        lines.append("No style records in this period.")
    lines.extend(["", "ℹ️ Unknown outcomes are excluded from win-rate calculations."])
    return "\n".join(lines)
