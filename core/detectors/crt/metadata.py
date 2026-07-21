"""
ATLAX CRT Quality Metadata Computation.

Pure functions that compute the quality metadata fields defined in
CRT-QUALITY-001. These fields are passed to the Confidence Engine
for scoring — they are quality inputs, NOT binary detection gates.

Authority Documents:
    - docs/rulebooks/CRT_RULEBOOK.md, CRT-QUALITY-001 (field definitions)
    - docs/rulebooks/CRT_RULEBOOK.md, CRT-SESSION-001 (kill zone timing)
    - docs/09_CONFIDENCE_ENGINE.md (downstream consumer)

Design Decisions:
    - All functions are pure and stateless.
    - Returns float for ratios and pip distances (Confidence Engine
      works with float scores, not Decimal).
    - Returns None when input data is insufficient, per ATLAX policy
      of returning UNKNOWN instead of guessing.
"""

from __future__ import annotations

from datetime import datetime, time
from decimal import Decimal
from typing import Optional

from core.models.candle import Candle
from core.settings.crt_config import KillZone


def compute_sweep_distance_pips(
    parent: Candle,
    sweep: Candle,
    pip_size: Decimal,
    direction: str,
) -> Optional[float]:
    """
    Rule: CRT-QUALITY-001 — sweep_distance_pips
    Status: APPROVED

    How far price extended beyond the parent CRH or CRL, in pips.

    For bullish: distance = |parent.low - sweep.low| / pip_size
    For bearish: distance = |sweep.high - parent.high| / pip_size

    Args:
        parent: The parent candle.
        sweep: The sweep candle.
        pip_size: The pip size for the instrument (e.g., 0.0001).
        direction: "bullish" or "bearish".

    Returns:
        The sweep distance in pips, or None if pip_size is zero.
    """
    if pip_size <= Decimal("0"):
        return None

    if direction == "bullish":
        distance = abs(parent.low - sweep.low)
    elif direction == "bearish":
        distance = abs(sweep.high - parent.high)
    else:
        raise ValueError(
            f"direction must be 'bullish' or 'bearish', got {direction!r}"
        )

    return float(distance / pip_size)


def compute_close_location_ratio(
    parent: Candle,
    sweep: Candle,
) -> Optional[float]:
    """
    Rule: CRT-QUALITY-001 — close_location_ratio
    Status: APPROVED

    Where the sweep candle closed within the parent range.
    0.0 = at CRL, 1.0 = at CRH.

    Formula:
        ratio = (sweep.close - parent.low) / (parent.high - parent.low)

    For bullish CRT: higher ratio = stronger close-back (price
    recovered further into the range after the sweep).
    For bearish CRT: lower ratio = stronger close-back.

    Args:
        parent: The parent candle.
        sweep: The sweep candle.

    Returns:
        A float between 0.0 and 1.0, or None if parent range is zero.
    """
    parent_range = parent.high - parent.low
    if parent_range <= Decimal("0"):
        return None

    ratio = float((sweep.close - parent.low) / parent_range)

    # Clamp to [0.0, 1.0] — sweep close may be slightly outside
    # the range due to the close-back boundary being inclusive.
    return max(0.0, min(1.0, ratio))


def compute_sweep_wick_ratio(
    parent: Candle,
    sweep: Candle,
    direction: str,
) -> Optional[float]:
    """
    Rule: CRT-QUALITY-001 — sweep_wick_ratio
    Status: APPROVED

    The ratio of the sweep candle's wick length (the portion that
    extends beyond CRH/CRL) to the parent range.

    A larger ratio indicates a more aggressive liquidity grab.

    For bullish: wick = |parent.low - sweep.low| / parent.range
    For bearish: wick = |sweep.high - parent.high| / parent.range

    Args:
        parent: The parent candle.
        sweep: The sweep candle.
        direction: "bullish" or "bearish".

    Returns:
        A float >= 0.0, or None if parent range is zero.
    """
    parent_range = parent.high - parent.low
    if parent_range <= Decimal("0"):
        return None

    if direction == "bullish":
        wick = abs(parent.low - sweep.low)
    elif direction == "bearish":
        wick = abs(sweep.high - parent.high)
    else:
        raise ValueError(
            f"direction must be 'bullish' or 'bearish', got {direction!r}"
        )

    return float(wick / parent_range)


def compute_session_alignment(
    candle_time: datetime,
    kill_zones: tuple[KillZone, ...],
) -> bool:
    """
    Rule: CRT-SESSION-001, CRT-QUALITY-001 — session_alignment
    Status: APPROVED

    Whether the candle occurred during a configured kill zone
    (e.g., London Open, New York AM).

    CRT setups during kill zones have the highest probability.

    Args:
        candle_time: The UTC datetime of the candle to check
                     (typically the sweep candle's open_time).
        kill_zones: The configured kill zone session windows.

    Returns:
        True if the candle time falls within any kill zone.
    """
    check_time: time = candle_time.time()

    for kill_zone in kill_zones:
        if kill_zone.contains(check_time):
            return True

    return False
