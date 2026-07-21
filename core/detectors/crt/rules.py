"""
ATLAX CRT Detection Rules — Pure Functions.

Every function in this module implements exactly one approved rule
from docs/rulebooks/CRT_RULEBOOK.md. All functions are:
    - Pure: no side effects, no I/O, no state mutation.
    - Deterministic: same inputs always produce the same output.
    - Typed: full type hints on all parameters and returns.
    - Traced: docstring cites the exact Rule ID it implements.

Authority Documents:
    - docs/rulebooks/CRT_RULEBOOK.md (all rule definitions)
    - docs/02_ENGINEERING_STANDARDS.md (no magic numbers, pure functions)

Usage:
    These functions are called by CRTDetector.detect() in detector.py.
    They are also directly unit-testable in isolation.
"""

from __future__ import annotations

from decimal import Decimal

from core.models.candle import Candle


# ---------------------------------------------------------------------------
# Gate Rules (must ALL pass before detection is attempted)
# ---------------------------------------------------------------------------


def all_candles_closed(
    parent: Candle,
    sweep: Candle,
    confirmation: Candle | None,
) -> bool:
    """
    Rule: CRT-DATA-002
    Status: APPROVED

    CRT evaluation must use CLOSED candles only. A forming (live,
    unclosed) candle must not be used to confirm a CRT sweep or
    close-back condition.

    Args:
        parent: The parent (reference) candle.
        sweep: The sweep (manipulation) candle.
        confirmation: The confirmation candle. May be None if
                      require_confirmation_candle is False.

    Returns:
        True if all provided candles are closed.
    """
    if not parent.is_closed:
        return False
    if not sweep.is_closed:
        return False
    if confirmation is not None and not confirmation.is_closed:
        return False
    return True


def parent_range_meets_minimum(
    parent: Candle,
    atr: Decimal,
    min_atr_multiple: float,
) -> bool:
    """
    Rule: CRT-PARENT-004
    Status: APPROVED

    Very small parent candles (doji or near-zero range) must be ignored.
    The parent range must be at least min_atr_multiple * ATR.

    Args:
        parent: The parent candle.
        atr: The current Average True Range value for the instrument.
        min_atr_multiple: Configured minimum ATR multiple (e.g., 0.5).

    Returns:
        True if parent.range >= min_atr_multiple * atr.
    """
    threshold = atr * Decimal(str(min_atr_multiple))
    return parent.range >= threshold


def is_both_sides_sweep(parent: Candle, sweep: Candle) -> bool:
    """
    Rule: CRT-INVALID-004
    Status: APPROVED

    A sweep candle that breaches BOTH the parent high AND the parent
    low is chaotic price action (both-sides sweep). This immediately
    invalidates the sequence.

    Args:
        parent: The parent candle.
        sweep: The sweep candle.

    Returns:
        True if BOTH sides were breached (invalid — must abort).
        False if only one side (or neither) was breached (OK to continue).
    """
    breached_low = sweep.low < parent.low
    breached_high = sweep.high > parent.high
    return breached_low and breached_high


# ---------------------------------------------------------------------------
# Sweep Validation Rules
# ---------------------------------------------------------------------------


def has_strict_bullish_sweep(parent: Candle, sweep: Candle) -> bool:
    """
    Rule: CRT-SWEEP-001, CRT-SWEEP-INVALID-002
    Status: APPROVED

    For a bullish CRT, the sweep candle must extend STRICTLY below
    the parent's CRL. Equality does NOT count as a sweep.

    Deterministic condition:
        sweep.low < parent.low

    Args:
        parent: The parent candle (CRL = parent.low).
        sweep: The sweep candle.

    Returns:
        True if the sweep strictly breached below CRL.
    """
    return sweep.low < parent.low


def has_strict_bearish_sweep(parent: Candle, sweep: Candle) -> bool:
    """
    Rule: CRT-SWEEP-002, CRT-SWEEP-INVALID-002
    Status: APPROVED

    For a bearish CRT, the sweep candle must extend STRICTLY above
    the parent's CRH. Equality does NOT count as a sweep.

    Deterministic condition:
        sweep.high > parent.high

    Args:
        parent: The parent candle (CRH = parent.high).
        sweep: The sweep candle.

    Returns:
        True if the sweep strictly breached above CRH.
    """
    return sweep.high > parent.high


