import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from core.api.webhook_consumer import WebhookShadowConsumer, validate_detector_event
from core.api.webhook_store import WebhookIntakeStore
from core.models.webhook import NormalizedWebhookEvent, TradingViewPayload


FIXTURE_PATH = Path(__file__).parents[2] / "fixtures" / "webhooks" / "shadow_replay.json"


def _event(message_id: str, *, complete: bool = True, forbidden: bool = False):
    metadata = {"sourceVersion": "replay-1"}
    if complete:
        metadata.update(
            {
                "detectorContractVersion": "1.0",
                "scriptVersion": "tv-1",
                "settingsVersion": "settings-1",
                "rulebookVersion": "CRT-1.0",
                "configurationVersion": "config-1",
                "confidence": 72,
                "reason": "approved detector evidence present",
                "invalidation": "UNKNOWN",
                "evidence": {"detected": True, "pattern": "bullish_crt"},
            }
        )
    if forbidden:
        metadata["direction"] = "BUY"
    payload = TradingViewPayload(
        symbol="EURUSD",
        timeframe="M15",
        eventType="detector_result",
        detector="CRTDetector",
        detected=True,
        eventTime="2026-08-14T10:15:00Z",
        metadata=metadata,
    )
    return NormalizedWebhookEvent(
        schemaVersion="1.0",
        messageId=message_id,
        createdAt=payload.eventTime,
        receivedAt=datetime.now(timezone.utc).isoformat(),
        source={"platform": "TradingView", "ip": "127.0.0.1"},
        correlationId=f"corr_{message_id}",
        payload=payload,
    )


def test_shadow_consumer_records_valid_event_without_side_effects(tmp_path):
    store = WebhookIntakeStore(str(tmp_path / "webhooks.sqlite"))
    event = _event("evt_valid")
    store.enqueue(event, store.fingerprint(event.payload))

    summary = WebhookShadowConsumer(store).consume_once()

    assert summary == {"claimed": 1, "processed": 1, "retryable": 0, "dead_letter": 0}
    result = store.get_processing_result("evt_valid")
    assert result is not None
    assert result.status == "OK"
    assert result.detector_event["symbol"] == "EURUSD"
    assert "direction" not in result.detector_event


def test_incomplete_event_is_audited_as_unknown_and_acknowledged(tmp_path):
    store = WebhookIntakeStore(str(tmp_path / "webhooks.sqlite"))
    event = _event("evt_incomplete", complete=False)
    store.enqueue(event, store.fingerprint(event.payload))

    summary = WebhookShadowConsumer(store).consume_once()

    assert summary["processed"] == 1
    result = store.get_processing_result("evt_incomplete")
    assert result is not None
    assert result.status == "UNKNOWN"
    assert "detector_metadata_incomplete" in result.reason


def test_detector_event_with_strategy_direction_is_unknown():
    event = _event("evt_forbidden", forbidden=True)

    result = validate_detector_event(event.model_dump(mode="json") if hasattr(event, "model_dump") else event.dict())

    assert result.status == "UNKNOWN"
    assert result.reason == "detector_payload_contains_strategy_decision"


@pytest.mark.parametrize(
    "case",
    json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))["cases"],
    ids=lambda case: case["name"],
)
def test_shadow_replay_fixture_matches_approved_outcome(case):
    result = validate_detector_event(case["event"])

    assert result.status == case["expectedStatus"]
    if "expectedReason" in case:
        assert result.reason == case["expectedReason"]
    else:
        assert result.reason.startswith(case["expectedReasonPrefix"])


def _parse_event(value):
    if hasattr(NormalizedWebhookEvent, "model_validate"):
        return NormalizedWebhookEvent.model_validate(value)
    return NormalizedWebhookEvent.parse_obj(value)


def test_shadow_replay_persists_retired_and_incomplete_outcomes(tmp_path):
    fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    store = WebhookIntakeStore(str(tmp_path / "webhooks.sqlite"))
    events = [
        case["event"]
        for case in fixture["cases"]
        if case["name"] in {"incomplete_detector_metadata", "retired_timeframe"}
    ]
    for event_data in events:
        event = _parse_event(event_data)
        store.enqueue(event, store.fingerprint(event.payload))

    summary = WebhookShadowConsumer(store).consume_once(limit=2)

    assert summary == {"claimed": 2, "processed": 2, "retryable": 0, "dead_letter": 0}
    incomplete = store.get_processing_result("evt_fixture_incomplete")
    retired = store.get_processing_result("evt_fixture_retired")
    assert incomplete is not None and incomplete.status == "UNKNOWN"
    assert retired is not None and retired.reason == "retired_timeframe"


def test_duplicate_fixture_payload_is_processed_once(tmp_path):
    fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    event_data = next(
        case["event"]
        for case in fixture["cases"]
        if case["name"] == "complete_detected_event"
    )
    first = _parse_event(event_data)
    duplicate_data = dict(event_data)
    duplicate_data["messageId"] = "evt_fixture_duplicate"
    duplicate_data["correlationId"] = "corr_fixture_duplicate"
    duplicate = _parse_event(duplicate_data)
    store = WebhookIntakeStore(str(tmp_path / "webhooks.sqlite"))

    first_result = store.enqueue(first, store.fingerprint(first.payload))
    duplicate_result = store.enqueue(duplicate, store.fingerprint(duplicate.payload))

    assert first_result.duplicate is False
    assert duplicate_result.duplicate is True
    assert duplicate_result.item.message_id == "evt_fixture_valid"
