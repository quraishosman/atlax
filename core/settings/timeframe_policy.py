"""Operational timeframe policy for active ATLAX runtime boundaries."""

from __future__ import annotations

RETIRED_TIMEFRAMES = frozenset({"M1"})


def is_retired_timeframe(value: str) -> bool:
    """Return whether a timeframe is retired from active runtime use."""

    return str(value).strip().upper() in RETIRED_TIMEFRAMES
