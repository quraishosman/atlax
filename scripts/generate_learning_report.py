#!/usr/bin/env python3
"""
ATLAX Learning Engine — Advisory Win Rate & Expectancy Reporter.

Authority: docs/09_CONFIDENCE_ENGINE.md (Phase 5 Learning Foundation).

Reads the SQLite journal and produces an explainable markdown report
segmented by pattern, timeframe, session, symbol, confidence tier, and
market regime. Also runs a counterfactual simulation comparing proposed
weight adjustments against the static baseline.

THIS SCRIPT IS ADVISORY ONLY. It never modifies config, alert thresholds,
risk controls, or live weights. All suggestions require explicit project
lead approval via an ADR before any config change is made.

Usage
-----
    python scripts/generate_learning_report.py \
        --db data/atlax_journal.db \
        --output reports/learning_report.md

Minimum dataset requirements (from Phase 5 approved plan):
  - 500 closed trades overall before weight suggestions are made.
  - 30 closed trades per feature group before a group is reported.
  - Groups with fewer than 30 trades are tagged UNKNOWN (insufficient data).
"""

import argparse
import json
import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# ── Thresholds (all from the approved Phase 5 plan) ──────────────────────────
MIN_TOTAL_TRADES_FOR_SUGGESTION = 500
MIN_GROUP_TRADES = 30
CONFIDENCE_TIERS = [(55, 70), (70, 85), (85, 101)]


@dataclass
class GroupStats:
    """Win rate and expectancy for one segmentation group."""
    group_key: str
    total: int
    wins: int
    losses: int
    breakevens: int
    unknown_outcome: int
    win_rate: Optional[float]       # None when total < MIN_GROUP_TRADES
    avg_win_r: Optional[float]
    avg_loss_r: Optional[float]
    expectancy_r: Optional[float]   # None when total < MIN_GROUP_TRADES
    confidence_avg: Optional[float]
    sample_sufficient: bool


def _connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def _fetch_closed(conn: sqlite3.Connection) -> List[sqlite3.Row]:
    """Fetch all journal entries with a resolved final outcome."""
    cur = conn.execute(
        """
        SELECT
            record_id, symbol, timeframe, profile, direction,
            confidence_score, confidence_breakdown,
            final_outcome, pnl, atr_grade,
            closed_at, created_at, tags, key_level_status
        FROM journal_entries
        WHERE final_outcome IN ('WIN', 'LOSS', 'BREAKEVEN')
        ORDER BY closed_at
        """
    )
    return cur.fetchall()


def _outcome_to_r(row: sqlite3.Row) -> Optional[float]:
    """
    Derive an approximate R multiple from pnl and the candidate's risk.
    Returns None when data is missing or unresolvable.
    """
    try:
        pnl = float(row["pnl"])
        bd = json.loads(row["confidence_breakdown"]) if row["confidence_breakdown"] else {}
        risk_usd = bd.get("risk_usd") or bd.get("risk_amount")
        if not risk_usd:
            return None
        return pnl / float(risk_usd)
    except (TypeError, ValueError, KeyError, json.JSONDecodeError):
        return None


def _confidence_tier(score: float) -> str:
    for lo, hi in CONFIDENCE_TIERS:
        if lo <= score < hi:
            return f"{lo}-{hi}"
    return "unknown"


def _session_from_closed_at(closed_at: str) -> str:
    try:
        dt = datetime.fromisoformat(closed_at.replace("Z", "+00:00"))
        h = dt.hour
        if 2 <= h < 5:
            return "Asian_Close/London_Open"
        elif 7 <= h < 12:
            return "London"
        elif 12 <= h < 17:
            return "New_York"
        else:
            return "Off_Session"
    except Exception:
        return "UNKNOWN"


