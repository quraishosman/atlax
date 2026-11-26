from datetime import datetime, timedelta, timezone
from decimal import Decimal

from core.market_data.key_level_evaluator import KeyLevelEvaluator, simple_atr_for_parent
from core.models.candle import Candle
from core.settings.key_level_config import KeyLevelConfig


UTC = timezone.utc


def _candle(
    symbol: str,
    timeframe: str,
    opened: datetime,
    high: str,
    low: str,
    close: str | None = None,
    *,
    duration_hours: int,
    is_closed: bool = True,
) -> Candle:
    close_price = close if close is not None else high
    return Candle(
        symbol=symbol,
        timeframe=timeframe,
        open_time=opened,
        close_time=opened + timedelta(hours=duration_hours),
        open=Decimal(low),
        high=Decimal(high),
        low=Decimal(low),
        close=Decimal(close_price),
        is_closed=is_closed,
    )


def _config(**overrides) -> KeyLevelConfig:
    values = {
        "enabled": True,
        "shadow_mode": True,
        "version": "0.1.0",
        "tolerance_atr_multiplier": Decimal("0.10"),
        "minimum_tolerance_pips": Decimal("5"),
        "swing_left_bars": 2,
        "swing_right_bars": 2,
        "maximum_swing_levels": 5,
        "source_timeframes": ("D1", "W1", "H4"),
    }
    values.update(overrides)
    return KeyLevelConfig(**values)


def _evaluate(
    *,
    parent: Candle,
    daily: list[Candle],
    weekly: list[Candle],
    h4: list[Candle],
    d1: list[Candle] | None = None,
):
    evaluator = KeyLevelEvaluator(_config())
    return evaluator.evaluate(
        parent=parent,
        parent_atr=Decimal("0.0020"),
        pip_size=Decimal("0.0001"),
        daily_candles=daily,
        weekly_candles=weekly,
        h4_candles=h4,
        d1_candles=d1 if d1 is not None else daily,
    )


def test_previous_period_uses_only_completed_period_before_parent_open():
    parent_open = datetime(2026, 8, 10, 10, tzinfo=UTC)
    parent = _candle(
        "EURUSD", "H1", parent_open, "1.1010", "1.0990", "1.1000", duration_hours=1
    )
    previous_day = _candle(
        "EURUSD", "D1", datetime(2026, 8, 9, tzinfo=UTC), "1.2000", "1.1900", duration_hours=24
    )
    current_day = _candle(
        "EURUSD", "D1", datetime(2026, 8, 10, tzinfo=UTC), "1.1005", "1.0985", duration_hours=24
    )
    previous_week = _candle(
        "EURUSD", "W1", datetime(2026, 7, 27, tzinfo=UTC), "1.3000", "1.2800", duration_hours=168
    )
    h4_history = [
        _candle("EURUSD", "H4", datetime(2026, 8, 8, tzinfo=UTC), "1.2500", "1.2400", duration_hours=4)
    ]

    result = _evaluate(
        parent=parent,
        daily=[previous_day, current_day],
        weekly=[previous_week],
        h4=h4_history,
    )

    assert result.status == "VALID"
    assert result.parent_at_htf_zone is False
    assert all(level.price != Decimal("1.1005") for level in result.matched_levels)


def test_parent_matches_previous_day_level_using_minimum_pip_tolerance():
    parent_open = datetime(2026, 8, 10, 10, tzinfo=UTC)
    parent = _candle(
        "EURUSD", "H1", parent_open, "1.1010", "1.0990", "1.1000", duration_hours=1
    )
    previous_day = _candle(
        "EURUSD", "D1", datetime(2026, 8, 9, tzinfo=UTC), "1.1014", "1.0900", duration_hours=24
    )
    previous_week = _candle(
        "EURUSD", "W1", datetime(2026, 7, 27, tzinfo=UTC), "1.3000", "1.2800", duration_hours=168
    )
    h4_history = [
        _candle("EURUSD", "H4", datetime(2026, 8, 8, tzinfo=UTC), "1.2500", "1.2400", duration_hours=4)
    ]

    result = _evaluate(
        parent=parent,
        daily=[previous_day],
        weekly=[previous_week],
        h4=h4_history,
    )

    assert result.parent_at_htf_zone is True
    match = next(level for level in result.matched_levels if level.level_type == "PREVIOUS_DAY_HIGH")
    assert match.price == Decimal("1.1014")
    assert result.tolerance == Decimal("0.0005")


def test_swing_requires_both_confirmed_right_side_bars():
    parent_open = datetime(2026, 8, 10, 16, tzinfo=UTC)
    parent = _candle(
        "EURUSD", "H1", parent_open, "1.1052", "1.1048", "1.1050", duration_hours=1
    )
    daily = [
        _candle("EURUSD", "D1", datetime(2026, 8, 9, tzinfo=UTC), "1.3000", "1.2800", duration_hours=24)
    ]
    weekly = [
        _candle("EURUSD", "W1", datetime(2026, 7, 27, tzinfo=UTC), "1.4000", "1.3800", duration_hours=168)
    ]
    h4 = [
        _candle("EURUSD", "H4", datetime(2026, 8, 10, hour, tzinfo=UTC), high, "1.0900", duration_hours=4)
        for hour, high in [(0, "1.1000"), (4, "1.1010"), (8, "1.1050"), (12, "1.1010"), (16, "1.1010")]
    ]

    before_second_right_close = _evaluate(
        parent=parent,
        daily=daily,
        weekly=weekly,
        h4=h4,
    )
    assert not any(level.level_type == "H4_SWING_HIGH" for level in before_second_right_close.matched_levels)

    confirmed_parent = _candle(
        "EURUSD", "H1", datetime(2026, 8, 10, 20, tzinfo=UTC), "1.1052", "1.1048", "1.1050", duration_hours=1
    )
    confirmed = _evaluate(
        parent=confirmed_parent,
        daily=daily,
        weekly=weekly,
        h4=h4,
    )
    assert any(level.level_type == "H4_SWING_HIGH" for level in confirmed.matched_levels)


def test_missing_history_returns_unknown_and_never_a_positive_match():
    parent = _candle(
        "EURUSD", "H1", datetime(2026, 8, 10, 10, tzinfo=UTC), "1.1010", "1.0990", duration_hours=1
    )
    result = _evaluate(parent=parent, daily=[], weekly=[], h4=[])

    assert result.status == "UNKNOWN"
    assert result.parent_at_htf_zone is None
    assert set(result.missing_sources) == {"D1", "W1", "H4"}


def test_parent_atr_ends_at_parent_and_ignores_later_candles():
    start = datetime(2026, 8, 10, tzinfo=UTC)
    candles = [
        _candle("EURUSD", "H1", start + timedelta(hours=index), "1.1010", "1.1000", duration_hours=1)
        for index in range(4)
    ]
    parent = candles[1]
    later = _candle("EURUSD", "H1", start + timedelta(hours=4), "9.0000", "1.0000", duration_hours=1)

    as_of_parent = simple_atr_for_parent(candles + [later], parent, period=1)
    as_of_parent_without_later = simple_atr_for_parent(candles, parent, period=1)

    assert as_of_parent == as_of_parent_without_later == Decimal("0.0010")
