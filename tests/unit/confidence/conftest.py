import pytest
from datetime import datetime, timezone
from decimal import Decimal

from core.models.candle import Candle
from core.models.trade_candidate import TradeCandidate, EntryModel, InvalidationModel, TargetModel, Direction
from core.settings.confidence_config import load_confidence_config, ConfidenceConfig
from core.confidence.confidence_engine import ConfidenceEngine

@pytest.fixture
def confidence_config() -> ConfidenceConfig:
    return load_confidence_config("config/atlax.yaml")

@pytest.fixture
def confidence_engine(confidence_config) -> ConfidenceEngine:
    return ConfidenceEngine(confidence_config)

@pytest.fixture
def base_candidate() -> TradeCandidate:
    now = datetime(2026, 7, 20, 12, 0, tzinfo=timezone.utc)
    inputs = {
        "classification": "bullish_crt",
        "swept_level": "CRL",
        "sweep_distance_pips": 15.0,
        "close_location_ratio": 0.70,
        "sweep_wick_ratio": 0.20,
        "session_alignment": True,
        "parent_at_htf_zone": True,
        "htf_trend_alignment": True,
        "fvg_present_after_sweep": True,
        "msb_on_ltf": True,
        "volume_profile_confluence": True,
    }
    return TradeCandidate(
        candidate_id="test-perf-001",
        strategy_name="CRTStrategy",
        source_detector_event_ids=("det-001",),
        symbol="EURUSD",
        timeframe="H4",
        profile="swing_trader",
        direction=Direction.BUY,
        entry_model=EntryModel(Decimal("100"), Decimal("98"), Decimal("99"), "test entry"),
        invalidation_model=InvalidationModel(Decimal("97"), "test SL"),
        target_model=TargetModel(Decimal("101"), Decimal("102"), "test TP"),
        confidence_inputs=inputs,
        explanation="test explanation",
        created_at=now
    )
