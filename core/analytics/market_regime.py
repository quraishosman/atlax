"""
Market Regime Evaluator.

Authority: docs/09_CONFIDENCE_ENGINE.md (Phase 5 Learning Foundation, Q8).

Deterministic D1-candle classifier. Classifies the current daily market
context into one of five regimes for use as a segmentation dimension in the
Learning Engine analytics. ADVISORY only — regime classification never
suppresses detection, changes strategy eligibility, overrides risk controls,
or executes trades.

Regime definitions (approved Phase 5 plan, 2026-09-18):
  TRENDING_BULLISH  - Close above D1 EMA20 and making higher highs.
  TRENDING_BEARISH  - Close below D1 EMA20 and making lower lows.
  RANGING           - No consistent HH/LL structure vs EMA20.
  HIGH_VOLATILITY   - Current D1 ATR > 1.5x the 30-day average D1 ATR.
  UNKNOWN           - Insufficient closed D1 candles (< 20 required for EMA20).

Only closed D1 candles (is_closed=True) whose close_time does not exceed
event_timestamp are eligible. No look-ahead bias is introduced.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Optional, Sequence

logger = logging.getLogger("atlax.analytics.market_regime")

# ── Thresholds (all numeric values are from the approved Phase 5 plan) ───────
_MIN_EMA_CANDLES = 20          # EMA20 requires at least 20 closed D1 candles
_ATR_HISTORY_CANDLES = 30      # 30-day ATR baseline for volatility check
_HIGH_VOL_MULTIPLIER = 1.5     # ATR > 1.5x avg = HIGH_VOLATILITY


class MarketRegime(str, Enum):
    """Five possible D1 market regimes."""
    TRENDING_BULLISH = "TRENDING_BULLISH"
    TRENDING_BEARISH = "TRENDING_BEARISH"
    RANGING          = "RANGING"
    HIGH_VOLATILITY  = "HIGH_VOLATILITY"
    UNKNOWN          = "UNKNOWN"


@dataclass(frozen=True)
class MarketRegimeResult:
    """
    Output from evaluate_market_regime().

    All numeric fields are None when data is insufficient (regime=UNKNOWN).
    """
    symbol: str
    regime: MarketRegime
    ema20: Optional[float]          # D1 EMA20 at event time
    current_atr: Optional[float]    # Latest closed D1 true range
    avg_atr_30d: Optional[float]    # 30-day average true range
    higher_highs: Optional[bool]    # Last 4 candles all made HH
    lower_lows: Optional[bool]      # Last 4 candles all made LL
    eligible_candles: int           # Number of closed D1 candles evaluated
    reason: str                     # Human-readable explanation
    evaluated_at: datetime          # Evaluation wall-clock time (UTC)

    @property
    def is_unknown(self) -> bool:
        return self.regime == MarketRegime.UNKNOWN


# ── Helpers ──────────────────────────────────────────────────────────────────

def _ema(values: Sequence[float], period: int) -> float:
    """Compute EMA over values (oldest-first), returns final value."""
    if not values:
        raise ValueError("Cannot compute EMA on empty sequence.")
    k = 2.0 / (period + 1)
    result = values[0]
    for v in values[1:]:
        result = v * k + result * (1.0 - k)
    return result


def _true_range(high: float, low: float, prev_close: float) -> float:
    return max(high - low, abs(high - prev_close), abs(low - prev_close))


# ── Public API ───────────────────────────────────────────────────────────────

def evaluate_market_regime(
    d1_candles: Sequence,
    symbol: str,
    event_timestamp: Optional[datetime] = None,
) -> MarketRegimeResult:
    """
    Evaluate D1 market regime at a given point in time.

    Parameters
    ----------
    d1_candles:
        Daily candles in oldest-first order. Only closed candles whose
        close_time does not exceed event_timestamp are evaluated.
    symbol:
        Instrument symbol (for logging).
    event_timestamp:
        CRT event timestamp (UTC). Uses current UTC time if None.

    Returns
    -------
    MarketRegimeResult
        regime=UNKNOWN when fewer than 20 closed D1 candles are available.
    """
    now: datetime = event_timestamp or datetime.utcnow()

    # 1. Filter to eligible closed candles
    eligible = [
        c for c in d1_candles
        if getattr(c, "is_closed", False)
        and getattr(c, "close_time", now) <= now
    ]
    n = len(eligible)

    # Common kwargs for the UNKNOWN path
    base = dict(
        symbol=symbol,
        ema20=None,
        current_atr=None,
        avg_atr_30d=None,
        higher_highs=None,
        lower_lows=None,
        eligible_candles=n,
        evaluated_at=now,
    )

    if n < _MIN_EMA_CANDLES:
        reason = (
            f"Insufficient closed D1 candles for EURUSD: "
            f"{n} < {_MIN_EMA_CANDLES} required for EMA20."
        ).replace("EURUSD", symbol)
        reason = (
            f"Insufficient closed D1 candles: "
            f"{n} < {_MIN_EMA_CANDLES} required for EMA20."
        )
        logger.debug(
            '{"event":"market_regime_unknown","symbol":"%s","eligible":%d,'
            '"reason":"%s"}',
            symbol, n, reason,
        )
        return MarketRegimeResult(regime=MarketRegime.UNKNOWN, reason=reason, **base)

    closes = [float(c.close) for c in eligible]
    highs  = [float(c.high)  for c in eligible]
    lows   = [float(c.low)   for c in eligible]

    # 2. EMA20
    ema20 = _ema(closes[-_MIN_EMA_CANDLES:], _MIN_EMA_CANDLES)
    latest_close = closes[-1]
    base["ema20"] = round(ema20, 6)

    # 3. True Range sequence (skip index 0, needs prev_close)
    trs = [_true_range(highs[i], lows[i], closes[i - 1]) for i in range(1, n)]

    if trs:
        base["current_atr"] = round(trs[-1], 6)

    if len(trs) >= _ATR_HISTORY_CANDLES:
        avg_atr = sum(trs[-_ATR_HISTORY_CANDLES:]) / _ATR_HISTORY_CANDLES
        base["avg_atr_30d"] = round(avg_atr, 6)

    # 4. High-Volatility check (takes precedence over trend/range)
    if base["current_atr"] is not None and base["avg_atr_30d"] is not None:
        if base["current_atr"] > _HIGH_VOL_MULTIPLIER * base["avg_atr_30d"]:
            reason = (
                f"D1 ATR {base['current_atr']:.5f} exceeds "
                f"{_HIGH_VOL_MULTIPLIER}x 30-day average "
                f"{base['avg_atr_30d']:.5f}."
            )
            logger.info(
                '{"event":"market_regime_evaluated","symbol":"%s",'
                '"regime":"HIGH_VOLATILITY","current_atr":%.5f,"avg_atr_30d":%.5f}',
                symbol, base["current_atr"], base["avg_atr_30d"],
            )
            return MarketRegimeResult(
                regime=MarketRegime.HIGH_VOLATILITY, reason=reason, **base
            )

    # 5. Trend / Range via HH / LL structure (last 4 candles)
    window = eligible[-4:]
    wh = [float(c.high) for c in window]
    wl = [float(c.low)  for c in window]

    hh = all(wh[i] > wh[i - 1] for i in range(1, len(wh)))
    ll = all(wl[i] < wl[i - 1] for i in range(1, len(wl)))

    base["higher_highs"] = hh
    base["lower_lows"] = ll

    if latest_close > ema20 and hh:
        regime = MarketRegime.TRENDING_BULLISH
        reason = (
            f"Close {latest_close:.5f} above EMA20 {ema20:.5f} "
            f"with higher highs. Regime: TRENDING_BULLISH."
        )
    elif latest_close < ema20 and ll:
        regime = MarketRegime.TRENDING_BEARISH
        reason = (
            f"Close {latest_close:.5f} below EMA20 {ema20:.5f} "
            f"with lower lows. Regime: TRENDING_BEARISH."
        )
    else:
        regime = MarketRegime.RANGING
        reason = (
            f"Close {latest_close:.5f} vs EMA20 {ema20:.5f} — "
            f"no consistent HH/LL structure. Regime: RANGING."
        )

    logger.info(
        '{"event":"market_regime_evaluated","symbol":"%s","regime":"%s",'
        '"ema20":%.5f,"close":%.5f,"eligible_candles":%d}',
        symbol, regime.value, ema20, latest_close, n,
    )
    return MarketRegimeResult(regime=regime, reason=reason, **base)
