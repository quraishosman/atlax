from decimal import Decimal

import pytest

from core.settings.key_level_config import KeyLevelConfig, load_key_level_config


def test_project_key_level_config_is_live_after_shadow_approval():
    config = load_key_level_config("config/atlax.yaml")

    assert config.enabled is True
    assert config.shadow_mode is False
    assert config.swing_left_bars == 2
    assert config.swing_right_bars == 2
    assert config.snapshot_id


def test_shadow_mode_cannot_be_enabled_without_evaluator():
    with pytest.raises(ValueError):
        KeyLevelConfig(
            enabled=False,
            shadow_mode=True,
            version="0.1.0",
            tolerance_atr_multiplier=Decimal("0.10"),
            minimum_tolerance_pips=Decimal("5"),
            swing_left_bars=2,
            swing_right_bars=2,
            maximum_swing_levels=5,
            source_timeframes=("D1", "W1", "H4"),
        )
