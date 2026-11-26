from datetime import datetime, timezone

import pytest

from core.settings.market_data_config import MarketScheduleConfig, load_market_data_config


def test_project_market_schedule_closes_weekends():
    config = load_market_data_config("config/atlax.yaml")

    assert config.schedule.timezone == "UTC"
    assert config.schedule.trading_weekdays == (0, 1, 2, 3, 4)
    assert config.schedule.is_open(datetime(2026, 8, 14, 12, tzinfo=timezone.utc)) is True
    assert config.schedule.is_open(datetime(2026, 8, 15, 12, tzinfo=timezone.utc)) is False
    assert config.schedule.is_open(datetime(2026, 8, 16, 12, tzinfo=timezone.utc)) is False


def test_market_schedule_rejects_invalid_timezone():
    with pytest.raises(ValueError, match="IANA timezone"):
        MarketScheduleConfig(timezone="Not/A_Timezone")


def test_market_schedule_rejects_invalid_weekday():
    with pytest.raises(ValueError, match="0 \\(Monday\\) to 6 \\(Sunday\\)"):
        MarketScheduleConfig(trading_weekdays=(0, 7))
