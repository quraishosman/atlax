"""
ATLAX Webhook Intake API.

Handles incoming alerts from TradingView.
Authority: docs/05_API_SPECIFICATION.md and docs/16_SECURITY.md
"""
from fastapi import APIRouter, HTTPException, Depends, Header, Request
from datetime import datetime, timezone
import uuid
import os
import logging
from secrets import compare_digest
from typing import Optional

from core.models.webhook import TradingViewPayload, NormalizedWebhookEvent
from core.settings.timeframe_policy import is_retired_timeframe
from core.api.webhook_store import WebhookIntakeStore

router = APIRouter(prefix="/api/webhooks", tags=["webhooks"])
logger = logging.getLogger("atlax.webhooks")

webhook_secret = os.environ.get("ATLAX_WEBHOOK_SECRET", "").strip()
if not webhook_secret:
    raise RuntimeError(
        "ATLAX_WEBHOOK_SECRET is required. "
        "TradingView webhook secrets must not be stored in config/atlax.yaml."
    )

_webhook_store: WebhookIntakeStore | None = None


def get_webhook_store() -> WebhookIntakeStore:
    """Return the process-local durable intake store."""
    global _webhook_store
    if _webhook_store is None:
        _webhook_store = WebhookIntakeStore()
    return _webhook_store


def _normalized_event_from_dict(value: dict) -> NormalizedWebhookEvent:
    if hasattr(NormalizedWebhookEvent, "model_validate"):
        return NormalizedWebhookEvent.model_validate(value)
    return NormalizedWebhookEvent.parse_obj(value)

def verify_webhook_secret(x_atlax_webhook_secret: Optional[str] = Header(None)):
    """
    Validates that the incoming webhook contains the correct authorization header.
    """
    if not x_atlax_webhook_secret or not compare_digest(x_atlax_webhook_secret, webhook_secret):
        logger.warning("Unauthorized webhook attempt blocked.")
        raise HTTPException(status_code=401, detail="Unauthorized")
    return x_atlax_webhook_secret

@router.post("/tradingview", response_model=NormalizedWebhookEvent)
async def tradingview_intake(
    payload: TradingViewPayload, 
    request: Request,
    secret: str = Depends(verify_webhook_secret)
):
    """
    Intake endpoint for TradingView webhooks.
    Validates payload shape, adds ATLAX routing metadata, and acknowledges receipt.
    """
    now = datetime.now(timezone.utc).isoformat()

    if is_retired_timeframe(payload.timeframe):
        logger.warning(
            '{"event":"webhook_rejected","reason":"retired_timeframe",'
            '"timeframe":"%s","symbol":"%s"}',
            payload.timeframe,
            payload.symbol,
        )
        raise HTTPException(
            status_code=422,
            detail=f"Timeframe {payload.timeframe!r} is retired from active ATLAX use",
        )
    
    # Create the normalized envelope
    normalized = NormalizedWebhookEvent(
        schemaVersion="1.0",
        messageId=f"evt_{uuid.uuid4().hex[:12]}",
        createdAt=payload.eventTime,
        receivedAt=now,
        source={
            "platform": "TradingView",
            "ip": request.client.host if request.client else "unknown"
        },
        correlationId=f"corr_{uuid.uuid4().hex[:12]}",
        payload=payload
    )
    
    result = get_webhook_store().enqueue(
        normalized,
        get_webhook_store().fingerprint(payload),
    )
    if result.duplicate:
        logger.info(
            '{"event":"webhook_duplicate_ignored","message_id":"%s",'
            '"symbol":"%s","timeframe":"%s"}',
            result.item.message_id,
            payload.symbol,
            payload.timeframe,
        )
        return _normalized_event_from_dict(result.item.normalized)

    logger.info(
        '{"event":"webhook_accepted","message_id":"%s",'
        '"correlation_id":"%s","symbol":"%s","timeframe":"%s",'
        '"validation":"valid","queue_status":"RECEIVED"}',
        normalized.messageId,
        normalized.correlationId,
        payload.symbol,
        payload.timeframe,
    )
    return normalized
