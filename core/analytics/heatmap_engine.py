"""
ATLAX Heatmap Engine.

A valid CRT heatmap zone forms when:
  1. The PARENT candle is fully closed.
  2. The SWEEP candle (currently forming) has NOT violated 65% of the parent
     candle's range toward the target side.

     - Bearish setup: sweep candle has NOT closed below parent_low + 65% of range
       (i.e., price is still near the high end, potentially sweeping the CRH)
     - Bullish setup: sweep candle has NOT closed above parent_high - 65% of range
       (i.e., price is still near the low end, potentially sweeping the CRL)

The entry is REMOVED from the heatmap as soon as the sweep candle violates
that 65% threshold, because the CRT setup is then invalidated.
"""

import json
import os
import logging
from core.models.candle import CandleSequence

logger = logging.getLogger("atlax.heatmap")

_INVALIDATION_RATIO = 0.65  # 65% of the parent candle's range


class HeatmapEngine:
    def __init__(self, output_file: str = "data/heatmap.json", state_store=None):
        self.output_file = output_file
        self._state_store = state_store
        loaded = state_store.get("heatmap.state", {}) if state_store is not None else {}
        self.state: dict = loaded if isinstance(loaded, dict) else {}

    def update(self, symbol: str, timeframe: str, seq: CandleSequence) -> None:
        """
        Called every scan cycle with the latest candle sequence.
        Evaluates whether a valid CRT heatmap zone exists for this symbol+timeframe.
        """
        if len(seq.candles) < 2:
            return

        # The last CLOSED candle is the parent (the candle whose range defines the CRT zone)
        parent = seq.candles[-2]
        # The currently FORMING candle is the sweep candle
        sweep = seq.candles[-1]

        if not parent.is_closed:
            return  # Parent must be fully closed

        parent_high = float(parent.high)
        parent_low  = float(parent.low)
        parent_range = parent_high - parent_low

        if parent_range <= 0:
            # Degenerate candle — no valid zone
            self._remove(symbol, timeframe)
            return

        sweep_close = float(sweep.close)

        # --- Bullish Setup: sweep is testing the parent low (CRL) ---
        # The sweep candle is VALID as long as it hasn't closed above
        # parent_low + 65% of the parent range (otherwise the low was absorbed, not swept)
        bullish_violation_level = parent_low + _INVALIDATION_RATIO * parent_range
        bullish_candidate = sweep_close < parent_low  # sweep actually dipped below the CRL
        bullish_invalidated = sweep_close > bullish_violation_level

        # --- Bearish Setup: sweep is testing the parent high (CRH) ---
        # The sweep candle is VALID as long as it hasn't closed below
        # parent_high - 65% of the parent range
        bearish_violation_level = parent_high - _INVALIDATION_RATIO * parent_range
        bearish_candidate = sweep_close > parent_high  # sweep actually broke above the CRH
        bearish_invalidated = sweep_close < bearish_violation_level

        # Determine which setup (if any) is active
        key = f"{symbol}_{timeframe}"

        if bullish_candidate and not bullish_invalidated:
            bias   = "Bullish Setup"
            dist   = sweep_close - parent_low   # negative = already swept (below CRL)
        elif bearish_candidate and not bearish_invalidated:
            bias   = "Bearish Setup"
            dist   = parent_high - sweep_close  # negative = already swept (above CRH)
        else:
            # No valid sweep in progress — remove this pair/timeframe from the heatmap
            self._remove(symbol, timeframe)
            return

        # Convert raw price distance to approximate pips
        multiplier = 100 if "JPY" in symbol else 10000
        if "XAU" in symbol:
            multiplier = 10

        dist_pips = round(dist * multiplier, 1)

        if dist_pips < 0:
            status = "Sweeping Now"
        elif dist_pips <= 3.0:
            status = "Imminent"
        else:
            status = "Ranging"

        self.state[key] = {
            "symbol":        symbol,
            "timeframe":     timeframe,
            "status":        status,
            "bias":          bias,
            "distance_pips": dist_pips,
            "parent_high":   round(parent_high, 5),
            "parent_low":    round(parent_low, 5),
            "violation_pct": _INVALIDATION_RATIO * 100,
        }
        self._save()

    def _remove(self, symbol: str, timeframe: str) -> None:
        """Remove an invalidated heatmap entry and persist."""
        key = f"{symbol}_{timeframe}"
        if key in self.state:
            del self.state[key]
            logger.info(
                '{"event":"heatmap_invalidated","symbol":"%s","timeframe":"%s"}',
                symbol, timeframe,
            )
            self._save()

    def _save(self) -> None:
        if self._state_store is not None:
            self._state_store.set("heatmap.state", self.state)
            return
        os.makedirs(os.path.dirname(self.output_file) or ".", exist_ok=True)
        try:
            with open(self.output_file, "w") as f:
                json.dump(self.state, f)
        except Exception as e:
            logger.error(f"Failed to save heatmap: {e}")
