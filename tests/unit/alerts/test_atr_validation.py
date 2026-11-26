from __future__ import annotations

import dataclasses
from decimal import Decimal

from core.alerts.alert_engine import AlertEngine
from core.alerts.base import BaseAlertChannel
from core.confidence.atr_quality import ATRQualityResult
from core.settings.alert_config import load_alert_config


class DisabledChannel(BaseAlertChannel):
    name = "disabled"
    is_enabled = False

    def send(self, alert):
        return False


class RecordingJournal:
    def __init__(self):
        self.entries = []

    def add_signal_entry(self, **entry):
        self.entries.append(entry)


class MemoryRuntime:
    def __init__(self):
        self.values = {}

    def get(self, key, default=None):
        return self.values.get(key, default)

    def set(self, key, value):
        self.values[key] = value


def test_constructed_signal_is_passported_when_delivery_is_suppressed(
    alert_config, sample_candidate, sample_confidence, sample_profile
):
    journal = RecordingJournal()
    engine = AlertEngine(
        alert_config,
        channels=[DisabledChannel()],
        journal=journal,
        runtime_store=MemoryRuntime(),
    )

    alert = engine.process(sample_candidate, sample_confidence, sample_profile)

    assert alert is not None
    assert journal.entries
    assert journal.entries[0]["candidate_id"] == sample_candidate.candidate_id


def test_atr_quality_does_not_add_execution_instructions(
    sample_candidate, sample_confidence, sample_profile
):
    engine = AlertEngine(
        dataclasses.replace(
            load_alert_config("config/atlax.yaml"),
            enabled=False,
        ),
        channels=[],
        journal=RecordingJournal(),
        runtime_store=MemoryRuntime(),
    )
    quality = ATRQualityResult(
        status="VALID",
        grade="A",
        atr=Decimal("0.0010"),
        atr_period=14,
        method="SIMPLE_TR_MEAN",
        parent_range=Decimal("0.0008"),
        parent_atr_ratio=Decimal("0.8"),
        sweep_depth=Decimal("0.0002"),
        sweep_atr_ratio=Decimal("0.2"),
        configuration_snapshot_id="test-snapshot",
        explanation="test quality",
    )
    alert = engine._build_alert(sample_candidate, sample_confidence, quality)
    display = alert.to_display_dict()

    assert display["atr_quality"]["grade"] == "A"
    assert "lot_size" not in display
    assert "risk_percentage" not in display
    assert "stop_loss_price" not in display
    assert "take_profit_price" not in display
