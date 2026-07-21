"""
ATLAX Log Alert Channel.

Delivers alerts as structured JSON to the Python logging system.
This channel is always active when enabled — it is the permanent
audit trail for every alert that was sent or suppressed.

Authority: docs/15_LOGGING.md (structured JSON logging)
           docs/03_ARCHITECTURE.md (audit trail must not be deleted)
"""

from __future__ import annotations

import json
import logging

from core.alerts.base import BaseAlertChannel
from core.models.alert_event import AlertEvent

logger = logging.getLogger("atlax.alerts.log")


class LogAlertChannel(BaseAlertChannel):
    """
    Structured JSON log alert channel.

    Writes every AlertEvent as a single JSON log line at INFO level.
    Used as the permanent audit trail and as the default output
    when no other channels are configured.

    This channel never fails silently — if logging itself is broken,
    that is a system-level issue outside ATLAX's control.
    """

    def __init__(self, enabled: bool = True) -> None:
        self._enabled = enabled

    @property
    def name(self) -> str:
        return "log"

    @property
    def is_enabled(self) -> bool:
        return self._enabled

    def send(self, alert: AlertEvent) -> bool:
        """
        Write the alert as a structured JSON log line.

        Args:
            alert: The AlertEvent to log.

        Returns:
            Always True — log delivery cannot fail silently.
        """
        if not self._enabled:
            return False

        payload = {
            "event": "alert_sent",
            "channel": "log",
            "alert_id": alert.alert_id,
            "candidate_id": alert.candidate_id,
            "symbol": alert.symbol,
            "timeframe": alert.timeframe,
            "profile": alert.profile,
            "direction": alert.direction,
            "classification": alert.classification,
            "confidence_score": round(alert.confidence_score, 2),
            "crt_high": str(alert.crt_high),
            "crt_low": str(alert.crt_low),
            "entry_zone_high": str(alert.entry_zone_high),
            "entry_zone_low": str(alert.entry_zone_low),
            "invalidation_level": str(alert.invalidation_level),
            "midpoint_target": str(alert.midpoint_target),
            "opposite_extreme_target": str(alert.opposite_extreme_target),
            "missing_factors": list(alert.missing_factors),
            "created_at": alert.created_at.isoformat(),
        }

        logger.info(json.dumps(payload))
        return True
