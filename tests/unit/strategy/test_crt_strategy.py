import dataclasses
from core.models.trade_candidate import StrategyOutcome, Direction

def test_bullish_evaluation(strategy, bullish_detection, strategy_config):
    profile = strategy_config.get_profile("swing_trader")
    decision = strategy.evaluate(bullish_detection, profile, strategy_config)
    
    assert decision.outcome == StrategyOutcome.TRADE_CANDIDATE
    cand = decision.candidate
    assert cand is not None
    assert cand.direction == Direction.BUY
    assert cand.entry_model.entry_zone_low == bullish_detection.metadata.crt_low
    assert cand.entry_model.entry_zone_high == bullish_detection.metadata.sweep_candle.close
    assert cand.target_model.midpoint_target == bullish_detection.metadata.midpoint_target

def test_bearish_evaluation(strategy, bearish_detection, strategy_config):
    profile = strategy_config.get_profile("swing_trader")
    decision = strategy.evaluate(bearish_detection, profile, strategy_config)
    
    assert decision.outcome == StrategyOutcome.TRADE_CANDIDATE
    cand = decision.candidate
    assert cand is not None
    assert cand.direction == Direction.SELL
    assert cand.entry_model.entry_zone_high == bearish_detection.metadata.crt_high
    assert cand.entry_model.entry_zone_low == bearish_detection.metadata.sweep_candle.close

def test_not_detected_gate(strategy, bullish_detection, strategy_config):
    not_detected = dataclasses.replace(bullish_detection, detected=False, metadata=None)
    profile = strategy_config.get_profile("swing_trader")
    decision = strategy.evaluate(not_detected, profile, strategy_config)
    
    assert decision.outcome == StrategyOutcome.NO_TRADE
    assert "No pattern detected" in decision.reason

def test_unknown_classification(strategy, bullish_detection, strategy_config):
    unknown_class = dataclasses.replace(bullish_detection, classification="UNKNOWN")
    profile = strategy_config.get_profile("swing_trader")
    decision = strategy.evaluate(unknown_class, profile, strategy_config)
    
    assert decision.outcome == StrategyOutcome.UNKNOWN

def test_missing_metadata(strategy, bullish_detection, strategy_config):
    no_metadata = dataclasses.replace(bullish_detection, metadata=None)
    profile = strategy_config.get_profile("swing_trader")
    decision = strategy.evaluate(no_metadata, profile, strategy_config)
    
    assert decision.outcome == StrategyOutcome.UNKNOWN

def test_timeframe_gate(strategy, bullish_detection, strategy_config):
    # scalper doesn't trade H4
    profile = strategy_config.get_profile("scalper")
    decision = strategy.evaluate(bullish_detection, profile, strategy_config)
    
    assert decision.outcome == StrategyOutcome.NO_TRADE
    assert "Timeframe" in decision.reason

def test_session_alignment_gate(strategy, bullish_detection, strategy_config):
    meta_no_session = dataclasses.replace(bullish_detection.metadata, session_alignment=False)
    no_session_det = dataclasses.replace(bullish_detection, metadata=meta_no_session)
    profile = strategy_config.get_profile("day_trader") # day_trader requires session alignment
    
    decision = strategy.evaluate(no_session_det, profile, strategy_config)
    assert decision.outcome == StrategyOutcome.NO_TRADE

def test_htf_trend_alignment_gate(strategy, bullish_detection, strategy_config):
    meta_no_trend = dataclasses.replace(bullish_detection.metadata, htf_trend_alignment=False)
    no_trend_det = dataclasses.replace(bullish_detection, metadata=meta_no_trend)
    # create a mock profile that requires it
    profile = dataclasses.replace(strategy_config.get_profile("swing_trader"), require_htf_trend_alignment=True)
    
    decision = strategy.evaluate(no_trend_det, profile, strategy_config)
    assert decision.outcome == StrategyOutcome.NO_TRADE
