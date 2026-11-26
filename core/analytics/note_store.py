"""
ATLAX Journal Note Store.

Allows traders to attach human-readable notes to recent closed trades
via the /note Telegram command. Notes are analysed by the AI advisory
engine to identify psychological patterns.

Authority: docs/12_ANALYTICS_ENGINE.md (trade journal analysis)
           docs/16_SECURITY.md (notes are sanitized before AI sees them)

Note: This module writes ONLY to the trader_notes column.
It never modifies trade data (prices, PnL, lot sizes, entry/exit).
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from core.settings.storage_config import load_storage_config
from core.storage.analytics_snapshot import connect_journal

logger = logging.getLogger("atlax.analytics.note_store")


def save_note(db_path: str, symbol: str, note_text: str) -> bool:
    """
    Attach a trader note to the most recent closed trade for the given symbol.

    Finds the most recently closed journal entry for `symbol` (today or
    historical), appends the note to its `trader_notes` field, and saves.

    Args:
        db_path:   Path to the journal SQLite database.
        symbol:    The instrument symbol (e.g., "GBPUSD").
        note_text: The raw note text from the trader.

    Returns:
        True if a matching trade was found and the note was saved.
        False if no trade was found or an error occurred.

    Authority: docs/12_ANALYTICS_ENGINE.md
    """
    if not load_storage_config().uses_postgres and not Path(db_path).exists():
        logger.warning(
            '{"event":"note_save_failed","reason":"db_not_found","symbol":"%s"}',
            symbol,
        )
        return False

    # Sanitize: strip dangerous characters, cap length
    clean_note = note_text.strip()[:500].replace("\x00", "")
    timestamp  = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    stamped_note = f"[{timestamp}] {clean_note}"

    if load_storage_config().uses_postgres:
        try:
            from core.storage.factory import get_journal
            saved = get_journal(db_path).append_note_for_symbol(symbol, stamped_note)
            logger.info(
                '{"event":"note_saved","symbol":"%s","backend":"postgres"}',
                symbol,
            )
            return bool(saved)
        except Exception as exc:
            logger.error(
                '{"event":"note_save_error","symbol":"%s","error":"%s"}',
                symbol,
                str(exc)[:120],
            )
            return False

    try:
        with sqlite3.connect(db_path) as conn:
            # Find the most recent trade for this symbol
            row = conn.execute(
                """
                SELECT record_id, trader_notes
                FROM journal_entries
                WHERE UPPER(symbol) = UPPER(?)
                ORDER BY closed_at DESC
                LIMIT 1
                """,
                (symbol.upper(),),
            ).fetchone()

            if not row:
                logger.info(
                    '{"event":"note_no_trade_found","symbol":"%s"}', symbol
                )
                return False

            record_id    = row[0]
            existing     = row[1] or ""
            updated_note = (existing + "\n" + stamped_note).strip()

            conn.execute(
                "UPDATE journal_entries SET trader_notes = ? WHERE record_id = ?",
                (updated_note, record_id),
            )
            conn.commit()

            logger.info(
                '{"event":"note_saved","symbol":"%s","record_id":"%s"}',
                symbol, record_id,
            )
            return True

    except Exception as exc:
        logger.error(
            '{"event":"note_save_error","symbol":"%s","error":"%s"}',
            symbol, str(exc)[:120],
        )
        return False


def get_recent_notes(db_path: str, days: int = 7) -> list[dict]:
    """
    Return trades that have trader notes attached, from the last `days` days.
    Used by the MCP gateway to pass notes to the AI advisory engine.

    Returns sanitized records — no lot sizes, account IDs, or credentials.
    """
    if not load_storage_config().uses_postgres and not Path(db_path).exists():
        return []

    from datetime import timedelta
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

    try:
        with connect_journal(db_path) as conn:
            rows = conn.execute(
                """
                SELECT symbol, timeframe, profile, direction,
                       pnl, exit_reason, closed_at, trader_notes
                FROM journal_entries
                WHERE trader_notes IS NOT NULL
                  AND TRIM(trader_notes) != ''
                  AND closed_at >= ?
                ORDER BY closed_at DESC
                LIMIT 50
                """,
                (cutoff,),
            ).fetchall()

        return [
            {
                "symbol":       r["symbol"],
                "timeframe":    r["timeframe"],
                "profile":      r["profile"],
                "direction":    r["direction"],
                "pnl":          r["pnl"],
                "exit_reason":  r["exit_reason"],
                "closed_at":    r["closed_at"],
                "trader_notes": r["trader_notes"],
            }
            for r in rows
        ]

    except Exception as exc:
        logger.error(
            '{"event":"note_fetch_error","error":"%s"}', str(exc)[:120]
        )
        return []
