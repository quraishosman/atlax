"""
Google Sheets trade logging.

This module provides a lightweight helper to send closed trade data to a
Google Apps Script Web App URL. It is intentionally tolerant of missing
requests dependencies and missing configuration.
"""

import logging
import os
from decimal import Decimal
from typing import Any, Mapping

from core.analytics.google_sheets_reporting import report_event

logger = logging.getLogger("atlax.analytics.google_sheets")

_GOOGLE_SHEETS_WEBHOOK_ENV = "ATLAX_GOOGLE_SHEETS_WEBHOOK_URL"


def _estimate_pips(symbol: str, entry_price: float | str, exit_price: float | str) -> float:
    try:
        entry = Decimal(str(entry_price))
        exit_ = Decimal(str(exit_price))
    except Exception:
        return 0.0

    diff = abs(exit_ - entry)
    symbol = str(symbol or "").upper()
    if "JPY" in symbol or symbol.startswith("XAU") or symbol.startswith("XAG"):
        multiplier = Decimal("100")
    else:
        multiplier = Decimal("10000")

    pips = diff * multiplier
    return float(pips.quantize(Decimal("0.1")))


def log_trade_to_sheets(
    symbol: str,
    timeframe: str,
    entry_price: float | str,
    exit_price: float | str,
    sl: float | str,
    tp: float | str,
    pips: float | None,
    result: str,
    notes: str = "",
    webhook_url: str | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> bool:
    """Send a closed trade payload to a Google Sheets Web App URL."""
    url = (webhook_url or os.environ.get(_GOOGLE_SHEETS_WEBHOOK_ENV, "")).strip()
    if not url:
        logger.debug("Google Sheets webhook URL not configured; skipping trade log.")
        return False

    if pips is None:
        pips = _estimate_pips(symbol, entry_price, exit_price)

    extra = dict(metadata or {})
    resolved_at = str(extra.get("resolved_at") or "").strip()
    correlation_id = str(extra.get("signal_id") or "").strip()
    outcome = str(extra.get("final_outcome") or result or "UNKNOWN").upper()
    trade_data = {
        "event_name": "signal_outcome",
        "created_at": extra.get("created_at", ""),
        "resolved_at": resolved_at,
        "symbol": symbol,
        "timeframe": timeframe,
        "profile": extra.get("profile", ""),
        "direction": extra.get("direction", ""),
        "confidence_score": extra.get("confidence_score", ""),
        "pnl_r": extra.get("pnl_r", ""),
        "atr_status": extra.get("atr_status", ""),
        "atr_grade": extra.get("atr_grade", ""),
        "parent_atr_ratio": extra.get("parent_atr_ratio", ""),
        "sweep_atr_ratio": extra.get("sweep_atr_ratio", ""),
        "key_level_status": extra.get("key_level_status", ""),
        "key_level_zone": extra.get("key_level_zone", ""),
        "key_level_match_count": extra.get("key_level_match_count", ""),
        "nested_route": extra.get("nested_route", ""),
        "nested_contained": extra.get("nested_contained", ""),
        "controller_event_id": extra.get("controller_event_id", ""),
        "strategy_name": extra.get("strategy_name", ""),
        "htf_controller_timeframe": extra.get("htf_controller_timeframe", ""),
        "htf_bias": extra.get("htf_bias", ""),
        "htf_relationship": extra.get("htf_relationship", ""),
        "htf_run_length": extra.get("htf_run_length", ""),
        "htf_strong_run": extra.get("htf_strong_run", ""),
        "entry": entry_price,
        "exit": exit_price,
        "sl": sl,
        "tp": tp,
        "pips": pips,
        "result": result,
        "signal_status": extra.get("signal_status", "RESOLVED"),
        "outcome": outcome,
        "resolution_reason": extra.get("exit_reason", ""),
        "delivery_routes": extra.get("delivery_routes", ""),
        "notes": notes,
    }

    if not report_event(
        "signal_resolved",
        trade_data,
        webhook_url=url,
        event_id=f"signal_resolved:{correlation_id}" if correlation_id else "",
        correlation_id=correlation_id,
    ):
        return False

    # Keep the file/console logger ASCII-safe on Windows while preserving the
    # full result value for the Google Sheets payload above.
    log_result = str(result).encode("ascii", "ignore").decode("ascii").strip()
    logger.info(
        '{"event":"google_sheets_trade_logged","symbol":"%s","timeframe":"%s","result":"%s"}',
        symbol,
        timeframe,
        log_result,
    )
    return True