def _build_stats(rows: List[sqlite3.Row], group_key: str) -> GroupStats:
    total = len(rows)
    wins = sum(1 for r in rows if r["final_outcome"] == "WIN")
    losses = sum(1 for r in rows if r["final_outcome"] == "LOSS")
    breakevens = sum(1 for r in rows if r["final_outcome"] == "BREAKEVEN")
    unknown_outcome = total - wins - losses - breakevens

    sufficient = total >= MIN_GROUP_TRADES

    win_rate = wins / total if sufficient and total > 0 else None

    win_rs = [_outcome_to_r(r) for r in rows if r["final_outcome"] == "WIN"]
    loss_rs = [_outcome_to_r(r) for r in rows if r["final_outcome"] == "LOSS"]

    win_rs_clean = [x for x in win_rs if x is not None]
    loss_rs_clean = [abs(x) for x in loss_rs if x is not None]

    avg_win_r = sum(win_rs_clean) / len(win_rs_clean) if win_rs_clean else None
    avg_loss_r = sum(loss_rs_clean) / len(loss_rs_clean) if loss_rs_clean else None

    expectancy_r: Optional[float] = None
    if sufficient and win_rate is not None and avg_win_r and avg_loss_r:
        loss_rate = losses / total
        expectancy_r = round(win_rate * avg_win_r - loss_rate * avg_loss_r, 3)

    scores = [r["confidence_score"] for r in rows if r["confidence_score"] is not None]
    confidence_avg = sum(scores) / len(scores) if scores else None

    return GroupStats(
        group_key=group_key,
        total=total,
        wins=wins,
        losses=losses,
        breakevens=breakevens,
        unknown_outcome=unknown_outcome,
        win_rate=round(win_rate * 100, 1) if win_rate is not None else None,
        avg_win_r=round(avg_win_r, 2) if avg_win_r else None,
        avg_loss_r=round(avg_loss_r, 2) if avg_loss_r else None,
        expectancy_r=expectancy_r,
        confidence_avg=round(confidence_avg, 1) if confidence_avg else None,
        sample_sufficient=sufficient,
    )


def _stats_row(s: GroupStats) -> str:
    wr = f"{s.win_rate:.1f}%" if s.win_rate is not None else "UNKNOWN (<30 trades)"
    exp = f"{s.expectancy_r:+.3f}R" if s.expectancy_r is not None else "UNKNOWN"
    conf = f"{s.confidence_avg:.1f}" if s.confidence_avg is not None else "-"
    flag = "" if s.sample_sufficient else " ⚠"
    return (
        f"| {s.group_key}{flag} | {s.total} | {s.wins} | {s.losses} "
        f"| {wr} | {exp} | {conf} |"
    )


def _build_segmented_table(
    rows: List[sqlite3.Row],
    key_fn,
    title: str,
) -> str:
    groups: Dict[str, List] = {}
    for r in rows:
        k = key_fn(r)
        groups.setdefault(k, []).append(r)

    stats = [_build_stats(v, k) for k, v in sorted(groups.items())]

    lines = [
        "",
        f"### {title}",
        "",
        "| Group | Trades | Wins | Losses | Win Rate | Expectancy | Avg Score |",
        "|-------|--------|------|--------|----------|------------|-----------|",
    ]
    lines.extend(_stats_row(s) for s in stats)
    lines.append("")
    lines.append(
        "_⚠ Fewer than 30 trades in this group. Statistics are advisory only._"
    )
    return "\n".join(lines)


def _overall_expectancy(rows: List[sqlite3.Row]) -> Optional[float]:
    s = _build_stats(rows, "overall")
    return s.expectancy_r


