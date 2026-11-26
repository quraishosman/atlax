"""Subscriber-safe aggregate reporting for Signal Replay coverage.

This module joins immutable replay snapshots with the existing Signal Passport
aggregate. It never derives a new outcome and never exposes signal IDs,
recipient IDs, price levels, or raw candle data.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

from core.analytics.signal_passport import get_signal_passport_performance
from core.models.signal_replay import (
    ReplayAvailability,
    ReplayLifecycleStatus,
    ReplayOutcome,
    ReplaySnapshot,
)


logger = logging.getLogger("atlax.analytics.signal_replay_reporting")


def _period(days: int) -> tuple[int, datetime, datetime]:
    bounded = min(max(int(days), 1), 365)
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=bounded)
    return bounded, start, end


def _bucket() -> dict[str, Any]:
    return {
        "snapshots": 0,
        "available": 0,
        "unavailable": 0,
        "open": 0,
        "resolved": 0,
        "wins": 0,
        "losses": 0,
        "breakevens": 0,
        "unknown_outcomes": 0,
        "win_rate_pct": None,
    }


def _add(bucket: dict[str, Any], snapshot: ReplaySnapshot) -> None:
    bucket["snapshots"] += 1
    if snapshot.availability is ReplayAvailability.AVAILABLE:
        bucket["available"] += 1
    else:
        bucket["unavailable"] += 1

    if snapshot.lifecycle_status is ReplayLifecycleStatus.OPEN:
        bucket["open"] += 1
    if snapshot.final_outcome is ReplayOutcome.WIN:
        bucket["resolved"] += 1
        bucket["wins"] += 1
    elif snapshot.final_outcome is ReplayOutcome.LOSS:
        bucket["resolved"] += 1
        bucket["losses"] += 1
    elif snapshot.final_outcome is ReplayOutcome.BREAKEVEN:
        bucket["resolved"] += 1
        bucket["breakevens"] += 1
    else:
        bucket["unknown_outcomes"] += 1

    resolved = bucket["wins"] + bucket["losses"]
    bucket["win_rate_pct"] = round(bucket["wins"] / resolved * 100, 1) if resolved else None


def _dimension_values(snapshot: ReplaySnapshot, dimension: str) -> tuple[str, ...]:
    context = snapshot.rule_context or {}
    if dimension == "pair":
        return (snapshot.symbol.upper(),)
    if dimension == "timeframe":
        return (snapshot.source_timeframe.upper(),)
    if dimension == "style":
        return (str(context.get("profile") or "UNKNOWN").strip() or "UNKNOWN",)
    if dimension == "grade":
        return (str(context.get("atr_grade") or "UNKNOWN").strip().upper() or "UNKNOWN",)
    if dimension == "route":
        raw = context.get("delivery_routes")
        if isinstance(raw, (list, tuple)):
            values = tuple(
                sorted(
                    {
                        str(value).strip().lower()
                        for value in raw
                        if str(value).strip()
                    }
                )
            )
            if values:
                return values
        return ("UNKNOWN",)
    return ("UNKNOWN",)


def _sorted_buckets(values: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {key: values[key] for key in sorted(values)}


def build_signal_replay_report(
    db_path: str,
    *,
    days: int = 30,
    repository=None,
) -> dict[str, Any]:
    """Return deterministic replay coverage and outcome aggregates."""
    bounded_days, start, end = _period(days)
    try:
        passport = get_signal_passport_performance(db_path, days=bounded_days)
        if passport.get("status") != "OK":
            return {
                "status": "UNKNOWN",
                "period_days": bounded_days,
                "reason": "Signal Passport performance is unavailable.",
            }

        if repository is None:
            from core.storage.factory import get_replay_repository

            repository = get_replay_repository(db_path, initialize=False)
        snapshots = [
            snapshot
            for snapshot in repository.list()
            if isinstance(snapshot, ReplaySnapshot)
            and start <= snapshot.created_at <= end
        ]

        overall = _bucket()
        by_pair: dict[str, dict[str, Any]] = {}
        by_timeframe: dict[str, dict[str, Any]] = {}
        by_style: dict[str, dict[str, Any]] = {}
        by_route: dict[str, dict[str, Any]] = {}
        by_grade: dict[str, dict[str, Any]] = {}
        for snapshot in snapshots:
            _add(overall, snapshot)
            for dimension, target in (
                ("pair", by_pair),
                ("timeframe", by_timeframe),
                ("style", by_style),
                ("route", by_route),
                ("grade", by_grade),
            ):
                for value in _dimension_values(snapshot, dimension):
                    _add(target.setdefault(value, _bucket()), snapshot)

        passport_total = int(passport.get("total_signals", 0))
        coverage_pct = (
            round(overall["available"] / passport_total * 100, 1)
            if passport_total > 0
            else None
        )
        result = {
            "status": "OK",
            "period_days": bounded_days,
            "passport_total_signals": passport_total,
            "replay_snapshot_count": overall["snapshots"],
            "replay_available_count": overall["available"],
            "replay_unavailable_count": overall["unavailable"],
            "replay_coverage_pct": coverage_pct,
            "outcomes": {
                "open": overall["open"],
                "resolved": overall["resolved"],
                "wins": overall["wins"],
                "losses": overall["losses"],
                "breakevens": overall["breakevens"],
                "unknown": overall["unknown_outcomes"],
                "win_rate_pct": overall["win_rate_pct"],
            },
            "by_pair": _sorted_buckets(by_pair),
            "by_timeframe": _sorted_buckets(by_timeframe),
            "by_style": _sorted_buckets(by_style),
            "by_route": _sorted_buckets(by_route),
            "by_grade": _sorted_buckets(by_grade),
        }
        logger.info(
            '{"event":"signal_replay_report_generated","days":%d,"passport_total":%d,"snapshots":%d,"available":%d}',
            bounded_days,
            passport_total,
            overall["snapshots"],
            overall["available"],
        )
        return result
    except Exception as exc:  # noqa: BLE001 - analytics must expose UNKNOWN
        logger.error(
            '{"event":"signal_replay_report_failed","days":%d,"reason":"%s"}',
            bounded_days,
            str(exc)[:160],
        )
        return {
            "status": "UNKNOWN",
            "period_days": bounded_days,
            "reason": "Signal Replay coverage is unavailable.",
        }


def public_signal_replay_report(db_path: str, days: int = 30) -> dict[str, Any]:
    """Return the aggregate subset allowed into ATLAX AI context."""
    report = build_signal_replay_report(db_path, days=days)
    if report.get("status") != "OK":
        return {
            "status": "UNKNOWN",
            "period_days": report.get("period_days", min(max(int(days), 1), 365)),
            "reason": report.get("reason", "Signal Replay coverage is unavailable."),
        }
    return {
        "status": "OK",
        "period_days": report["period_days"],
        "passport_total_signals": report["passport_total_signals"],
        "replay_snapshot_count": report["replay_snapshot_count"],
        "replay_available_count": report["replay_available_count"],
        "replay_coverage_pct": report["replay_coverage_pct"],
        "outcomes": report["outcomes"],
        "by_pair": report["by_pair"],
        "by_timeframe": report["by_timeframe"],
        "by_style": report["by_style"],
        "by_route": report["by_route"],
        "by_grade": report["by_grade"],
    }
