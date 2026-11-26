"""
Tests for VolumeProfileConfig settings loader.

Verifies that:
  - Valid YAML produces a correct, immutable VolumeProfileConfig.
  - Each invalid field is rejected fail-closed with a clear ValueError.
  - Missing section returns safe defaults (disabled, shadow_mode=True).
  - The confidence factor gate enforces Phase 6 approval requirements.

Authority:
    docs/40_VOLUME_PROFILE_CONFLUENCE_PLAN.md §8 (Configuration Schema)
    docs/13_CONFIGURATION.md (Settings Engine specification)
    docs/decisions/ADR-017.md
"""

import textwrap
import pytest

from core.settings.volume_profile_config import VolumeProfileConfig, load_volume_profile_config


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_yaml(tmp_path, volume_profile_section: str) -> str:
    """Write a minimal YAML file with the given volume_profile block and return its path."""
    lines = ["volume_profile:"]
    for line in textwrap.dedent(volume_profile_section).splitlines():
        if line.strip():
            lines.append(f"  {line}")
    content = "\n".join(lines) + "\n"
    p = tmp_path / "atlax.yaml"
    p.write_text(content, encoding="utf-8")
    return str(p)


def _write_full_yaml(tmp_path, extra_keys: str = "") -> str:
    """Write a complete valid YAML config and return its path."""
    return _write_yaml(tmp_path, f"""
        enabled: false
        shadow_mode: true
        version: "0.1.0"
        volume_source: MT5_TICK_VOLUME
        real_volume_fallback: true
        profile_periods:
          - PREVIOUS_DAY
          - PREVIOUS_WEEK
        source_timeframe_by_instrument_class:
          forex_non_jpy: M15
          forex_jpy: M15
          gold: M5
        value_area_percent: 0.70
        bin_size_ticks_by_instrument_class:
          forex_non_jpy: 10
          forex_jpy: 10
          gold: 20
        allocation_method: RANGE_UNIFORM
        minimum_source_bars: 50
        minimum_valid_volume_bars: 40
        use_current_period: false
        confidence_factor_enabled: false
        confidence_factor_weight: 0.0
        require_for_premium_confluence: false
        {extra_keys}
    """)


# ---------------------------------------------------------------------------
# Valid configuration
# ---------------------------------------------------------------------------


class TestValidVolumeProfileConfig:
    def test_default_construction_succeeds(self):
        cfg = VolumeProfileConfig()
        assert cfg.enabled is False
        assert cfg.shadow_mode is True
        assert cfg.volume_source == "MT5_TICK_VOLUME"
        assert cfg.value_area_percent == 0.70
        assert cfg.allocation_method == "RANGE_UNIFORM"
        assert cfg.use_current_period is False
        assert cfg.confidence_factor_enabled is False

    def test_load_from_valid_yaml(self, tmp_path):
        path = _write_full_yaml(tmp_path)
        cfg = load_volume_profile_config(path)
        assert cfg.enabled is False
        assert cfg.shadow_mode is True
        assert cfg.volume_source == "MT5_TICK_VOLUME"
        assert cfg.value_area_percent == pytest.approx(0.70)
        assert cfg.profile_periods == ("PREVIOUS_DAY", "PREVIOUS_WEEK")
        assert cfg.source_timeframe_by_instrument_class["forex_non_jpy"] == "M15"
        assert cfg.source_timeframe_by_instrument_class["gold"] == "M5"
        assert cfg.bin_size_ticks_by_instrument_class["forex_non_jpy"] == 10
        assert cfg.bin_size_ticks_by_instrument_class["gold"] == 20

    def test_missing_section_returns_safe_defaults(self, tmp_path):
        p = tmp_path / "atlax.yaml"
        p.write_text("market_data:\n  enabled: true\n", encoding="utf-8")
        cfg = load_volume_profile_config(str(p))
        assert cfg.enabled is False
        assert cfg.shadow_mode is True

    def test_frozen_immutability(self):
        from dataclasses import FrozenInstanceError
        cfg = VolumeProfileConfig()
        with pytest.raises(FrozenInstanceError):
            cfg.enabled = True  # type: ignore[misc]

    def test_real_volume_source_accepted(self):
        cfg = VolumeProfileConfig(volume_source="REAL_VOLUME")
        assert cfg.volume_source == "REAL_VOLUME"

    def test_only_previous_day_period_is_valid(self):
        cfg = VolumeProfileConfig(profile_periods=("PREVIOUS_DAY",))
        assert cfg.profile_periods == ("PREVIOUS_DAY",)

    def test_confidence_factor_enabled_with_nonzero_weight(self):
        cfg = VolumeProfileConfig(
            confidence_factor_enabled=True,
            confidence_factor_weight=5.0,
        )
        assert cfg.confidence_factor_enabled is True
        assert cfg.confidence_factor_weight == 5.0

    def test_symbol_level_timeframe_override_accepted(self):
        cfg = VolumeProfileConfig(
            source_timeframe_symbol_overrides={"XAUUSD": "M5"}
        )
        assert cfg.source_timeframe_symbol_overrides["XAUUSD"] == "M5"

    def test_symbol_level_bin_size_override_accepted(self):
        cfg = VolumeProfileConfig(
            bin_size_ticks_symbol_overrides={"USDJPY": 5}
        )
        assert cfg.bin_size_ticks_symbol_overrides["USDJPY"] == 5


