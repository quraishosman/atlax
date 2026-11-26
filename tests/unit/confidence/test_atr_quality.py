from datetime import datetime, timezone
from decimal import Decimal

import pytest
import yaml

from core.confidence.atr_quality import (
    ATRPlanEntitlement,
    ATRQualityConfig,
    evaluate_atr_quality,
)
from core.models.candle import Candle
from core.models.detector_output import CRTMetadata, DetectorOutput
from core.settings.atr_quality_config import load_atr_quality_config


@pytest.fixture
def quality_config() -> ATRQualityConfig:
    return ATRQualityConfig(
        enabled=True,
        version="0.1.0",
        method="SIMPLE_TR_MEAN",
        atr_period=14,
        parent_high_min=Decimal("0.80"),
        parent_a_min=Decimal("0.60"),
        parent_b_min=Decimal("0.45"),
        sweep_high_min=Decimal("0.25"),
        sweep_a_min=Decimal("0.20"),
        sweep_b_min=Decimal("0.15"),
        minimum_premium_grade="A",
        missing_data_policy="UNKNOWN",
        advanced_regime_filter_enabled=False,
        max_parent_atr_ratio=None,
        max_sweep_atr_ratio=None,
        volatility_regime_min_ratio=None,
        volatility_regime_max_ratio=None,
        default_plan="standard",
        plans=(
            ATRPlanEntitlement("standard", False, None),
            ATRPlanEntitlement("premium", True, "A"),
        ),
    )


def _detection(parent_ratio: str, sweep_ratio: str, *, bearish: bool = False) -> DetectorOutput:
    now = datetime(2026, 8, 13, 12, 0, tzinfo=timezone.utc)
    atr = Decimal("1")
    parent_low = Decimal("100")
    parent_high = parent_low + Decimal(parent_ratio)

    if bearish:
        sweep_high = parent_high + Decimal(sweep_ratio)
        sweep_low = parent_low + Decimal("0.10")
        sweep_close = parent_high - Decimal("0.10")
        sweep_open = parent_high - Decimal("0.05")
        classification = "bearish_crt"
        swept_level = "CRH"
        opposite = parent_low
    else:
        sweep_low = parent_low - Decimal(sweep_ratio)
        sweep_high = parent_high - Decimal("0.10")
        sweep_close = parent_low + Decimal("0.10")
        sweep_open = parent_low + Decimal("0.05")
        classification = "bullish_crt"
        swept_level = "CRL"
        opposite = parent_high

    parent = Candle(
        "EURUSD", "H1", now, now,
        parent_low + Decimal("0.20"), parent_high, parent_low, parent_high - Decimal("0.20"), True,
    )
    sweep = Candle(
        "EURUSD", "H1", now, now,
        sweep_open, sweep_high, sweep_low, sweep_close, True,
    )
    metadata = CRTMetadata(
        parent_candle=parent,
        sweep_candle=sweep,
        confirmation_candle=None,
        swept_level=swept_level,
        crt_high=parent_high,
        crt_low=parent_low,
        midpoint_target=(parent_high + parent_low) / 2,
        opposite_extreme_target=opposite,
        sweep_distance_pips=None,
        close_location_ratio=None,
        sweep_wick_ratio=None,
        parent_at_htf_zone=None,
        session_alignment=None,
        htf_trend_alignment=None,
        fvg_present_after_sweep=None,
        msb_on_ltf=None,
    )
    return DetectorOutput(
        detector="CRTDetector",
        symbol="EURUSD",
        timeframe="H1",
        detected=True,
        timestamp=now,
        classification=classification,
        reason="test detection",
        invalidation_reason=None,
        metadata=metadata,
    )


@pytest.mark.parametrize(
    ("parent_ratio", "sweep_ratio", "expected"),
    [
        ("0.90", "0.30", "A+"),
        ("0.90", "0.18", "A"),
        ("0.55", "0.30", "A"),
        ("0.70", "0.22", "A"),
        ("0.70", "0.18", "B"),
        ("0.55", "0.18", "B"),
        ("0.70", "0.12", "C"),
        ("0.40", "0.30", "C"),
        ("0.45", "0.15", "B"),
        ("0.60", "0.20", "A"),
        ("0.80", "0.25", "A+"),
    ],
)
def test_quality_matrix_has_expected_grade(
    quality_config, parent_ratio, sweep_ratio, expected
):
    result = evaluate_atr_quality(
        _detection(parent_ratio, sweep_ratio), Decimal("1"), quality_config
    )

    assert result.status == "VALID"
    assert result.grade == expected
    assert result.parent_atr_ratio == Decimal(parent_ratio)
    assert result.sweep_atr_ratio == Decimal(sweep_ratio)


def test_bearish_sweep_depth_is_symmetric(quality_config):
    result = evaluate_atr_quality(
        _detection("0.70", "0.22", bearish=True), Decimal("1"), quality_config
    )

    assert result.grade == "A"
    assert result.sweep_depth == Decimal("0.22")


def test_missing_atr_returns_unknown(quality_config):
    result = evaluate_atr_quality(_detection("0.90", "0.30"), None, quality_config)

    assert result.status == "UNKNOWN"
    assert result.grade is None


@pytest.mark.parametrize("atr", [Decimal("0"), Decimal("-1")])
def test_non_positive_atr_returns_unknown(quality_config, atr):
    result = evaluate_atr_quality(_detection("0.90", "0.30"), atr, quality_config)

    assert result.status == "UNKNOWN"
    assert result.grade is None


