import pytest
from datetime import datetime, timezone
from decimal import Decimal
import dataclasses

from core.models.candle import Candle
from core.models.detector_output import DetectorOutput, CRTMetadata
from core.settings.strategy_config import load_strategy_config, StrategyConfig
from core.strategy.crt_strategy import CRTStrategy

@pytest.fixture
def strategy_config() -> StrategyConfig:
    return load_strategy_config("config/atlax.yaml")

@pytest.fixture
def strategy() -> CRTStrategy:
    return CRTStrategy()

@pytest.fixture
def base_metadata() -> CRTMetadata:
    now = datetime(2026, 7, 20, 12, 0, tzinfo=timezone.utc)
    parent = Candle("EURUSD", "H4", now, now, Decimal("100"), Decimal("102.50"), Decimal("98.50"), Decimal("101"), True)
    sweep = Candle("EURUSD", "H4", now, now, Decimal("101"), Decimal("101.80"), Decimal("97.80"), Decimal("100.80"), True)
    return CRTMetadata(
        parent_candle=parent,
        sweep_candle=sweep,
        confirmation_candle=None,
        swept_level="CRL",
        crt_high=Decimal("102.50"),
        crt_low=Decimal("98.50"),
        midpoint_target=Decimal("100.50"),
        opposite_extreme_target=Decimal("102.50"),
        sweep_distance_pips=70.0,
        close_location_ratio=0.575,
        sweep_wick_ratio=0.175,
        parent_at_htf_zone=True,
        session_alignment=True,
        htf_trend_alignment=True,
        fvg_present_after_sweep=True,
        msb_on_ltf=True
    )

@pytest.fixture
def bullish_detection(base_metadata) -> DetectorOutput:
    now = datetime(2026, 7, 20, 12, 0, tzinfo=timezone.utc)
    return DetectorOutput(
        detector="CRTDetector",
        symbol="EURUSD",
        timeframe="H4",
        detected=True,
        timestamp=now,
        classification="bullish_crt",
        reason="Test detection",
        invalidation_reason=None,
        metadata=base_metadata
    )

@pytest.fixture
def bearish_detection(base_metadata) -> DetectorOutput:
    now = datetime(2026, 7, 20, 12, 0, tzinfo=timezone.utc)
    bearish_meta = dataclasses.replace(
        base_metadata,
        swept_level="CRH",
        opposite_extreme_target=Decimal("98.50")
    )
    return DetectorOutput(
        detector="CRTDetector",
        symbol="EURUSD",
        timeframe="H4",
        detected=True,
        timestamp=now,
        classification="bearish_crt",
        reason="Test detection",
        invalidation_reason=None,
        metadata=bearish_meta
    )
