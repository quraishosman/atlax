"""Tests for the counterfactual CRT quality-tightening policy."""

import dataclasses
import logging
from decimal import Decimal

from core.detectors.crt.detector import CRTDetector
from core.detectors.crt.quality_tightening import evaluate_quality_tightening
from core.settings.crt_config import load_crt_config

from .conftest import make_sequence, make_candle


def test_active_shadow_policy_loads_proposed_thresholds():
    config = load_crt_config("config/atlax.yaml")
    policy = config.quality_tightening

    assert policy.enabled is True
    assert policy.shadow_mode is True
    assert policy.min_parent_range_atr_multiple == 0.75
    assert policy.min_parent_body_ratio == 0.38
    assert policy.max_sweep_retracement_pct == 0.55
    assert policy.min_sweep_depth_atr_multiple == 0.22


def test_shadow_policy_marks_weak_parent_and_sweep_without_rejecting(caplog):
    """Uses explicit WEAK candles (body ratio 0.20, sweep depth ratio 0.175) that fail thresholds.
    Runs in shadow_mode=True so detection is not suppressed but suppression is logged.
    """
    weak_parent = make_candle("100.00", "102.50", "98.50", "101.20", hour=0)
    weak_sweep  = make_candle("100.40", "100.60", "97.80", "100.50", hour=4)
    config = load_crt_config("config/atlax.yaml")
    shadow_config = dataclasses.replace(
        config,
        quality_tightening=dataclasses.replace(config.quality_tightening, shadow_mode=True)
    )
    detector = CRTDetector(shadow_config)
    caplog.set_level(logging.INFO, logger="atlax.detection")

    output = detector.detect(
        make_sequence(weak_parent, weak_sweep),
        atr=Decimal("4.0"),
    )

    assert output.detected is True
    assert "crt_quality_tightening_evaluated" in caplog.text
    assert '"mode": "shadow"' in caplog.text
    assert "parent_body_below_threshold" in caplog.text
    assert "sweep_depth_below_threshold" in caplog.text


def test_sweep_depth_formula_is_directionally_symmetric():
    """Uses explicit WEAK candles (sweep depth ratio 0.175 < 0.22) to verify
    the depth formula is symmetric for bullish and bearish setups.
    """
    weak_bull_parent = make_candle("100.00", "102.50", "98.50", "101.20", hour=0)
    weak_bull_sweep  = make_candle("100.40", "100.60", "97.80", "100.50", hour=4)
    weak_bear_parent = make_candle("151.90", "152.80", "149.20", "150.30", hour=0)
    weak_bear_sweep  = make_candle("151.00", "153.50", "150.90", "151.10", hour=4)
    config = load_crt_config("config/atlax.yaml").quality_tightening

    bullish = evaluate_quality_tightening(
        weak_bull_parent, weak_bull_sweep, Decimal("4.0"), "bullish", config
    )
    bearish = evaluate_quality_tightening(
        weak_bear_parent, weak_bear_sweep, Decimal("4.0"), "bearish", config
    )

    assert bullish.sweep_depth_atr_ratio == Decimal("0.175")
    assert bearish.sweep_depth_atr_ratio == Decimal("0.175")
    assert bullish.would_suppress is True
    assert bearish.would_suppress is True


def test_quality_policy_never_rejects_detector_output_globally():
    """Quality tightening is routed per subscriber, never globally."""
    weak_parent = make_candle("100.00", "102.50", "98.50", "101.20", hour=0)
    weak_sweep  = make_candle("100.40", "100.60", "97.80", "100.50", hour=4)
    config = load_crt_config("config/atlax.yaml")
    policy = dataclasses.replace(config.quality_tightening, shadow_mode=False)
    detector = CRTDetector(dataclasses.replace(config, quality_tightening=policy))

    output = detector.detect(
        make_sequence(weak_parent, weak_sweep),
        atr=Decimal("4.0"),
    )

    assert output.detected is True
    assert "quality tightening" not in output.reason.lower()


def test_missing_atr_is_unknown_and_never_suppresses(bullish_parent, bullish_sweep):
    config = load_crt_config("config/atlax.yaml").quality_tightening
    result = evaluate_quality_tightening(
        bullish_parent,
        bullish_sweep,
        None,
        "bullish",
        config,
    )

    assert result.status == "UNKNOWN"
    assert result.would_suppress is False
    assert result.reasons == ("missing_or_invalid_atr",)

