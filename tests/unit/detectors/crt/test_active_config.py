"""Acceptance tests for the active CRT scanner configuration."""

from decimal import Decimal

from core.detectors.crt.detector import CRTDetector
from core.settings.crt_config import load_crt_config

from .conftest import make_sequence


def test_active_config_accepts_two_candle_bullish_branch(bullish_parent, bullish_sweep):
    """The deployed optional-confirmation mode evaluates parent plus sweep."""
    config = load_crt_config("config/atlax.yaml")
    assert config.require_confirmation_candle is False
    assert config.max_sweep_retracement_pct == 0.55

    result = CRTDetector(config).detect(
        make_sequence(bullish_parent, bullish_sweep),
        atr=Decimal("4.0"),
    )

    assert result.detected is True
    assert result.classification == "bullish_crt"
    assert result.metadata.confirmation_candle is None


def test_active_config_accepts_two_candle_bearish_branch(bearish_parent, bearish_sweep):
    """The deployed optional-confirmation mode is directionally symmetric."""
    config = load_crt_config("config/atlax.yaml")
    assert config.require_confirmation_candle is False
    assert config.max_sweep_retracement_pct == 0.55

    result = CRTDetector(config).detect(
        make_sequence(bearish_parent, bearish_sweep),
        atr=Decimal("4.0"),
    )

    assert result.detected is True
    assert result.classification == "bearish_crt"
    assert result.metadata.confirmation_candle is None
