"""Shadow-only consumer for the approved TradingView detector-event boundary.

This module validates provenance and the generic detector output contract. It
does not run a detector, create a strategy candidate, score confidence, send
an alert, approve risk, or execute a trade.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import logging
from typing import Any

from core.api.webhook_store import WebhookIntakeStore
from core.models.webhook import NormalizedWebhookEvent
from core.settings.timeframe_policy import is_retired_timeframe

logger = logging.getLogger("atlax.webhooks.consumer")

DETECTOR_CONTRACT_VERSION = "1.0"
_REQUIRED_METADATA = (
    "detectorContractVersion",
    "scriptVersion",
    "settingsVersion",
    "rulebookVersion",
    "configurationVersion",
    "confidence",
    "reason",
    "invalidation",
    "evidence",
)
_STRATEGY_KEYS = {"direction", "tradedirection", "signal", "action", "decision"}
_FORBIDDEN_STRATEGY_VALUES = {"BUY", "SELL", "EXECUTE"}


@dataclass(frozen=True)
class DetectorEventValidation:
    status: str
    reason: str
    detector_event: dict[str, Any]


def _model_validate(value: dict[str, Any]) -> NormalizedWebhookEvent:
    if hasattr(NormalizedWebhookEvent, "model_validate"):
        return NormalizedWebhookEvent.model_validate(value)
    return NormalizedWebhookEvent.parse_obj(value)


def _utc_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed


def _contains_strategy_decision(value: Any, *, key: str = "") -> bool:
    if isinstance(value, dict):
        for child_key, child_value in value.items():
            normalized_key = str(child_key).replace("_", "").lower()
            if normalized_key in _STRATEGY_KEYS:
                if isinstance(child_value, str) and child_value.upper() in _FORBIDDEN_STRATEGY_VALUES:
                    return True
            if _contains_strategy_decision(child_value, key=str(child_key)):
                return True
        return False
    if isinstance(value, list):
        return any(_contains_strategy_decision(item, key=key) for item in value)
    return False


def _non_empty_text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def validate_detector_event(value: dict[str, Any]) -> DetectorEventValidation:
    """Validate the approved generic detector-result contract in shadow mode."""
    try:
        event = _model_validate(value)
    except Exception as exc:  # noqa: BLE001
        return DetectorEventValidation(
            status="UNKNOWN",
            reason=f"invalid_normalized_event:{type(exc).__name__}",
            detector_event={"raw": value},
        )

    payload = event.payload
    metadata = payload.metadata if isinstance(payload.metadata, dict) else None
    if event.schemaVersion != "1.0":
        reason = "unsupported_envelope_schema"
    elif event.source.get("platform") != "TradingView":
        reason = "unsupported_source_platform"
    elif payload.eventType != "detector_result":
        reason = "unsupported_event_type"
    elif is_retired_timeframe(payload.timeframe):
        reason = "retired_timeframe"
    elif _utc_timestamp(payload.eventTime) is None:
        reason = "event_time_must_be_timezone_aware"
    elif metadata is None:
        reason = "detector_metadata_missing"
    else:
        missing = [key for key in _REQUIRED_METADATA if key not in metadata]
        if missing:
            reason = "detector_metadata_incomplete:" + ",".join(missing)
        elif str(metadata.get("detectorContractVersion")) != DETECTOR_CONTRACT_VERSION:
            reason = "unsupported_detector_contract_version"
        elif any(
            not _non_empty_text(metadata.get(key))
            for key in (
                "scriptVersion",
                "settingsVersion",
                "rulebookVersion",
                "configurationVersion",
                "reason",
                "invalidation",
            )
        ):
            reason = "detector_metadata_has_blank_text"
        elif not isinstance(metadata.get("evidence"), dict) or not metadata["evidence"]:
            reason = "detector_evidence_missing"
        elif _contains_strategy_decision(metadata):
            reason = "detector_payload_contains_strategy_decision"
        else:
            detector_event = {
                "schemaVersion": DETECTOR_CONTRACT_VERSION,
                "messageId": event.messageId,
                "correlationId": event.correlationId,
                "source": dict(event.source),
                "detector": payload.detector,
                "symbol": payload.symbol,
                "timeframe": payload.timeframe,
                "detected": payload.detected,
                "timestamp": payload.eventTime,
                "confidence": metadata["confidence"],
                "reason": metadata["reason"],
                "invalidation": metadata["invalidation"],
                "metadata": dict(metadata),
            }
            return DetectorEventValidation(
                status="OK",
                reason="shadow_detector_contract_valid",
                detector_event=detector_event,
            )

    return DetectorEventValidation(
        status="UNKNOWN",
        reason=reason,
        detector_event={
            "schemaVersion": DETECTOR_CONTRACT_VERSION,
            "messageId": event.messageId,
            "correlationId": event.correlationId,
            "source": dict(event.source),
            "detector": payload.detector,
            "symbol": payload.symbol,
            "timeframe": payload.timeframe,
            "detected": payload.detected,
            "timestamp": payload.eventTime,
        },
    )


class WebhookShadowConsumer:
    """Process queued events without downstream trading side effects."""

    def __init__(self, store: WebhookIntakeStore | None = None) -> None:
        self.store = store or WebhookIntakeStore()

    def consume_once(self, *, limit: int = 10) -> dict[str, int]:
        return self.store.consume_pending(self._process, limit=limit)

    def _process(self, normalized: dict[str, Any]) -> None:
        result = validate_detector_event(normalized)
        message_id = str(normalized.get("messageId", ""))
        correlation_id = str(normalized.get("correlationId", ""))
        if not message_id or not correlation_id:
            raise ValueError("normalized webhook event identifiers are required")
        recorded = self.store.record_processing_result(
            message_id=message_id,
            correlation_id=correlation_id,
            status=result.status,
            reason=result.reason,
            detector_event=result.detector_event,
        )
        logger.info(
            '{"event":"webhook_shadow_processed","message_id":"%s",'
            '"correlation_id":"%s","status":"%s","reason":"%s"}',
            recorded.message_id,
            recorded.correlation_id,
            recorded.status,
            recorded.reason,
        )