def test_non_detected_event_is_not_applicable(quality_config):
    detection = _detection("0.90", "0.30")
    non_detection = DetectorOutput(
        detector=detection.detector,
        symbol=detection.symbol,
        timeframe=detection.timeframe,
        detected=False,
        timestamp=detection.timestamp,
        classification="UNKNOWN",
        reason="no setup",
        invalidation_reason="not a setup",
        metadata=None,
    )

    result = evaluate_atr_quality(non_detection, Decimal("1"), quality_config)

    assert result.status == "NOT_APPLICABLE"
    assert result.grade is None


def test_threshold_order_is_validated():
    with pytest.raises(ValueError, match="Parent ATR thresholds"):
        ATRQualityConfig(
            enabled=True,
            version="0.1.0",
            method="SIMPLE_TR_MEAN",
            atr_period=14,
            parent_high_min=Decimal("0.60"),
            parent_a_min=Decimal("0.80"),
            parent_b_min=Decimal("0.45"),
            sweep_high_min=Decimal("0.25"),
            sweep_a_min=Decimal("0.20"),
            sweep_b_min=Decimal("0.15"),
            minimum_premium_grade="A",
            missing_data_policy="UNKNOWN",
            advanced_regime_filter_enabled=False,
            max_parent_atr_ratio=None,
            max_sweep_atr_ratio=None,
            volatility_regime_min_ratio=None,
            volatility_regime_max_ratio=None,
            default_plan="standard",
            plans=(
                ATRPlanEntitlement("standard", False, None),
                ATRPlanEntitlement("premium", True, "A"),
            ),
        )


def _base_overlay() -> dict:
    return {
        "enabled": False,
        "version": "0.1.0",
        "method": "SIMPLE_TR_MEAN",
        "atr_period": 14,
        "minimum_premium_grade": "A",
        "missing_data_policy": "UNKNOWN",
        "parent_a_plus_min_atr_ratio": 0.80,
        "parent_a_min_atr_ratio": 0.60,
        "parent_b_min_atr_ratio": 0.45,
        "sweep_a_plus_min_atr_ratio": 0.25,
        "sweep_a_min_atr_ratio": 0.20,
        "sweep_b_min_atr_ratio": 0.15,
        "advanced_regime_filter_enabled": False,
        "max_parent_atr_ratio": None,
        "max_sweep_atr_ratio": None,
        "volatility_regime_min_ratio": None,
        "volatility_regime_max_ratio": None,
        "default_plan": "standard",
        "plans": {
            "standard": {
                "atr_quality_filter": False,
                "minimum_grade": None,
            },
            "premium": {
                "atr_quality_filter": True,
                "minimum_grade": "A",
            },
        },
    }


def _write_overlay(tmp_path, overlay: dict):
    path = tmp_path / "atlax.yaml"
    path.write_text(
        yaml.safe_dump({"atr_quality_overlay": overlay}), encoding="utf-8"
    )
    return path


def test_loads_enforced_production_overlay():
    config = load_atr_quality_config("config/atlax.yaml")

    assert config.enabled is True
    assert config.shadow_mode is False
    assert config.method == "SIMPLE_TR_MEAN"
    assert config.atr_period == 14
    assert config.minimum_premium_grade == "A"
    assert config.parent_high_min == Decimal("0.80")
    assert config.sweep_b_min == Decimal("0.15")
    assert config.snapshot_id
    assert config.mode == "ENFORCE"


def test_shadow_mode_is_loaded_without_enforcing_delivery(tmp_path):
    overlay = _base_overlay()
    overlay["enabled"] = True
    overlay["shadow_mode"] = True

    config = load_atr_quality_config(_write_overlay(tmp_path, overlay))

    assert config.shadow_mode is True
    assert config.mode == "SHADOW"


def test_missing_overlay_section_fails_closed(tmp_path):
    path = tmp_path / "atlax.yaml"
    path.write_text("confidence: {}\n", encoding="utf-8")

    with pytest.raises(ValueError, match="atr_quality_overlay"):
        load_atr_quality_config(path)


def test_string_boolean_fails_closed(tmp_path):
    overlay = _base_overlay()
    overlay["enabled"] = "false"

    with pytest.raises(ValueError, match="enabled must be a boolean"):
        load_atr_quality_config(_write_overlay(tmp_path, overlay))


def test_advanced_regime_requires_both_bounds(tmp_path):
    overlay = _base_overlay()
    overlay["advanced_regime_filter_enabled"] = True

    with pytest.raises(ValueError, match="requires both regime bounds"):
        load_atr_quality_config(_write_overlay(tmp_path, overlay))


def test_unsupported_atr_method_fails_closed(tmp_path):
    overlay = _base_overlay()
    overlay["method"] = "WILDER"

    with pytest.raises(ValueError, match="Unsupported ATR quality method"):
        load_atr_quality_config(_write_overlay(tmp_path, overlay))


def test_complete_advanced_bounds_are_loaded(tmp_path):
    overlay = _base_overlay()
    overlay["advanced_regime_filter_enabled"] = True
    overlay["volatility_regime_min_ratio"] = 0.70
    overlay["volatility_regime_max_ratio"] = 1.50

    config = load_atr_quality_config(_write_overlay(tmp_path, overlay))

    assert config.advanced_regime_filter_enabled is True
    assert config.volatility_regime_min_ratio == Decimal("0.7")
    assert config.volatility_regime_max_ratio == Decimal("1.5")


def test_configuration_snapshot_changes_when_thresholds_change(tmp_path):
    first = load_atr_quality_config(_write_overlay(tmp_path, _base_overlay()))
    changed = _base_overlay()
    changed["parent_a_min_atr_ratio"] = 0.65
    second = load_atr_quality_config(_write_overlay(tmp_path, changed))

    assert first.snapshot_id != second.snapshot_id
