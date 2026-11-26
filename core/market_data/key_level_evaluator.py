"""Pure V1 key-level evaluation for closed ATLAX parent candles."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Iterable, Optional

from core.models.candle import Candle
from core.models.key_level import KeyLevel, KeyLevelEvaluation, KeyLevelMatch
from core.settings.key_level_config import KeyLevelConfig


def simple_atr_for_parent(
    candles: Iterable[Candle],
    parent: Optional[Candle],
    period: int,
) -> Optional[Decimal]:
    """Return the simple true-range mean ending at one closed parent.

    The calculation is intentionally as-of the parent close.  Later candles
    must not change a cached evaluation for an already closed parent.
    """
    if parent is None or not parent.is_closed or period < 1:
        return None
    history = sorted(
        (
            candle
            for candle in candles
            if candle.is_closed
            and candle.close_time.astimezone(timezone.utc)
            <= parent.close_time.astimezone(timezone.utc)
        ),
        key=lambda candle: candle.open_time,
    )
    if len(history) < period + 1:
        return None
    true_ranges = []
    for index in range(1, len(history)):
        candle = history[index]
        previous = history[index - 1]
        true_ranges.append(
            max(
                candle.high - candle.low,
                abs(candle.high - previous.close),
                abs(candle.low - previous.close),
            )
        )
    values = true_ranges[-period:]
    return sum(values, Decimal("0")) / Decimal(str(len(values)))


class KeyLevelEvaluator:
    """Evaluate parent location without detecting CRT or making trade decisions."""

    def __init__(self, config: KeyLevelConfig) -> None:
        self._config = config

    def evaluate(
        self,
        *,
        parent: Optional[Candle],
        parent_atr: Optional[Decimal],
        pip_size: Decimal,
        daily_candles: Iterable[Candle],
        weekly_candles: Iterable[Candle],
        h4_candles: Iterable[Candle],
        d1_candles: Iterable[Candle],
        volume_profile_levels: Optional[list] = None,
    ) -> KeyLevelEvaluation:
        """Return a deterministic result using only data known at parent close.

        Args:
            volume_profile_levels: Optional list of KeyLevel objects
                produced by the Volume Profile bridge. When provided,
                these are included in the matching pass alongside
                period and swing levels. UNKNOWN Volume Profile results
                should be converted to an empty list by the bridge
                before reaching this method.
        """
        if parent is None:
            return self._unknown("Parent candle is unavailable.", None, "UNKNOWN")
        if not parent.is_closed:
            return self._unknown("Parent candle is not closed.", parent, "UNKNOWN")
        if parent_atr is None or parent_atr <= Decimal("0"):
            return self._unknown("Parent timeframe ATR is missing or not positive.", parent, "UNKNOWN")
        if pip_size <= Decimal("0"):
            return self._unknown("Instrument pip size is missing or not positive.", parent, "UNKNOWN")

        # A parent must never use the still-forming or same-period D1/W1 bar
        # as its "previous" period.  The parent open is the strict cutoff.
        daily = self._closed_before(daily_candles, parent.open_time)
        weekly = self._closed_before(weekly_candles, parent.open_time)
        h4 = self._closed_before(h4_candles, parent.close_time)
        d1 = self._closed_before(d1_candles, parent.close_time)
        if not daily or not weekly or not h4 or not d1:
            missing = tuple(
                name
                for name, candles in (
                    ("D1", daily),
                    ("W1", weekly),
                    ("H4", h4),
                )
                if not candles
            )
            return self._unknown(
                "Required closed key-level history is unavailable: " + ", ".join(missing),
                parent,
                "UNKNOWN",
                missing_sources=missing,
            )

        tolerance = max(
            parent_atr * self._config.tolerance_atr_multiplier,
            pip_size * self._config.minimum_tolerance_pips,
        )
        levels = self._period_levels(daily, weekly)
        levels.extend(
            self._confirmed_swings(
                h4,
                "H4",
                parent.close_time,
            )
        )
        levels.extend(
            self._confirmed_swings(
                d1,
                "D1",
                parent.close_time,
            )
        )
        levels = self._latest_swing_limited(levels)

        # --- Volume Profile levels (Doc 40 §6, Phase 3) ---
        if volume_profile_levels:
            levels.extend(volume_profile_levels)

        matches = tuple(
            self._match(parent, level, tolerance, parent_atr)
            for level in levels
            if self._is_match(parent, level.price, tolerance)
        )
        zone = bool(matches)
        return KeyLevelEvaluation(
            status="VALID",
            parent_at_htf_zone=zone,
            symbol=parent.symbol,
            parent_timeframe=parent.timeframe,
            parent_open_time=parent.open_time,
            tolerance=tolerance,
            tolerance_atr_multiplier=self._config.tolerance_atr_multiplier,
            minimum_tolerance_pips=self._config.minimum_tolerance_pips,
            matched_levels=matches,
            missing_sources=(),
            configuration_snapshot_id=self._config.snapshot_id,
            explanation=(
                f"Parent matched {len(matches)} configured key level(s)."
                if zone
                else "Parent did not match any configured key level."
            ),
        )


    @staticmethod
    def _closed_before(candles: Iterable[Candle], cutoff: datetime) -> list[Candle]:
        cutoff = cutoff.astimezone(timezone.utc)
        return [
            candle
            for candle in candles
            if candle.is_closed
            and candle.close_time.astimezone(timezone.utc) <= cutoff
        ]

    @staticmethod
    def _period_levels(daily: list[Candle], weekly: list[Candle]) -> list[KeyLevel]:
        levels: list[KeyLevel] = []
        previous_day = daily[-1] if daily else None
        previous_week = weekly[-1] if weekly else None
        if previous_day is not None:
            levels.extend(
                [
                    KeyLevel("PREVIOUS_DAY_HIGH", previous_day.high, "D1", previous_day.open_time),
                    KeyLevel("PREVIOUS_DAY_LOW", previous_day.low, "D1", previous_day.open_time),
                ]
            )
        if previous_week is not None:
            levels.extend(
                [
                    KeyLevel("PREVIOUS_WEEK_HIGH", previous_week.high, "W1", previous_week.open_time),
                    KeyLevel("PREVIOUS_WEEK_LOW", previous_week.low, "W1", previous_week.open_time),
                ]
            )
        return levels

    def _confirmed_swings(
        self,
        candles: list[Candle],
        timeframe: str,
        parent_close: datetime,
    ) -> list[KeyLevel]:
        left = self._config.swing_left_bars
        right = self._config.swing_right_bars
        levels: list[KeyLevel] = []
        for index in range(left, len(candles) - right):
            center = candles[index]
            right_side = candles[index + 1 : index + right + 1]
            if not right_side or right_side[-1].close_time > parent_close:
                continue
            left_side = candles[index - left : index]
            if len(left_side) < left:
                continue
            if all(center.high > item.high for item in (*left_side, *right_side)):
                levels.append(KeyLevel(f"{timeframe}_SWING_HIGH", center.high, timeframe, center.open_time))
            if all(center.low < item.low for item in (*left_side, *right_side)):
                levels.append(KeyLevel(f"{timeframe}_SWING_LOW", center.low, timeframe, center.open_time))
        return levels

    def _latest_swing_limited(self, levels: list[KeyLevel]) -> list[KeyLevel]:
        period = [level for level in levels if level.level_type.startswith("PREVIOUS_")]
        swings = [level for level in levels if not level.level_type.startswith("PREVIOUS_")]
        selected: list[KeyLevel] = []
        for level_type in sorted({level.level_type for level in swings}):
            candidates = [level for level in swings if level.level_type == level_type]
            candidates.sort(key=lambda level: level.reference_open_time, reverse=True)
            selected.extend(candidates[: self._config.maximum_swing_levels])
        return period + selected

    @staticmethod
    def _is_match(parent: Candle, price: Decimal, tolerance: Decimal) -> bool:
        return (
            price >= parent.low - tolerance
            and price <= parent.high + tolerance
        )

    @staticmethod
    def _match(
        parent: Candle,
        level: KeyLevel,
        tolerance: Decimal,
        parent_atr: Decimal,
    ) -> KeyLevelMatch:
        if parent.low <= level.price <= parent.high:
            distance = Decimal("0")
        else:
            distance = min(abs(level.price - parent.low), abs(level.price - parent.high))
        return KeyLevelMatch(
            level_type=level.level_type,
            price=level.price,
            source_timeframe=level.source_timeframe,
            distance=distance,
            distance_atr=distance / parent_atr if parent_atr > Decimal("0") else None,
        )

    def _unknown(
        self,
        explanation: str,
        parent: Optional[Candle],
        status: str,
        *,
        missing_sources: tuple[str, ...] = (),
    ) -> KeyLevelEvaluation:
        now = datetime.now(timezone.utc)
        return KeyLevelEvaluation(
            status=status,
            parent_at_htf_zone=None,
            symbol=parent.symbol if parent is not None else "UNKNOWN",
            parent_timeframe=parent.timeframe if parent is not None else "UNKNOWN",
            parent_open_time=parent.open_time if parent is not None else now,
            tolerance=None,
            tolerance_atr_multiplier=None,
            minimum_tolerance_pips=None,
            matched_levels=(),
            missing_sources=missing_sources,
            configuration_snapshot_id=self._config.snapshot_id,
            explanation=explanation,
        )
