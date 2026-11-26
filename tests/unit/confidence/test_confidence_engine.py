import dataclasses
from core.models.trade_candidate import Direction

def test_perfect_score(confidence_engine, base_candidate):
    score = confidence_engine.score(base_candidate)
    assert score.final_score == 100.0
    assert len(score.missing_factors) == 0

def test_all_unknown_score(confidence_engine, base_candidate):
    unknown_inputs = {k: None for k in base_candidate.confidence_inputs}
    unknown_inputs["classification"] = "bullish_crt"
    unknown_inputs["swept_level"] = "CRL"
    
    zero_cand = dataclasses.replace(base_candidate, confidence_inputs=unknown_inputs)
    score = confidence_engine.score(zero_cand)
    
    assert score.final_score == 0.0
    # volume_profile_confluence is only scored when its weight > 0 (see confidence_engine.py).
    # With the default weight=0 the factor is not included, so 8 factors are missing.
    assert len(score.missing_factors) == 8


def test_missing_factors_are_rescaled(confidence_engine, base_candidate):
    # If 5 factors are unknown, the score should rescale and still be 100 if the remaining 4 are perfect
    partial_inputs = dict(base_candidate.confidence_inputs)
    partial_inputs["parent_at_htf_zone"] = None
    partial_inputs["htf_trend_alignment"] = None
    partial_inputs["fvg_present_after_sweep"] = None
    partial_inputs["msb_on_ltf"] = None
    partial_inputs["volume_profile_confluence"] = None

    partial_cand = dataclasses.replace(base_candidate, confidence_inputs=partial_inputs)
    score = confidence_engine.score(partial_cand)

    assert score.final_score == 100.0
    # volume_profile_confluence is gated on its weight > 0.
    # With weight=0 it is never scored, so it is not counted as a missing factor.
    # Only 4 of the 5 set-to-None keys are actual scored factors.
    assert len(score.missing_factors) == 4
    assert "htf_zone" in score.missing_factors


def test_binary_false_scores_zero(confidence_engine, base_candidate, confidence_config):
    # Set session_alignment to False (which is a binary factor)
    inputs = dict(base_candidate.confidence_inputs)
    inputs["session_alignment"] = False
    cand = dataclasses.replace(base_candidate, confidence_inputs=inputs)
    
    score = confidence_engine.score(cand)
    # the score should be 100 - session_alignment_weight (15.0) = 85.0
    assert score.final_score == 85.0
    
    sa = next(f for f in score.factor_breakdown if f.factor_name == "session_alignment")
    assert sa.score == 0.0

def test_continuous_overextended(confidence_engine, base_candidate, confidence_config):
    # Set sweep_distance_pips extremely high to trigger decay
    inputs = dict(base_candidate.confidence_inputs)
    inputs["sweep_distance_pips"] = 7000.0
    cand = dataclasses.replace(base_candidate, confidence_inputs=inputs)
    
    score = confidence_engine.score(cand)
    
    sd = next(f for f in score.factor_breakdown if f.factor_name == "sweep_distance")
    assert sd.score < confidence_config.sweep_distance_weight
    assert sd.score == 0.0 # 7000 is way above ideal_max (30) so score decays to 0

def test_bearish_close_location_direction_awareness(confidence_engine, base_candidate, confidence_config):
    inputs = dict(base_candidate.confidence_inputs)
    inputs["classification"] = "bearish_crt"
    inputs["swept_level"] = "CRH"
    inputs["close_location_ratio"] = 0.30 # <= 0.60 ideal for bearish = full score
    
    bearish_cand = dataclasses.replace(base_candidate, direction=Direction.SELL, confidence_inputs=inputs)
    score = confidence_engine.score(bearish_cand)
    
    cl = next(f for f in score.factor_breakdown if f.factor_name == "close_location")
    assert cl.score == confidence_config.close_location_weight
