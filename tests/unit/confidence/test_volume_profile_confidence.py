"""
Tests for Volume Profile confidence factor promotion (Phase 6).

Covers:
  - Config: VP weight accepted at 0.0 (disabled) and positive values
  - Config: Weight sum validation includes VP weight
  - Scoring: Factor excluded when weight is 0.0
  - Scoring: Factor scored as binary when weight > 0
  - Scoring: UNKNOWN (None) input excludes from denominator
  - Scoring: True input adds full weight
  - Scoring: False input adds 0

Authority:
    docs/40_VOLUME_PROFILE_CONFLUENCE_PLAN.md Phase 6
    docs/09_CONFIDENCE_ENGINE.md
    docs/decisions/ADR-017.md
"""

import pytest

from core.confidence.confidence_engine import ConfidenceEngine
from core.settings.confidence_config import ConfidenceConfig


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _config(vp_weight: float = 0.0, **overrides) -> ConfidenceConfig:
    """Build a ConfidenceConfig with configurable VP weight.

    When vp_weight > 0, we reduce htf_zone proportionally to keep sum = 100.
    """
    base = {
        "version": "test",
        "session_alignment_weight": 15.0,
        "sweep_distance_weight": 15.0,
        "close_location_weight": 20.0,
        "sweep_wick_ratio_weight": 10.0,
        "htf_zone_weight": 15.0 - vp_weight,  # redistribute from htf_zone
        "htf_trend_weight": 10.0,
        "fvg_weight": 5.0,
        "msb_ltf_weight": 10.0,
        "volume_profile_confluence_weight": vp_weight,
        "sweep_distance_ideal_min_pips": 3.0,
        "sweep_distance_ideal_max_pips": 30.0,
        "close_location_bull_ideal_min": 0.40,
        "close_location_bear_ideal_max": 0.60,
        "sweep_wick_ideal_min": 0.05,
        "sweep_wick_ideal_max": 0.50,
    }
    base.update(overrides)
    return ConfidenceConfig(**base)


def _candidate(vp_confluence=None, direction="BUY"):
    """Build a minimal TradeCandidate-like object with confidence_inputs."""
    from unittest.mock import MagicMock
    c = MagicMock()
    c.candidate_id = "test-cand"
    c.symbol = "EURUSD"
    c.timeframe = "H1"
    c.profile = "day_trader"
    c.direction = direction
    c.confidence_inputs = {
        "session_alignment": True,
        "sweep_distance_pips": 10.0,
        "close_location_ratio": 0.65,
        "sweep_wick_ratio": 0.20,
        "parent_at_htf_zone": True,
        "htf_trend_alignment": True,
        "fvg_present_after_sweep": True,
        "msb_on_ltf": True,
    }
    if vp_confluence is not None:
        c.confidence_inputs["volume_profile_confluence"] = vp_confluence
    return c


# ---------------------------------------------------------------------------
# Config tests
# ---------------------------------------------------------------------------


class TestConfidenceConfigVPWeight:
    def test_default_weight_zero_accepted(self):
        cfg = _config(vp_weight=0.0)
        assert cfg.volume_profile_confluence_weight == 0.0

    def test_positive_weight_accepted_when_sum_is_100(self):
        cfg = _config(vp_weight=5.0)
        assert cfg.volume_profile_confluence_weight == 5.0

    def test_weight_sum_validation_fails_if_total_exceeds_100(self):
        with pytest.raises(ValueError, match="must sum to 100"):
            _config(vp_weight=5.0, htf_zone_weight=15.0)  # 105 total

    def test_snapshot_id_includes_vp_weight(self):
        cfg_a = _config(vp_weight=0.0)
        cfg_b = _config(vp_weight=5.0)
        assert cfg_a.snapshot_id != cfg_b.snapshot_id


# ---------------------------------------------------------------------------
# Scoring tests
# ---------------------------------------------------------------------------


class TestConfidenceEngineVPFactor:
    def test_factor_excluded_when_weight_is_zero(self):
        """With weight=0, VP factor should not appear in breakdown."""
        cfg = _config(vp_weight=0.0)
        engine = ConfidenceEngine(cfg)
        candidate = _candidate(vp_confluence=True)

        score = engine.score(candidate)

        factor_names = [f.factor_name for f in score.factor_breakdown]
        assert "volume_profile_confluence" not in factor_names

    def test_factor_present_when_weight_positive(self):
        """With weight>0, VP factor must appear in breakdown."""
        cfg = _config(vp_weight=5.0)
        engine = ConfidenceEngine(cfg)
        candidate = _candidate(vp_confluence=True)

        score = engine.score(candidate)

        factor_names = [f.factor_name for f in score.factor_breakdown]
        assert "volume_profile_confluence" in factor_names

    def test_true_value_scores_full_weight(self):
        cfg = _config(vp_weight=5.0)
        engine = ConfidenceEngine(cfg)
        candidate = _candidate(vp_confluence=True)

        score = engine.score(candidate)

        vp_factor = next(
            f for f in score.factor_breakdown
            if f.factor_name == "volume_profile_confluence"
        )
        assert vp_factor.score == 5.0
        assert vp_factor.max_score == 5.0
        assert not vp_factor.is_unknown

    def test_false_value_scores_zero(self):
        cfg = _config(vp_weight=5.0)
        engine = ConfidenceEngine(cfg)
        candidate = _candidate(vp_confluence=False)

        score = engine.score(candidate)

        vp_factor = next(
            f for f in score.factor_breakdown
            if f.factor_name == "volume_profile_confluence"
        )
        assert vp_factor.score == 0.0
        assert vp_factor.max_score == 5.0
        assert not vp_factor.is_unknown

    def test_none_value_is_unknown_excluded_from_denominator(self):
        cfg = _config(vp_weight=5.0)
        engine = ConfidenceEngine(cfg)
        candidate = _candidate(vp_confluence=None)

        score = engine.score(candidate)

        vp_factor = next(
            f for f in score.factor_breakdown
            if f.factor_name == "volume_profile_confluence"
        )
        assert vp_factor.is_unknown
        assert "volume_profile_confluence" in score.missing_factors

    def test_missing_input_key_treated_as_unknown(self):
        """When the key is completely absent from confidence_inputs, factor is UNKNOWN."""
        cfg = _config(vp_weight=5.0)
        engine = ConfidenceEngine(cfg)
        candidate = _candidate()  # No vp_confluence key at all

        score = engine.score(candidate)

        vp_factor = next(
            f for f in score.factor_breakdown
            if f.factor_name == "volume_profile_confluence"
        )
        assert vp_factor.is_unknown

    def test_score_unchanged_when_weight_zero(self):
        """Score must not change when VP weight is 0 regardless of input value."""
        cfg = _config(vp_weight=0.0)
        engine = ConfidenceEngine(cfg)

        score_without = engine.score(_candidate())
        score_with_true = engine.score(_candidate(vp_confluence=True))
        score_with_false = engine.score(_candidate(vp_confluence=False))

        assert score_without.final_score == score_with_true.final_score
        assert score_without.final_score == score_with_false.final_score

    def test_score_difference_with_vp_true_vs_false(self):
        """When weight>0, True should produce a higher score than False."""
        cfg = _config(vp_weight=5.0)
        engine = ConfidenceEngine(cfg)

        score_true = engine.score(_candidate(vp_confluence=True))
        score_false = engine.score(_candidate(vp_confluence=False))

        assert score_true.final_score > score_false.final_score