# ---------------------------------------------------------------------------
# Invalid configuration — volume source
# ---------------------------------------------------------------------------


class TestVolumeSourceValidation:
    def test_unknown_source_rejected(self):
        with pytest.raises(ValueError, match="volume_source"):
            VolumeProfileConfig(volume_source="GLOBAL_VOLUME")

    def test_empty_source_rejected(self):
        with pytest.raises(ValueError, match="volume_source"):
            VolumeProfileConfig(volume_source="")


# ---------------------------------------------------------------------------
# Invalid configuration — profile periods
# ---------------------------------------------------------------------------


class TestProfilePeriodValidation:
    def test_empty_periods_rejected(self):
        with pytest.raises(ValueError, match="profile_periods"):
            VolumeProfileConfig(profile_periods=())

    def test_unknown_period_rejected(self):
        with pytest.raises(ValueError, match="profile_periods"):
            VolumeProfileConfig(profile_periods=("PREVIOUS_MONTH",))

    def test_current_day_period_rejected(self):
        with pytest.raises(ValueError, match="profile_periods"):
            VolumeProfileConfig(profile_periods=("CURRENT_DAY",))


# ---------------------------------------------------------------------------
# Invalid configuration — value area percent
# ---------------------------------------------------------------------------


class TestValueAreaPercentValidation:
    def test_zero_rejected(self):
        with pytest.raises(ValueError, match="value_area_percent"):
            VolumeProfileConfig(value_area_percent=0.0)

    def test_one_rejected(self):
        with pytest.raises(ValueError, match="value_area_percent"):
            VolumeProfileConfig(value_area_percent=1.0)

    def test_negative_rejected(self):
        with pytest.raises(ValueError, match="value_area_percent"):
            VolumeProfileConfig(value_area_percent=-0.5)

    def test_greater_than_one_rejected(self):
        with pytest.raises(ValueError, match="value_area_percent"):
            VolumeProfileConfig(value_area_percent=1.5)


# ---------------------------------------------------------------------------
# Invalid configuration — bin sizes
# ---------------------------------------------------------------------------


class TestBinSizeValidation:
    def test_zero_bin_size_rejected(self):
        bins = {"forex_non_jpy": 0, "forex_jpy": 10, "gold": 20}
        with pytest.raises(ValueError, match="bin_size_ticks_by_instrument_class"):
            VolumeProfileConfig(bin_size_ticks_by_instrument_class=bins)

    def test_negative_bin_size_rejected(self):
        bins = {"forex_non_jpy": 10, "forex_jpy": -1, "gold": 20}
        with pytest.raises(ValueError, match="bin_size_ticks_by_instrument_class"):
            VolumeProfileConfig(bin_size_ticks_by_instrument_class=bins)

    def test_missing_instrument_class_rejected(self):
        bins = {"forex_non_jpy": 10, "forex_jpy": 10}  # missing 'gold'
        with pytest.raises(ValueError, match="bin_size_ticks_by_instrument_class"):
            VolumeProfileConfig(bin_size_ticks_by_instrument_class=bins)

    def test_symbol_override_zero_bin_rejected(self):
        with pytest.raises(ValueError, match="bin_size_ticks_symbol_overrides"):
            VolumeProfileConfig(bin_size_ticks_symbol_overrides={"EURUSD": 0})


