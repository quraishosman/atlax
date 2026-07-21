"""
ATLAX Alert Engine.

Orchestrates alert routing for scored trade candidates.
Applies deduplication, confidence threshold gating, and
routes accepted alerts to all configured delivery channels.

Authority Documents:
    - docs/03_ARCHITECTURE.md (Alert Engine responsibilities, deduplication)
    - docs/04_SYSTEM_DESIGN.md (Alert Engine layer: sends alerts only)

Responsibilities (one only):
    - Send alerts. Never execute trades.

Deduplication (docs/03_ARCHITECTURE.md):
    "Minimum seconds between alerts for the same pair."
    "Preserve all raw events in logs even when notifications are deduplicated."
    Deduplication key: (symbol, timeframe, profile, direction)
    Window: configurable seconds (default 300 = 5 minutes)

Threshold Gating:
    When suppress_below_threshold=True, candidates whose confidence score
    is below the profile's min_confidence_threshold are suppressed.
    Suppressed alerts are still logged (audit trail preserved).

Architectural Boundaries:
    - AlertEngine is STATELESS between scanner cycles except for the
      deduplication cache (in-memory, intentionally ephemeral).
    - AlertEngine does NOT detect patterns.
    - AlertEngine does NOT execute trades.
    - AlertEngine does NOT modify the trade candidate.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

from core.alerts.base import BaseAlertChannel
from core.alerts.log_channel import LogAlertChannel
from core.alerts.telegram_channel import TelegramAlertChannel
from core.models.alert_event import AlertEvent
from core.models.confidence_score import ConfidenceScore
from core.models.trade_candidate import TradeCandidate
from core.settings.alert_config import AlertConfig
from core.settings.strategy_config import ProfileConfig

logger = logging.getLogger("atlax.alerts.engine")


class AlertEngine:
    """
    Alert routing and delivery orchestrator.

    Accepts a scored TradeCandidate, applies deduplication and threshold
    gating, builds an AlertEvent, and delivers it to all active channels.

    Args:
        config: Immutable alert configuration.
        profile_config: The active profile's configuration (for threshold).
    """

    def __init__(
        self,
        config: AlertConfig,
        channels: Optional[list[BaseAlertChannel]] = None,
    ) -> None:
        self._config = config
        self._channels: list[BaseAlertChannel] = channels or self._build_channels()
        # Deduplication cache: key → last alert UTC timestamp (seconds)
        self._dedup_cache: dict[str, float] = {}

    def _build_channels(self) -> list[BaseAlertChannel]:
        """Build the default channel list from config."""
        channels: list[BaseAlertChannel] = []
        if self._config.log_channel_enabled:
            channels.append(LogAlertChannel(enabled=True))
        if self._config.telegram.enabled:
            channels.append(TelegramAlertChannel(self._config.telegram))
        return channels

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def process(
        self,
        candidate: TradeCandidate,
        confidence: ConfidenceScore,
        profile_config: ProfileConfig,
    ) -> Optional[AlertEvent]:
        """
        Process a scored trade candidate through the alert pipeline.

        Steps:
            1. Threshold gate: suppress if score < profile minimum.
            2. Deduplication gate: suppress if same key alerted recently.
            3. Build AlertEvent from candidate + confidence score.
            4. Deliver to all active channels.
            5. Log all decisions — sent or suppressed.

        Args:
            candidate: The TradeCandidate from the Strategy Engine.
            confidence: The ConfidenceScore from the Confidence Engine.
            profile_config: The profile's config (for threshold check).

        Returns:
            The AlertEvent if it was delivered, None if suppressed.
        """
        if not self._config.enabled:
            self._log_suppressed(candidate, confidence, "alert_engine_disabled")
            return None

        # --- Gate 1: Confidence threshold ---
        if self._config.suppress_below_threshold:
            if confidence.final_score < profile_config.min_confidence_threshold:
                self._log_suppressed(
                    candidate, confidence,
                    f"below_threshold({confidence.final_score:.1f}"
                    f"<{profile_config.min_confidence_threshold})",
                )
                return None

        # --- Gate 2: Deduplication ---
        dedup_key = self._dedup_key(candidate)
        now_ts = datetime.now(timezone.utc).timestamp()

        if self._config.deduplication_window_seconds > 0:
            last_sent = self._dedup_cache.get(dedup_key)
            if last_sent is not None:
                elapsed = now_ts - last_sent
                if elapsed < self._config.deduplication_window_seconds:
                    self._log_suppressed(
                        candidate, confidence,
                        f"dedup({elapsed:.0f}s < "
                        f"{self._config.deduplication_window_seconds}s window)",
                    )
                    return None

        # --- Build AlertEvent ---
        alert = self._build_alert(candidate, confidence)

        # --- Deliver to channels ---
        delivered_count = 0
        for channel in self._channels:
            if channel.is_enabled:
                success = channel.send(alert)
                if success:
                    delivered_count += 1

        # --- Update dedup cache (regardless of delivery success) ---
        self._dedup_cache[dedup_key] = now_ts

        logger.info(
            '{"event":"alert_dispatched","alert_id":"%s","candidate_id":"%s",'
            '"symbol":"%s","timeframe":"%s","profile":"%s","direction":"%s",'
            '"confidence":%.2f,"channels_delivered":%d}',
            alert.alert_id, candidate.candidate_id,
            candidate.symbol, candidate.timeframe, candidate.profile,
            candidate.direction, confidence.final_score, delivered_count,
        )

        return alert

    def reset_dedup_cache(self) -> None:
        """
        Clear the deduplication cache.
        Useful for testing or when restarting the scanner.
        """
        self._dedup_cache.clear()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_alert(
        self,
        candidate: TradeCandidate,
        confidence: ConfidenceScore,
    ) -> AlertEvent:
        """Construct an AlertEvent from a candidate and its confidence score."""
        metadata = candidate.confidence_inputs
        breakdown = {
            f.factor_name: (
                "UNKNOWN" if f.is_unknown
                else f"{f.score:.1f}/{f.max_score:.1f}"
            )
            for f in confidence.factor_breakdown
        }

        return AlertEvent(
            alert_id=str(uuid.uuid4()),
            candidate_id=candidate.candidate_id,
            symbol=candidate.symbol,
            timeframe=candidate.timeframe,
            profile=candidate.profile,
            direction=candidate.direction,
            classification=metadata.get("classification", "UNKNOWN"),
            confidence_score=confidence.final_score,
            confidence_breakdown=breakdown,
            missing_factors=confidence.missing_factors,
            crt_high=candidate.target_model.opposite_extreme_target
            if candidate.direction == "BUY"
            else candidate.entry_model.entry_zone_high,
            crt_low=candidate.entry_model.entry_zone_low
            if candidate.direction == "BUY"
            else candidate.target_model.opposite_extreme_target,
            entry_zone_high=candidate.entry_model.entry_zone_high,
            entry_zone_low=candidate.entry_model.entry_zone_low,
            invalidation_level=candidate.invalidation_model.invalidation_level,
            midpoint_target=candidate.target_model.midpoint_target,
            opposite_extreme_target=candidate.target_model.opposite_extreme_target,
            explanation=candidate.explanation,
            created_at=datetime.now(timezone.utc),
        )

    @staticmethod
    def _dedup_key(candidate: TradeCandidate) -> str:
        """Build a deduplication key from the candidate's identity."""
        return (
            f"{candidate.symbol}:{candidate.timeframe}:"
            f"{candidate.profile}:{candidate.direction}"
        )

    def _log_suppressed(
        self,
        candidate: TradeCandidate,
        confidence: ConfidenceScore,
        reason: str,
    ) -> None:
        """Log a suppressed alert — audit trail is always preserved."""
        logger.info(
            '{"event":"alert_suppressed","candidate_id":"%s","symbol":"%s",'
            '"timeframe":"%s","profile":"%s","direction":"%s",'
            '"confidence":%.2f,"reason":"%s"}',
            candidate.candidate_id, candidate.symbol, candidate.timeframe,
            candidate.profile, candidate.direction,
            confidence.final_score, reason,
        )
