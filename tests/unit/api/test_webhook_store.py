from datetime import datetime, timezone

from core.api.webhook_store import WebhookIntakeStore
from core.models.webhook import NormalizedWebhookEvent, TradingViewPayload


def _event(message_id: str, correlation_id: str) -> NormalizedWebhookEvent:
    payload = TradingViewPayload(
        symbol="EURUSD",
        timeframe="M15",
        eventType="detector_result",
        detector="CRTDetector",
        detected=True,
        eventTime="2026-08-14T10:15:00Z",
        metadata={"sourceVersion": "replay-1"},
    )
    return NormalizedWebhookEvent(
        schemaVersion="1.0",
        messageId=message_id,
        createdAt=payload.eventTime,
        receivedAt=datetime.now(timezone.utc).isoformat(),
        source={"platform": "TradingView", "ip": "127.0.0.1"},
        correlationId=correlation_id,
        payload=payload,
    )


def test_webhook_store_is_idempotent_and_durable(tmp_path):
    store = WebhookIntakeStore(str(tmp_path / "webhooks.sqlite"))
    first = _event("evt_first", "corr_first")
    second = _event("evt_second", "corr_second")
    fingerprint = store.fingerprint(first.payload)

    queued = store.enqueue(first, fingerprint)
    duplicate = store.enqueue(second, fingerprint)

    assert queued.duplicate is False
    assert duplicate.duplicate is True
    assert duplicate.item.message_id == "evt_first"

    restarted = WebhookIntakeStore(str(tmp_path / "webhooks.sqlite"))
    claimed = restarted.claim_pending()
    assert [item.message_id for item in claimed] == ["evt_first"]
    assert claimed[0].attempts == 1
    assert restarted.mark_processed("evt_first") is True
    assert restarted.claim_pending() == []


def test_webhook_consumer_acknowledges_once(tmp_path):
    store = WebhookIntakeStore(str(tmp_path / "webhooks.sqlite"))
    event = _event("evt_consume", "corr_consume")
    store.enqueue(event, store.fingerprint(event.payload))
    handled = []

    summary = store.consume_pending(lambda value: handled.append(value["messageId"]))

    assert summary == {"claimed": 1, "processed": 1, "retryable": 0, "dead_letter": 0}
    assert handled == ["evt_consume"]
    assert store.consume_pending(lambda value: handled.append(value["messageId"]))["claimed"] == 0


def test_webhook_consumer_dead_letters_after_configured_attempts(tmp_path):
    store = WebhookIntakeStore(
        str(tmp_path / "webhooks.sqlite"),
        max_attempts=1,
        retry_delay_seconds=0,
    )
    event = _event("evt_fail", "corr_fail")
    store.enqueue(event, store.fingerprint(event.payload))

    summary = store.consume_pending(lambda value: (_ for _ in ()).throw(RuntimeError("downstream unavailable")))

    assert summary == {"claimed": 1, "processed": 0, "retryable": 0, "dead_letter": 1}
    assert store.claim_pending() == []
