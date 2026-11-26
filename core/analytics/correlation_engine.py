"""
ATLAX Correlation Engine.

Computes real statistical (Pearson) correlation coefficients between forex pairs
using the last N days of daily close prices sourced from MT5.

This is the same correlation metric shown on sites like myfxbook, mataf.net
and investing.com — calculated from actual price returns, not from CRT bias.

A coefficient of:
  +0.70 to +1.00  →  Strong positive (pairs tend to move together)
  +0.30 to +0.70  →  Moderate positive
  -0.30 to +0.30  →  No reliable correlation
  -0.70 to -0.30  →  Moderate negative
  -1.00 to -0.70  →  Strong negative (pairs tend to move inversely)

Architecture:
  - Called by MarketScanner once per scan cycle (update_from_candle).
  - Maintains a rolling deque of daily closes per symbol (in-memory).
  - After collecting MIN_PERIODS closes for ≥2 symbols, writes correlation.json.
  - Telegram handler reads correlation.json to display the table.
  - MCP server exposes correlation.json to the AI advisory engine.
"""

import json
import logging
import math
import os
from collections import deque
from typing import Sequence

logger = logging.getLogger("atlax.correlation")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MIN_PERIODS    = 10   # minimum closes needed before computing (need enough history)
MAX_PERIODS    = 100  # rolling window length (in candle bars)

# Display thresholds
_STRONG_POS  =  0.70
_MOD_POS     =  0.30
_MOD_NEG     = -0.30
_STRONG_NEG  = -0.70

_OUTPUT_FILE = "data/correlation.json"


def _pearson(x: list[float], y: list[float]) -> float | None:
    """Compute Pearson correlation coefficient between two equal-length lists."""
    n = min(len(x), len(y))
    if n < MIN_PERIODS:
        return None
    x, y = x[-n:], y[-n:]

    # Use returns (day-over-day % change) instead of raw prices
    rx = [(x[i] - x[i - 1]) / x[i - 1] for i in range(1, n) if x[i - 1] != 0]
    ry = [(y[i] - y[i - 1]) / y[i - 1] for i in range(1, n) if y[i - 1] != 0]
    n = min(len(rx), len(ry))
    if n < MIN_PERIODS - 1:
        return None
    rx, ry = rx[-n:], ry[-n:]

    mean_x = sum(rx) / n
    mean_y = sum(ry) / n
    cov    = sum((rx[i] - mean_x) * (ry[i] - mean_y) for i in range(n))
    std_x  = math.sqrt(sum((v - mean_x) ** 2 for v in rx))
    std_y  = math.sqrt(sum((v - mean_y) ** 2 for v in ry))
    if std_x == 0 or std_y == 0:
        return None
    return round(cov / (std_x * std_y), 3)


def _label(coef: float) -> str:
    if coef >= _STRONG_POS:
        return "Strong Positive"
    if coef >= _MOD_POS:
        return "Moderate Positive"
    if coef > _MOD_NEG:
        return "No Correlation"
    if coef > _STRONG_NEG:
        return "Moderate Negative"
    return "Strong Negative"


def _emoji(coef: float, pair_a: str, pair_b: str) -> str:
    """
    Return directional emojis that convey the relationship:
    both up, both down, one up one down.
    For positive correlation we show matching arrows.
    For negative correlation we show opposing arrows.
    """
    if coef >= _MOD_POS:
        return "📈📈"    # both tend to move same way
    if coef <= _MOD_NEG:
        return "📈📉"    # one up, other down
    return "↔️"           # no reliable direction


# ---------------------------------------------------------------------------
# CorrelationEngine
# ---------------------------------------------------------------------------

class CorrelationEngine:
    """
    Maintains rolling daily close prices per symbol and computes
    pairwise Pearson correlations on every update.

    Usage (scanner calls this per instrument per cycle):
        engine.update("GBPUSD", "D1", close_price)
    """

    def __init__(self, output_file: str = _OUTPUT_FILE, state_store=None) -> None:
        self.output_file = output_file
        self._state_store = state_store
        saved_closes = state_store.get("correlation.closes", {}) if state_store is not None else {}
        # symbol → deque of recent close prices (D1 only, other TFs ignored)
        self._closes: dict[str, deque] = {
            str(symbol): deque(values, maxlen=MAX_PERIODS)
            for symbol, values in (saved_closes.items() if isinstance(saved_closes, dict) else [])
            if isinstance(values, list)
        }
        self.latest_state = state_store.get("correlation.state", {}) if state_store is not None else {}

    def update(self, symbol: str, timeframe: str, close_price: float) -> None:
        """
        Feed a new daily close price for a symbol.
        Only D1 closes are used for the correlation calculation.
        """
        if timeframe != "D1":
            return  # Use only daily closes for statistical reliability
        if not symbol or close_price <= 0:
            return

        if symbol not in self._closes:
            self._closes[symbol] = deque(maxlen=MAX_PERIODS)
        self._closes[symbol].append(close_price)

        # Recompute and persist after every update
        self._compute_and_save()

    def _compute_and_save(self) -> None:
        """Compute all pairwise correlations and write to JSON."""
        symbols = [s for s, d in self._closes.items() if len(d) >= MIN_PERIODS]
        if len(symbols) < 2:
            return  # not enough data yet

        matrix: dict[str, dict[str, dict]] = {}
        pairs_summary: list[dict] = []

        for i, a in enumerate(symbols):
            for b in symbols[i + 1:]:
                xa = list(self._closes[a])
                xb = list(self._closes[b])
                coef = _pearson(xa, xb)
                if coef is None:
                    continue

                entry = {
                    "pair_a":      a,
                    "pair_b":      b,
                    "coefficient": coef,
                    "label":       _label(coef),
                    "emoji":       _emoji(coef, a, b),
                    "periods":     min(len(xa), len(xb)),
                }
                pairs_summary.append(entry)

                # Fill matrix both ways
                matrix.setdefault(a, {})[b] = {"coefficient": coef, "label": _label(coef)}
                matrix.setdefault(b, {})[a] = {"coefficient": coef, "label": _label(coef)}

        # Sort by absolute strength descending
        pairs_summary.sort(key=lambda x: abs(x["coefficient"]), reverse=True)

        state = {
            "pairs":  pairs_summary,
            "matrix": matrix,
            "note":   (
                f"Pearson correlation from last {MAX_PERIODS} D1 closes. "
                "Correlation does not guarantee future co-movement."
            ),
        }
        self._save(state)

    def _save(self, state: dict) -> None:
        self.latest_state = state
        if self._state_store is not None:
            self._state_store.set("correlation.state", state)
            self._state_store.set(
                "correlation.closes",
                {symbol: list(values) for symbol, values in self._closes.items()},
            )
            return
        os.makedirs(os.path.dirname(self.output_file) or ".", exist_ok=True)
        try:
            with open(self.output_file, "w", encoding="utf-8") as f:
                json.dump(state, f)
        except Exception as exc:
            logger.error('{"event":"correlation_save_error","error":"%s"}', str(exc)[:160])
