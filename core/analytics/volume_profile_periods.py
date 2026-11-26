"""
ATLAX Volume Profile Period Boundary Calculator.

Computes the [period_start_utc, period_end_utc) half-open interval for
the *most recently completed* PREVIOUS_DAY or PREVIOUS_WEEK period,
relative to a reference UTC datetime.

The current incomplete period is never included. This is a hard
requirement from Doc 40 §3 and §5.1 to prevent lookahead.

Authority:
    docs/40_VOLUME_PROFILE_CONFLUENCE_PLAN.md §3 and §5.1
    docs/decisions/ADR-017.md

Design:
    - All boundaries are computed in UTC only. No timezone conversion.
    - The reference time is always an explicit parameter (never datetime.now())
      so calculations are deterministic and testable.
    - PREVIOUS_DAY:  [prev_day_00:00Z, today_00:00Z)
    - PREVIOUS_WEEK: [prev_monday_00:00Z, this_monday_00:00Z)
                     ISO week: Monday=0 ... Sunday=6
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Tuple

# Approved period labels (ADR-017, Doc 40 §3)
APPROVED_PERIODS = frozenset({"PREVIOUS_DAY", "PREVIOUS_WEEK"})


def period_boundaries(
    period_label: str,
    reference_utc: datetime,
) -> Tuple[datetime, datetime]:
    """
    Return the [start, end) half-open UTC interval for the most recently
    completed period relative to *reference_utc*.

    Args:
        period_label: One of ``PREVIOUS_DAY`` or ``PREVIOUS_WEEK``.
        reference_utc: The UTC datetime used as "now". Must be timezone-aware.

    Returns:
        (period_start_utc, period_end_utc) — both timezone-aware UTC datetimes.

    Raises:
        ValueError: For unknown period_label or naive reference_utc.

    Examples:
        reference = 2026-08-17 14:30 UTC

        PREVIOUS_DAY  → [2026-08-16 00:00Z, 2026-08-17 00:00Z)
        PREVIOUS_WEEK → [2026-08-10 00:00Z, 2026-08-17 00:00Z)
          (week of Mon 11 Aug is the current incomplete week; Mon 04 Aug
           is a completed week — wait, 2026-08-17 is a Monday, so
           previous week is Mon 10 Aug – Sun 16 Aug, i.e.
           [2026-08-10 00:00Z, 2026-08-17 00:00Z) )
    """
    if period_label not in APPROVED_PERIODS:
        raise ValueError(
            f"Unknown profile period {period_label!r}. "
            f"Approved: {sorted(APPROVED_PERIODS)}"
        )
    if reference_utc.tzinfo is None:
        raise ValueError(
            "reference_utc must be a timezone-aware datetime (UTC). "
            f"Got naive datetime: {reference_utc!r}"
        )

    # Normalise to UTC
    ref = reference_utc.astimezone(timezone.utc)

    if period_label == "PREVIOUS_DAY":
        return _previous_day(ref)
    else:  # PREVIOUS_WEEK
        return _previous_week(ref)


def _previous_day(ref: datetime) -> Tuple[datetime, datetime]:
    """Return [yesterday 00:00Z, today 00:00Z)."""
    today_midnight = ref.replace(hour=0, minute=0, second=0, microsecond=0)
    yesterday_midnight = today_midnight - timedelta(days=1)
    return yesterday_midnight, today_midnight


def _previous_week(ref: datetime) -> Tuple[datetime, datetime]:
    """Return [last Monday 00:00Z, this Monday 00:00Z).

    ISO weekday: Monday=0, Sunday=6.
    The "current week" starts on this Monday (or today if today IS Monday).
    The "previous week" starts on the Monday 7 days before the current week start.
    """
    ref_midnight = ref.replace(hour=0, minute=0, second=0, microsecond=0)
    # weekday(): 0=Monday, 6=Sunday
    days_since_monday = ref_midnight.weekday()
    this_monday = ref_midnight - timedelta(days=days_since_monday)
    prev_monday = this_monday - timedelta(weeks=1)
    return prev_monday, this_monday


def candles_in_period(
    candles: tuple,
    period_start_utc: datetime,
    period_end_utc: datetime,
) -> tuple:
    """
    Filter a candle tuple to those whose entire interval falls within
    the half-open period [period_start_utc, period_end_utc).

    Only closed candles are eligible. Forming candles are always rejected.

    Conditions for inclusion:
        candle.is_closed is True
        candle.open_time >= period_start_utc
        candle.close_time <= period_end_utc

    Args:
        candles: Tuple of Candle objects (any order, any timeframe).
        period_start_utc: Inclusive start of the period.
        period_end_utc: Exclusive end of the period.

    Returns:
        Tuple of eligible Candle objects, in the same order as the input.
    """
    eligible = []
    for candle in candles:
        if not candle.is_closed:
            continue
        # Normalise candle timestamps to UTC for comparison
        ot = _to_utc(candle.open_time)
        ct = _to_utc(candle.close_time)
        if ot >= period_start_utc and ct <= period_end_utc:
            eligible.append(candle)
    return tuple(eligible)


def _to_utc(dt: datetime) -> datetime:
    """Return a UTC-aware version of dt."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)
