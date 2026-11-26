import pytest
from datetime import datetime, timezone
from decimal import Decimal
import dataclasses

from core.settings.alert_config import load_alert_config, AlertConfig
from core.alerts.alert_engine import AlertEngine
from core.alerts.log_channel import LogAlertChannel
from core.models.trade_candidate import TradeCandidate, EntryModel, InvalidationModel, TargetModel, Direction
from core.models.confidence_score import ConfidenceScore
from core.settings.strategy_config import ProfileConfig

class MemoryRuntime:
    def __init__(self):
        self.values = {}

    def get(self, key, default=None):
        return self.values.get(key, default)

    def set(self, key, value):
        self.values[key] = value


@pytest.fixture
def runtime_store() -> MemoryRuntime:
    return MemoryRuntime()


@pytest.fixture
def alert_config() -> AlertConfig:
    return load_alert_config("config/atlax.yaml")

@pytest.fixture
def alert_engine(alert_config, tmp_path, runtime_store) -> AlertEngine:
    return AlertEngine(
        alert_config,
        channels=[LogAlertChannel(enabled=True)],
        db_path=str(tmp_path / "journal.sqlite"),
        runtime_store=runtime_store,
    )

@pytest.fixture
def sample_candidate() -> TradeCandidate:
    now = datetime(2026, 7, 20, 12, 0, tzinfo=timezone.utc)
    inputs = {
        "classification": "bullish_crt",
        "swept_level": "CRL"
    }
    return TradeCandidate(
        candidate_id="test-cand-001",
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

@pytest.fixture
def sample_confidence(sample_candidate) -> ConfidenceScore:
    return ConfidenceScore(
        candidate_id=sample_candidate.candidate_id,
        symbol=sample_candidate.symbol,
        timeframe=sample_candidate.timeframe,
        profile=sample_candidate.profile,
        final_score=85.0,
        raw_score=85.0,
        factor_breakdown=tuple(),
        missing_factors=tuple(),
        config_snapshot_id="test-hash",
        explanation="test explanation",
        version="1.0.0",
        scored_at=datetime.now(timezone.utc)
    )

@pytest.fixture
def sample_profile() -> ProfileConfig:
    return ProfileConfig(
        name="swing_trader",
        enabled=True,
        allowed_timeframes=["H1", "H4", "D1"],
        require_session_alignment=False,
        require_htf_trend_alignment=False,
        min_confidence_threshold=70.0
    )
