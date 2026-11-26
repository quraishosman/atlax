"""
Deterministic Lower-Timeframe Market Structure Break (MSB) detector.

Authority:
    docs/09_CONFIDENCE_ENGINE.md  -- msb_on_ltf is an approved confidence factor
    docs/07_DETECTOR_SPECIFICATION.md -- Market Structure Detector concept

Responsibility:
    Detect whether a Market Structure Break occurred on the LTF AFTER the
    CRT sweep candle close time, using only closed candle data.

    Bullish MSB: any post-sweep LTF high exceeds the reference window highest high.
    Bearish MSB: any post-sweep LTF low falls below the reference window lowest low.

Returns:
    True   -- MSB confirmed
    False  -- No MSB detected
    None   -- Insufficient data (UNKNOWN)

Non-negotiables (AGENTS.md):
    - No hardcoded thresholds.
    - Returns None when data is insufficient.
    - Deterministic, closed-candle only.
    - No execution, strategy, or risk decisions.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from core.models.candle import Candle, CandleSequence


def _utc_naive(dt: datetime) -> datetime:
    """Normalize to UTC naive for comparison."""
    if dt.tzinfo is None:
        return dt
    return dt.astimezone(timezone.utc).replace(tzinfo=None)


@dataclass(frozen=True)
class MSBResult:
    """Result of an LTF Market Structure Break evaluation."""

    detected: Optional[bool]
    """True = MSB confirmed, False = no MSB, None = insufficient data."""

    direction: str
    """'bullish' or 'bearish'."""

    pre_sweep_candles: int
    """Closed LTF candles in the reference window (before sweep)."""

    post_sweep_candles: int
    """Closed LTF candles after the sweep close time."""

    reference_extreme: Optional[float]
    """Highest high (bullish) or lowest low (bearish) of the reference window."""

    break_level: Optional[float]
    """The price that caused the MSB, if detected."""

    reason: str
    """Human-readable explanation."""


def evaluate_ltf_msb(
    ltf_sequence: Optional[CandleSequence],
    *,
    sweep_close_time: datetime,
    direction: str,
    min_pre_sweep_candles: int = 2,
    min_post_sweep_candles: int = 1,
) -> MSBResult:
    """
    Detect a Market Structure Break on the LTF after the CRT sweep.

    Args:
        ltf_sequence:            Closed LTF candle sequence.
        sweep_close_time:        Close time of the HTF sweep candle.
        direction:               'bullish' or 'bearish'.
        min_pre_sweep_candles:   Min closed candles in reference window.
        min_post_sweep_candles:  Min closed candles after sweep.

    Returns:
        MSBResult with detected = True / False / None (UNKNOWN).
    """
    _dir = (direction or "").lower()
    sweep_naive = _utc_naive(sweep_close_time)

    if ltf_sequence is None or not ltf_sequence.candles:
        return MSBResult(None, _dir, 0, 0, None, None, "LTF sequence unavailable.")

    pre: list[Candle] = []
    post: list[Candle] = []
    for c in ltf_sequence.candles:
        if not c.is_closed:
            continue
        if _utc_naive(c.close_time) <= sweep_naive:
            pre.append(c)
        else:
            post.append(c)

    if len(pre) < min_pre_sweep_candles:
        return MSBResult(
            None, _dir, len(pre), len(post), None, None,
            f"Insufficient pre-sweep LTF candles: {len(pre)} / {min_pre_sweep_candles} required.",
        )
    if len(post) < min_post_sweep_candles:
        return MSBResult(
            None, _dir, len(pre), len(post), None, None,
            f"Insufficient post-sweep LTF candles: {len(post)} / {min_post_sweep_candles} required.",
        )

    if _dir == "bullish":
        ref = float(max(c.high for c in pre))
        for c in post:
            ch = float(c.high)
            if ch > ref:
                return MSBResult(
                    True, _dir, len(pre), len(post), ref, ch,
                    f"Bullish MSB: LTF high {ch:.5f} broke above reference {ref:.5f}.",
                )
        post_max = float(max(c.high for c in post))
        return MSBResult(
            False, _dir, len(pre), len(post), ref, None,
            f"No bullish MSB: post-sweep high {post_max:.5f} did not break reference {ref:.5f}.",
        )

    if _dir == "bearish":
        ref = float(min(c.low for c in pre))
        for c in post:
            cl = float(c.low)
            if cl < ref:
                return MSBResult(
                    True, _dir, len(pre), len(post), ref, cl,
                    f"Bearish MSB: LTF low {cl:.5f} broke below reference {ref:.5f}.",
                )
        post_min = float(min(c.low for c in post))
        return MSBResult(
            False, _dir, len(pre), len(post), ref, None,
            f"No bearish MSB: post-sweep low {post_min:.5f} did not break reference {ref:.5f}.",
        )

    return MSBResult(
        None, _dir, len(pre), len(post), None, None,
        f"Unknown CRT direction: '{direction}'.",
    )
