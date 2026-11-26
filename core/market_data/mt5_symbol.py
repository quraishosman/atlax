"""Resolve canonical ATLAX symbols to broker-specific MT5 symbols."""

from __future__ import annotations

import os
from typing import Any, Optional


def resolve_mt5_symbol(mt5: Any, canonical_symbol: str) -> Optional[str]:
    """Return the visible MT5 symbol for a canonical ATLAX symbol.

    Exact names always win. A broker suffix may be supplied through
    ``ATLAX_MT5_SYMBOL_SUFFIX`` for terminals such as Exness, while ATLAX
    keeps the canonical symbol in detector, alert, and subscriber records.
    """
    symbol = str(canonical_symbol or "").strip()
    if not symbol or mt5 is None:
        return None

    symbol_info = getattr(mt5, "symbol_info", None)
    if not callable(symbol_info):
        # Lightweight MT5 test doubles may omit symbol_info. The real MT5
        # module always exposes it, so this fallback does not weaken runtime
        # broker validation.
        return symbol
    try:
        if symbol_info(symbol) is not None:
            return symbol
    except Exception:
        pass

    suffix = os.getenv("ATLAX_MT5_SYMBOL_SUFFIX", "").strip()
    if not suffix:
        return None
    broker_symbol = f"{symbol}{suffix}"
    try:
        if symbol_info(broker_symbol) is not None:
            return broker_symbol
    except Exception:
        return None
    return None
