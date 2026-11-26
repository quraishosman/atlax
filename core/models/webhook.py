"""
Webhook intake data models.

Authority: docs/05_API_SPECIFICATION.md (TradingView Webhook Intake Contract)
"""

from typing import Dict, Any, Optional
from pydantic import BaseModel, Field

class TradingViewPayload(BaseModel):
    """
    Expected JSON payload from TradingView alerts.
    """
    symbol: str = Field(..., description="Instrument symbol, e.g., EURUSD")
    timeframe: str = Field(..., description="Candle timeframe, e.g., M15")
    eventType: str = Field(..., description="Type of event, usually 'detector_result'")
    detector: str = Field(..., description="Name of the detector, e.g., 'CRTDetector'")
    detected: bool = Field(..., description="Whether the pattern was detected")
    eventTime: str = Field(..., description="ISO8601 UTC timestamp of the event from TV")
    metadata: Optional[Dict[str, Any]] = Field(default_factory=dict, description="Flexible metadata for specific detectors")

class NormalizedWebhookEvent(BaseModel):
    """
    The ATLAX standard message envelope for processed webhooks.
    """
    schemaVersion: str = "1.0"
    messageId: str
    createdAt: str
    receivedAt: str
    source: Dict[str, str]
    correlationId: str
    payload: TradingViewPayload