# ---------------------------------------------------------------------------
# Invalid configuration — source timeframes
# ---------------------------------------------------------------------------


class TestSourceTimeframeValidation:
    def test_h4_source_timeframe_rejected(self):
        tf_map = {"forex_non_jpy": "H4", "forex_jpy": "M15", "gold": "M5"}
        with pytest.raises(ValueError, match="source_timeframe_by_instrument_class"):
            VolumeProfileConfig(source_timeframe_by_instrument_class=tf_map)

    def test_missing_class_rejected(self):
        tf_map = {"forex_non_jpy": "M15", "forex_jpy": "M15"}  # missing 'gold'
        with pytest.raises(ValueError, match="source_timeframe_by_instrument_class"):
            VolumeProfileConfig(source_timeframe_by_instrument_class=tf_map)

    def test_symbol_override_invalid_timeframe_rejected(self):
        with pytest.raises(ValueError, match="source_timeframe_symbol_overrides"):
            VolumeProfileConfig(
                source_timeframe_symbol_overrides={"XAUUSD": "H1"}
            )


# ---------------------------------------------------------------------------
# Invalid configuration — use_current_period lookahead guard
# ---------------------------------------------------------------------------


class TestCurrentPeriodGuard:
    def test_use_current_period_true_rejected(self):
        with pytest.raises(ValueError, match="use_current_period"):
            VolumeProfileConfig(use_current_period=True)

    def test_use_current_period_false_accepted(self):
        cfg = VolumeProfileConfig(use_current_period=False)
        assert cfg.use_current_period is False


# ---------------------------------------------------------------------------
# Invalid configuration — confidence factor gate
# ---------------------------------------------------------------------------


class TestConfidenceFactorGate:
    def test_enabled_with_zero_weight_rejected(self):
        """Phase 6 gate: a non-zero approved weight is mandatory."""
        with pytest.raises(ValueError, match="confidence_factor_weight"):
            VolumeProfileConfig(
                confidence_factor_enabled=True,
                confidence_factor_weight=0.0,
            )

    def test_enabled_with_negative_weight_rejected(self):
        with pytest.raises(ValueError, match="confidence_factor_weight"):
            VolumeProfileConfig(
                confidence_factor_enabled=True,
                confidence_factor_weight=-1.0,
            )

    def test_disabled_with_zero_weight_accepted(self):
        cfg = VolumeProfileConfig(
            confidence_factor_enabled=False,
            confidence_factor_weight=0.0,
        )
        assert cfg.confidence_factor_enabled is False


# ---------------------------------------------------------------------------
# Invalid configuration — minimum bars contract
# ---------------------------------------------------------------------------


class TestMinimumBarsValidation:
    def test_zero_minimum_source_bars_rejected(self):
        with pytest.raises(ValueError, match="minimum_source_bars"):
            VolumeProfileConfig(minimum_source_bars=0)

    def test_zero_minimum_valid_volume_bars_rejected(self):
        with pytest.raises(ValueError, match="minimum_valid_volume_bars"):
            VolumeProfileConfig(minimum_valid_volume_bars=0)

    def test_valid_bars_exceeding_source_bars_rejected(self):
        with pytest.raises(ValueError, match="minimum_valid_volume_bars"):
            VolumeProfileConfig(minimum_source_bars=10, minimum_valid_volume_bars=20)


# ---------------------------------------------------------------------------
# Invalid configuration — allocation method
# ---------------------------------------------------------------------------


class TestAllocationMethodValidation:
    def test_unknown_method_rejected(self):
        with pytest.raises(ValueError, match="allocation_method"):
            VolumeProfileConfig(allocation_method="TICK_REPLAY")

    def test_range_uniform_accepted(self):
        cfg = VolumeProfileConfig(allocation_method="RANGE_UNIFORM")
        assert cfg.allocation_method == "RANGE_UNIFORM"
