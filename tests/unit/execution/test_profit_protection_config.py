from pathlib import Path

import pytest

from core.settings.profit_protection_config import load_profit_protection_config


def _write_config(tmp_path: Path, section: str) -> Path:
    path = tmp_path / "atlax.yaml"
    path.write_text(section, encoding="utf-8")
    return path


def test_loads_shadow_profit_protection_config(tmp_path):
    path = _write_config(
        tmp_path,
        """
profit_protection:
  enabled: true
  shadow_mode: true
  activation_r: 0.25
  minimum_lock_r: 0.05
  maximum_giveback_r: 0.20
  confirmation_bars: 2
  momentum_loss_enabled: false
  momentum_shadow_mode: true
  momentum_source_timeframe: H1
  minimum_current_profit_r: 0.05
  momentum_action: TIGHTEN_STOP
  close_remaining_on_momentum_loss: false
  include_transaction_cost_buffer: true
  trailing_stop_enabled: true
  trailing_activation_r: 0.25
  trailing_distance_r: 0.20
  trailing_step_r: 0.05
  target_proximity_fraction: 0.80
""",
    )

    config = load_profit_protection_config(path)

    assert config.enabled is True
    assert config.shadow_mode is True
    assert config.activation_r == 0.25
    assert config.confirmation_bars == 2
    assert config.target_proximity_fraction == 0.80


def test_rejects_non_boolean_shadow_mode(tmp_path):
    path = _write_config(
        tmp_path,
        """
profit_protection:
  enabled: true
  shadow_mode: "true"
  activation_r: 0.25
  minimum_lock_r: 0.05
  maximum_giveback_r: 0.20
  confirmation_bars: 2
  momentum_loss_enabled: false
  momentum_shadow_mode: true
  momentum_source_timeframe: H1
  minimum_current_profit_r: 0.05
  momentum_action: TIGHTEN_STOP
  close_remaining_on_momentum_loss: false
  include_transaction_cost_buffer: true
  trailing_stop_enabled: true
  trailing_activation_r: 0.25
  trailing_distance_r: 0.20
  trailing_step_r: 0.05
  target_proximity_fraction: 0.80
""",
    )

    with pytest.raises(ValueError, match="shadow_mode must be a boolean"):
        load_profit_protection_config(path)