def sweep_open_inside_range(
    parent: Candle,
    sweep: Candle,
    max_gap_tolerance: Decimal,
    direction: str,
) -> bool:
    """
    Rule: CRT-SWEEP-INVALID-003
    Status: APPROVED

    Gap candles are invalid if the sweep candle opens beyond the
    parent range boundary by more than the configured tolerance.
    Small gaps within tolerance are allowed but flagged with lower
    quality metadata.

    For bullish: sweep.open must be >= (parent.low - tolerance)
    For bearish: sweep.open must be <= (parent.high + tolerance)

    Args:
        parent: The parent candle.
        sweep: The sweep candle.
        max_gap_tolerance: Maximum allowed gap in price units.
        direction: "bullish" or "bearish".

    Returns:
        True if the sweep open is inside the range (or within tolerance).
    """
    if direction == "bullish":
        return sweep.open >= (parent.low - max_gap_tolerance)
    elif direction == "bearish":
        return sweep.open <= (parent.high + max_gap_tolerance)
    else:
        raise ValueError(
            f"direction must be 'bullish' or 'bearish', got {direction!r}"
        )


# ---------------------------------------------------------------------------
# Close-Back Rules
# ---------------------------------------------------------------------------


def bullish_close_back(parent: Candle, sweep: Candle) -> bool:
    """
    Rule: CRT-SWEEP-003, CRT-CLOSE-001, CRT-BULL-001 (condition 2)
    Status: APPROVED

    For a valid bullish CRT, the sweep candle's body must close back
    inside the parent range. A boundary close exactly on CRL is valid.

    Deterministic condition:
        sweep.close >= parent.low

    This is the single most critical CRT rule. It distinguishes a
    manipulation sweep from a genuine breakout.

    Args:
        parent: The parent candle (CRL = parent.low).
        sweep: The sweep candle.

    Returns:
        True if the sweep candle closed at or above CRL.
    """
    return sweep.close >= parent.low


def bearish_close_back(parent: Candle, sweep: Candle) -> bool:
    """
    Rule: CRT-SWEEP-003, CRT-CLOSE-001, CRT-BEAR-001 (condition 2)
    Status: APPROVED

    For a valid bearish CRT, the sweep candle's body must close back
    inside the parent range. A boundary close exactly on CRH is valid.

    Deterministic condition:
        sweep.close <= parent.high

    Args:
        parent: The parent candle (CRH = parent.high).
        sweep: The sweep candle.

    Returns:
        True if the sweep candle closed at or below CRH.
    """
    return sweep.close <= parent.high


# ---------------------------------------------------------------------------
# Confirmation Rules
# ---------------------------------------------------------------------------


def bullish_confirmation(sweep: Candle, confirmation: Candle) -> bool:
    """
    Rule: CRT-BULL-001 (condition 3)
    Status: APPROVED

    The confirmation candle must close above the sweep candle's high,
    signaling a market structure shift (MSS) confirming the bullish CRT.

    Deterministic condition:
        confirmation.close > sweep.high

    Args:
        sweep: The sweep candle.
        confirmation: The confirmation candle.

    Returns:
        True if the confirmation candle closed above sweep.high.
    """
    return confirmation.close > sweep.high


def bearish_confirmation(sweep: Candle, confirmation: Candle) -> bool:
    """
    Rule: CRT-BEAR-001 (condition 3)
    Status: APPROVED

    The confirmation candle must close below the sweep candle's low,
    signaling a market structure shift (MSS) confirming the bearish CRT.

    Deterministic condition:
        confirmation.close < sweep.low

    Args:
        sweep: The sweep candle.
        confirmation: The confirmation candle.

    Returns:
        True if the confirmation candle closed below sweep.low.
    """
    return confirmation.close < sweep.low
