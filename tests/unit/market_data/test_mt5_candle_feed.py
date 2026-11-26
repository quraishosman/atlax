from datetime import datetime, timedelta, timezone
from decimal import Decimal

from core.market_data.mt5_candle_feed import MT5CandleFeed
from core.models.candle import Candle, CandleSequence
from core.settings.market_data_config import WatchedInstrument


def test_atr_from_sequence_uses_closed_candles_without_mt5_fetch():
    start = datetime(2026, 9, 22, tzinfo=timezone.utc)

    def candle(index, high, low, close):
        opened = start + timedelta(minutes=index * 5)
        return Candle(
            symbol="EURUSD",
            timeframe="M5",
            open_time=opened,
            close_time=opened + timedelta(minutes=5),
            open=Decimal(str(close)),
            high=Decimal(str(high)),
            low=Decimal(str(low)),
            close=Decimal(str(close)),
            is_closed=True,
        )

    sequence = CandleSequence(
        candles=(
            candle(0, 11, 9, 10),
            candle(1, 13, 11, 12),
            candle(2, 15, 13, 14),
            candle(3, 18, 15, 17),
        ),
        symbol="EURUSD",
        timeframe="M5",
    )
    feed = MT5CandleFeed(WatchedInstrument("EURUSD", "M5", 50, 14, 0.0001))

    # The final two true ranges are 3 and 4, so ATR(2) is 3.5.
    assert feed.get_atr_from_sequence(sequence, period=2) == Decimal("3.5")
