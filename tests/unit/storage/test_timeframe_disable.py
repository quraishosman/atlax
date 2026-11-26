"""Regression tests for the operational M1 timeframe disablement."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from core.alerts.subscriber_db import STYLE_TIMEFRAMES, SubscriberDB


class TimeframeDisableTests(unittest.TestCase):
    def test_subscriber_routes_exclude_retired_timeframe(self) -> None:
        self.assertEqual(STYLE_TIMEFRAMES["scalper"], ["M5"])
        self.assertEqual(STYLE_TIMEFRAMES["all_styles"], ["M5", "M15", "H1", "H4", "D1"])

    def test_legacy_subscriber_record_is_normalized_on_load(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            codes_path = root / "invite_codes.json"
            subscribers_path = root / "subscribers.json"
            codes_path.write_text("{}", encoding="utf-8")
            subscribers_path.write_text(json.dumps({
                "123": {
                    "chat_id": "123",
                    "style": "scalper",
                    "timeframes": ["M1", "M5"],
                    "active": True,
                }
            }), encoding="utf-8")

            database = SubscriberDB(str(codes_path), str(subscribers_path))

            self.assertEqual(database.get_subscriber("123")["timeframes"], ["M5"])
            persisted = json.loads(subscribers_path.read_text(encoding="utf-8"))
            self.assertEqual(persisted["123"]["timeframes"], ["M5"])

    def test_runtime_market_configuration_contains_no_m1_instrument(self) -> None:
        try:
            from core.settings.market_data_config import load_market_data_config
        except ModuleNotFoundError as exc:
            if exc.name == "yaml":
                self.skipTest("PyYAML is not installed in this test environment")
            raise
        config = load_market_data_config("config/atlax.yaml")
        self.assertTrue(config.instruments)
        self.assertTrue(all(instrument.timeframe != "M1" for instrument in config.instruments))


if __name__ == "__main__":
    unittest.main()
