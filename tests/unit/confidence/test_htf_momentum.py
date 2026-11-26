from datetime import datetime, timedelta, timezone
from decimal import Decimal

from core.confidence.htf_momentum import HTFMomentumEvaluator, proposed_score
from core.models.candle import Candle, CandleSequence
from core.models.trade_candidate import Direction
from core.settings.htf_momentum_config import load_htf_momentum_config


def _candle(index, open_price, close_price, *, closed=True):
    start = datetime(2026, 8, 14, 8, 0, tzinfo=timezone.utc) + timedelta(hours=index)
    return Candle(
        symbol="EURUSD",
        timeframe="H1",
        open_time=start,
        close_time=start + timedelta(hours=1),
        open=Decimal(str(open_price)),
        high=Decimal(str(max(open_price, close_price) + 0.001)),
        low=Decimal(str(min(open_price, close_price) - 0.001)),
        close=Decimal(str(close_price)),
        is_closed=closed,
    )


def _sequence(*candles):
    return CandleSequence(candles=tuple(candles), symbol="EURUSD", timeframe="H1")


def test_one_closed_bullish_h1_candle_establishes_bias():
    config = load_htf_momentum_config("config/atlax.yaml")
    evaluator = HTFMomentumEvaluator(config)
    result = evaluator.evaluate(
        _sequence(_candle(0, 1.1000, 1.0990), _candle(1, 1.0990, 1.1010)),
        event_timestamp=datetime(2026, 8, 14, 10, 30, tzinfo=timezone.utc),
        direction=Direction.BUY,
    )

    assert result.status == "VALID"
    assert result.bias == "BULLISH"
    assert result.relationship == "ALIGNED"
    assert result.run_length == 1
    assert result.strong_run is False


def test_two_consecutive_candles_are_marked_as_strong_run_and_opposition_is_retained():
    config = load_htf_momentum_config("config/atlax.yaml")
    evaluator = HTFMomentumEvaluator(config)
    result = evaluator.evaluate(
        _sequence(
            _candle(0, 1.1000, 1.0990),
            _candle(1, 1.0990, 1.1010),
            _candle(2, 1.1010, 1.1030),
        ),
        event_timestamp=datetime(2026, 8, 14, 11, 30, tzinfo=timezone.utc),
        direction=Direction.SELL,
    )

    assert result.bias == "BULLISH"
    assert result.relationship == "OPPOSING"
    assert result.run_length == 2
    assert result.strong_run is True


def test_future_closed_candle_is_excluded_from_evaluation():
    config = load_htf_momentum_config("config/atlax.yaml")
    evaluator = HTFMomentumEvaluator(config)
    result = evaluator.evaluate(
        _sequence(_candle(0, 1.1000, 1.0990), _candle(1, 1.0990, 1.1010)),
        event_timestamp=datetime(2026, 8, 14, 9, 30, tzinfo=timezone.utc),
        direction=Direction.BUY,
    )

    assert result.bias == "BEARISH"
    assert result.run_length == 1
    assert result.relationship == "OPPOSING"


def test_neutral_latest_candle_is_unknown_not_a_guessed_sideways_bias():
    config = load_htf_momentum_config("config/atlax.yaml")
    evaluator = HTFMomentumEvaluator(config)
    result = evaluator.evaluate(
        _sequence(_candle(0, 1.1000, 1.0990), _candle(1, 1.0990, 1.0990)),
        event_timestamp=datetime(2026, 8, 14, 10, 30, tzinfo=timezone.utc),
        direction=Direction.BUY,
    )

    assert result.status == "UNKNOWN"
    assert result.relationship == "UNKNOWN"
    assert proposed_score(42.0, result, config) == 42.0