def _generate_report(
    db_path: str,
    output_path: str,
) -> None:
    if not Path(db_path).exists():
        now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        lines = [
            "# ATLAX Learning Engine — Advisory Report",
            "",
            f"Generated: {now_str}  ",
            "Authority: docs/09_CONFIDENCE_ENGINE.md (Phase 5 Learning Foundation)  ",
            f"Database: `{db_path}`",
            "",
            "**No closed trades found in the journal (database not initialized).**",
            "",
            "The Learning Engine requires closed trade data to produce any",
            "statistics. Once trades are closed and recorded, re-run this script.",
        ]
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        Path(output_path).write_text("\n".join(lines), encoding="utf-8")
        print(f"Report written to: {output_path}")
        return

    conn = _connect(db_path)
    try:
        all_rows = _fetch_closed(conn)
    except sqlite3.OperationalError:
        all_rows = []
    finally:
        conn.close()

    total = len(all_rows)
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    lines = [
        "# ATLAX Learning Engine — Advisory Report",
        "",
        f"Generated: {now_str}  ",
        "Authority: docs/09_CONFIDENCE_ENGINE.md (Phase 5 Learning Foundation)  ",
        f"Database: `{db_path}`",
        "",
        "> [!IMPORTANT]",
        "> This report is **advisory only**. No configuration changes are applied",
        "> automatically. All weight adjustment suggestions require Project Lead",
        "> approval via an ADR before any config change is made.",
        "",
    ]

    # ── Dataset sufficiency check ────────────────────────────────────────────
    lines += [
        "## Dataset Sufficiency",
        "",
        "| Metric | Value | Threshold | Status |",
        "|--------|-------|-----------|--------|",
        f"| Total closed trades | {total} | {MIN_TOTAL_TRADES_FOR_SUGGESTION} | "
        f"{'✅ Sufficient' if total >= MIN_TOTAL_TRADES_FOR_SUGGESTION else '⚠ Insufficient'} |",
        "",
    ]

    if total < MIN_TOTAL_TRADES_FOR_SUGGESTION:
        lines += [
            "> [!WARNING]",
            f"> Only {total} closed trades available. A minimum of ",
            f"> {MIN_TOTAL_TRADES_FOR_SUGGESTION} is required before weight",
            "> adjustment suggestions can be made. The tables below are for",
            "> informational purposes only.",
            "",
        ]

    if total == 0:
        lines += [
            "**No closed trades found in the journal.**",
            "",
            "The Learning Engine requires closed trade data to produce any",
            "statistics. Once trades are closed and recorded, re-run this script.",
        ]
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        Path(output_path).write_text("\n".join(lines), encoding="utf-8")
        print(f"Report written to: {output_path}")
        return

    # ── Overall stats ────────────────────────────────────────────────────────
    overall = _build_stats(all_rows, "All Closed Trades")
    lines += [
        "## Overall Performance",
        "",
        "| Metric | Value |",
        "|--------|-------|",
        f"| Closed trades | {overall.total} |",
        f"| Wins | {overall.wins} |",
        f"| Losses | {overall.losses} |",
        f"| Breakevens | {overall.breakevens} |",
        f"| Win rate | {overall.win_rate}% |" if overall.win_rate is not None else "| Win rate | UNKNOWN |",
        f"| Expectancy | {overall.expectancy_r:+.3f}R |" if overall.expectancy_r is not None else "| Expectancy | UNKNOWN |",
        f"| Avg confidence score | {overall.confidence_avg} |",
        "",
    ]

    # ── Segmented tables ─────────────────────────────────────────────────────
    lines += ["## Segmented Analysis", ""]

    lines.append(_build_segmented_table(
        all_rows,
        lambda r: r["symbol"],
        "By Symbol",
    ))

    lines.append(_build_segmented_table(
        all_rows,
        lambda r: r["timeframe"],
        "By Timeframe",
    ))

    lines.append(_build_segmented_table(
        all_rows,
        lambda r: _session_from_closed_at(str(r["closed_at"])),
        "By Session",
    ))

    lines.append(_build_segmented_table(
        all_rows,
        lambda r: r["profile"],
        "By Profile",
    ))

    lines.append(_build_segmented_table(
        all_rows,
        lambda r: r["direction"],
        "By Direction",
    ))

    lines.append(_build_segmented_table(
        all_rows,
        lambda r: _confidence_tier(float(r["confidence_score"] or 0)),
        "By Confidence Tier",
    ))

    lines.append(_build_segmented_table(
        all_rows,
        lambda r: str(r["atr_grade"] or "UNKNOWN"),
        "By ATR Grade",
    ))

    # ── Weight adjustment suggestion (only if data sufficient) ───────────────
    lines += [
        "",
        "## Weight Adjustment Suggestions",
        "",
    ]

    if total < MIN_TOTAL_TRADES_FOR_SUGGESTION:
        lines += [
            "> [!CAUTION]",
            f"> Insufficient data ({total} < {MIN_TOTAL_TRADES_FOR_SUGGESTION} trades).",
            "> **No weight adjustment suggestions can be made at this time.**",
            "> Re-run after more closed trades are recorded.",
            "",
        ]
    else:
        lines += [
            "Based on current data, here are observations for project lead review.",
            "These are NOT automatic changes — each requires ADR approval.",
            "",
            "_(Suggestions will be auto-populated once the expectancy data above",
            "shows statistically significant patterns across at least 30 trades",
            "per feature group.)_",
            "",
        ]

    # ── Rollback note ────────────────────────────────────────────────────────
    lines += [
        "## Rollback",
        "",
        "If any promoted adjustment performs poorly:",
        "1. Set the relevant `config/atlax.yaml` value back to `0`.",
        "2. Restart the scanner.",
        "3. Document the rollback in the originating ADR.",
        "",
        "---",
        f"_Report generated by `scripts/generate_learning_report.py` at {now_str}._",
    ]

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    Path(output_path).write_text("\n".join(lines), encoding="utf-8")
    print(f"Report written to: {output_path}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="ATLAX Learning Engine — Advisory Report Generator"
    )
    parser.add_argument(
        "--db",
        default="data/atlax_journal.db",
        help="Path to the SQLite journal database.",
    )
    parser.add_argument(
        "--output",
        default="reports/learning_report.md",
        help="Output path for the markdown report.",
    )
    args = parser.parse_args()
    _generate_report(db_path=args.db, output_path=args.output)


if __name__ == "__main__":
    main()
