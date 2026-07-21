"""
ATLAX Detection Event Logger.

Structured JSON logging for every detect() call. Every detection
event — whether successful or not — is logged with full context
for audit, replay, and debugging.

Authority Documents:
    - docs/15_LOGGING.md (logging specification)
    - docs/01_ONBOARDING.md (log every detector result)

Log Levels (from docs/15_LOGGING.md):
    - DEBUG: Every detection evaluation (including non-detections).
    - INFO: Successful pattern detections.
    - WARNING: Configuration issues, missing data.
    - ERROR: Failures, exceptions, invariant violations.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from core.models.detector_output import DetectorOutput


# Module-level logger for detection events.
logger = logging.getLogger("atlax.detection")


class DetectionEventLogger:
    """
    Structured JSON logger for CRT detection events.

    Produces one structured log entry per detect() call containing
    all fields needed for audit and replay.

    Authority: docs/15_LOGGING.md
        "Every detection event logged with: symbol, timeframe,
        classification, detected, reason, timestamp."

    Usage:
        event_logger = DetectionEventLogger()
        event_logger.log_detection(output)
    """

    @staticmethod
    def log_detection(output: DetectorOutput) -> None:
        """
        Log a detection event as structured JSON.

        Successful detections are logged at INFO level.
        Non-detections are logged at DEBUG level.

        Args:
            output: The DetectorOutput from a detect() call.
        """
        log_entry: dict[str, Any] = {
            "event": "crt_detection",
            "logged_at": datetime.now(timezone.utc).isoformat(),
            "detector": output.detector,
            "symbol": output.symbol,
            "timeframe": output.timeframe,
            "detected": output.detected,
            "classification": output.classification,
            "reason": output.reason,
            "invalidation_reason": output.invalidation_reason,
            "detection_timestamp": output.timestamp.isoformat(),
        }

        # Add metadata summary if present (avoid logging full candle objects)
        if output.metadata is not None:
            log_entry["metadata_summary"] = {
                "swept_level": output.metadata.swept_level,
                "crt_high": str(output.metadata.crt_high),
                "crt_low": str(output.metadata.crt_low),
                "sweep_distance_pips": output.metadata.sweep_distance_pips,
                "close_location_ratio": output.metadata.close_location_ratio,
                "sweep_wick_ratio": output.metadata.sweep_wick_ratio,
                "session_alignment": output.metadata.session_alignment,
            }

        message = json.dumps(log_entry, default=str)

        if output.detected:
            logger.info(message)
        else:
            logger.debug(message)

    @staticmethod
    def log_error(
        detector_name: str,
        symbol: str,
        timeframe: str,
        error: Exception,
    ) -> None:
        """
        Log a detection error as structured JSON.

        Args:
            detector_name: Name of the detector that failed.
            symbol: The instrument being evaluated.
            timeframe: The timeframe being evaluated.
            error: The exception that occurred.
        """
        log_entry: dict[str, Any] = {
            "event": "crt_detection_error",
            "logged_at": datetime.now(timezone.utc).isoformat(),
            "detector": detector_name,
            "symbol": symbol,
            "timeframe": timeframe,
            "error_type": type(error).__name__,
            "error_message": str(error),
        }

        logger.error(json.dumps(log_entry, default=str))
