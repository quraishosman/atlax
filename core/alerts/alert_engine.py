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
    An explicit, valid Telegram subscriber minimum below the profile floor may
    opt into subscriber delivery; the admin channel remains suppressed.

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
from core.alerts.discord_channel import DiscordChannel
from core.models.alert_event import AlertEvent
from core.models.confidence_score import ConfidenceScore
from core.models.trade_candidate import TradeCandidate
from core.confidence.atr_quality import ATRQualityResult, ATRQualityConfig
from core.detectors.crt.quality_tightening import CRTQualityTighteningResult
from core.settings.alert_config import AlertConfig
from core.settings.strategy_config import ProfileConfig
from core.analytics.signal_tracker import SignalTracker
from core.storage.factory import get_journal

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
        db_path: str = "data/journal.sqlite",
        journal=None,
        subscriber_db=None,
        runtime_store=None,
        monitoring=None,
        google_sheets_webhook_url: str = "",
        atr_quality_config: Optional[ATRQualityConfig] = None,
    ) -> None:
        self._config = config
        self._google_sheets_webhook_url = google_sheets_webhook_url
        self._subscriber_db = subscriber_db
        self._runtime_store = runtime_store
        self._monitoring = monitoring
        self._atr_quality_config = atr_quality_config
        self._channels: list[BaseAlertChannel] = channels or self._build_channels()
        # Deduplication cache: key → last alert UTC timestamp (seconds)
        self._dedup_cache: dict[str, float] = {}
        self.last_delivery_status = "not_attempted"
        # Keep Telegram transport eligibility separate from log/Discord writes.
        self.last_telegram_delivery_status = "not_attempted"
        self._journal = journal or get_journal(db_path)
        self.tracker = SignalTracker(
            db_path=db_path,
            runtime_store=runtime_store,
            journal=self._journal,
            subscriber_db=subscriber_db,
            google_sheets_webhook_url=google_sheets_webhook_url,
        )

    def _build_channels(self) -> list[BaseAlertChannel]:
        """Build the default channel list from config."""
        channels: list[BaseAlertChannel] = []
        if self._config.log_channel_enabled:
            channels.append(LogAlertChannel(enabled=True))
        if self._config.telegram.enabled:
            channels.append(TelegramAlertChannel(
                self._config.telegram,
                subscriber_db=self._subscriber_db,
                runtime_store=self._runtime_store,
                monitoring=self._monitoring,
                google_sheets_webhook_url=self._google_sheets_webhook_url,
            ))
        if getattr(self._config, 'discord', None) and self._config.discord.enabled:
            channels.append(DiscordChannel(self._config.discord.webhook_url))
        return channels

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def process(
        self,
        candidate: TradeCandidate,
        confidence: ConfidenceScore,
        profile_config: ProfileConfig,
        atr_quality: Optional[ATRQualityResult] = None,
        quality_tightening: Optional[CRTQualityTighteningResult] = None,
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
        self.last_telegram_delivery_status = "not_attempted"
        if not self._config.enabled:
            self.last_delivery_status = "suppressed"
            self.last_telegram_delivery_status = "suppressed"
            self._log_suppressed(candidate, confidence, "alert_engine_disabled")
            return None

        # --- Gate 1: Confidence threshold ---
        profile_threshold_passed = (
            not self._config.suppress_below_threshold
            or confidence.final_score >= profile_config.min_confidence_threshold
        )
        subscriber_threshold_bypass = False
        if not profile_threshold_passed:
            for channel in self._channels:
                if not isinstance(channel, TelegramAlertChannel):
                    continue
                if channel.has_explicit_subscriber_override(
                    timeframe=candidate.timeframe,
                    symbol=candidate.symbol,
                    profile_min_confidence=profile_config.min_confidence_threshold,
                ):
                    subscriber_threshold_bypass = True
                    break
            if not subscriber_threshold_bypass:
                self.last_delivery_status = "suppressed"
                self.last_telegram_delivery_status = "suppressed"
                self._log_suppressed(
                    candidate, confidence,
                    f"below_threshold({confidence.final_score:.1f}"
                    f"<{profile_config.min_confidence_threshold})",
                )
                return None
            logger.info(
                '{"event":"profile_threshold_bypassed_for_subscribers",'
                '"candidate_id":"%s","symbol":"%s","timeframe":"%s",'
                '"confidence":%.2f,"profile_minimum":%.2f}',
                candidate.candidate_id,
                candidate.symbol,
                candidate.timeframe,
                confidence.final_score,
                profile_config.min_confidence_threshold,
            )

        # --- Gate 2: Deduplication ---
        dedup_key = self._dedup_key(candidate)
        now_ts = datetime.now(timezone.utc).timestamp()

        if self._config.deduplication_window_seconds > 0:
            last_sent = self._dedup_cache.get(dedup_key)
            if last_sent is not None:
                elapsed = now_ts - last_sent
                if elapsed < self._config.deduplication_window_seconds:
                    self.last_delivery_status = "suppressed"
                    self.last_telegram_delivery_status = "suppressed"
                    self._log_suppressed(
                        candidate, confidence,
                        f"dedup({elapsed:.0f}s < "
                        f"{self._config.deduplication_window_seconds}s window)",
                    )
                    return None

        # --- Build AlertEvent ---
        alert = self._build_alert(
            candidate,
            confidence,
            atr_quality,
            quality_tightening,
        )

        # --- Deliver to channels ---
        delivered_count = 0
        delivery_routes: set[str] = set()
        telegram_delivery_statuses: list[str] = []
        telegram_channel_seen = False
        for channel in self._channels:
            if not channel.is_enabled:
                continue

            try:
                if not profile_threshold_passed and not isinstance(channel, TelegramAlertChannel):
                    continue
                # Below the profile floor, the admin channel must remain quiet;
                # only the per-subscriber route may evaluate explicit overrides.
                success = channel.send(alert) if profile_threshold_passed else False
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    '{"event":"alert_channel_exception","alert_id":"%s",'
                    '"channel":"%s","symbol":"%s","timeframe":"%s",'
                    '"error":"%s"}',
                    alert.alert_id,
                    getattr(channel, "name", channel.__class__.__name__),
                    alert.symbol,
                    alert.timeframe,
                    str(exc)[:160],
                )
                continue

            if isinstance(channel, TelegramAlertChannel):
                telegram_channel_seen = True
                # Subscriber delivery is independent from the admin channel.
                # An admin-chat failure must not suppress paid subscriber alerts.
                sub_deliveries = []
                try:
                    sub_deliveries = channel.broadcast_to_subscribers(
                        alert,
                        confidence_score=confidence.final_score,
                        profile_min_confidence=profile_config.min_confidence_threshold,
                        atr_quality_config=self._atr_quality_config,
                        quality_tightening=quality_tightening,
                    )
                    delivery_routes.update(
                        str(route).strip().lower()
                        for route in getattr(channel, "last_delivery_routes", set())
                        if str(route).strip()
                    )
                    # Subscriber delivery status has priority over an admin
                    # chat success: a retryable subscriber route must not be
                    # treated as fully delivered for execution eligibility.
                    telegram_delivery_statuses.append(
                        getattr(channel, "last_delivery_status", "suppressed")
                    )
                except Exception as sub_exc:
                    telegram_delivery_statuses.append("retryable")
                    logger.warning(
                        '{"event":"subscriber_broadcast_failed","alert_id":"%s","error":"%s"}',
                        alert.alert_id, str(sub_exc)[:120]
                    )

                if success or sub_deliveries:
                    delivered_count += 1
                    self.tracker.register_prediction(
                        alert_id=alert.alert_id,
                        symbol=alert.symbol,
                        timeframe=alert.timeframe,
                        direction=alert.direction,
                        target=float(alert.opposite_extreme_target),
                        midpoint=float(alert.midpoint_target),
                        sl=float(alert.invalidation_level),
                        entry_price=float((alert.entry_zone_high + alert.entry_zone_low) / 2),
                        telegram_message_id=int(success) if success else None,
                        chat_id=self._config.telegram.chat_id,
                        candle_open_time=candidate.confidence_inputs.get("sweep_open_time", 0.0),
                        subscriber_deliveries=sub_deliveries,
                        feedback_metadata=self._feedback_metadata(
                            alert,
                            candidate,
                            delivery_routes=delivery_routes,
                        ),
                    )
            elif success:
                delivered_count += 1

        if not profile_threshold_passed and delivered_count == 0:
            self.last_delivery_status = "suppressed"
            self.last_telegram_delivery_status = "suppressed"
            self._log_suppressed(
                candidate,
                confidence,
                f"below_threshold({confidence.final_score:.1f}"
                f"<{profile_config.min_confidence_threshold})",
            )
            return None

        telegram_delivered = "delivered" in telegram_delivery_statuses
        telegram_retryable = "retryable" in telegram_delivery_statuses
        if telegram_delivered:
            self.last_telegram_delivery_status = "delivered"
        elif telegram_retryable:
            self.last_telegram_delivery_status = "retryable"
        elif telegram_channel_seen:
            self.last_telegram_delivery_status = "suppressed"
        else:
            self.last_telegram_delivery_status = "unavailable"
        if telegram_retryable and not telegram_delivered:
            self.last_delivery_status = "retryable"
        elif delivered_count > 0:
            self.last_delivery_status = "delivered"
        else:
            self.last_delivery_status = "suppressed"

        # --- Update dedup cache only after subscriber delivery is settled ---
        # An admin-chat success must not consume the key when an eligible
        # subscriber transport failed. Otherwise a transient Telegram/API
        # failure would suppress the subscriber retry on the next cycle.
        if self.last_delivery_status == "retryable":
            logger.warning(
                '{"event":"alert_delivery_retryable","alert_id":"%s",'
                '"candidate_id":"%s","symbol":"%s","timeframe":"%s",'
                '"reason":"subscriber_delivery_failed"}',
                alert.alert_id,
                candidate.candidate_id,
                candidate.symbol,
                candidate.timeframe,
            )
        elif delivered_count > 0:
            self._dedup_cache[dedup_key] = now_ts
        else:
            logger.warning(
                '{"event":"alert_delivery_retryable","alert_id":"%s",'
                '"candidate_id":"%s","symbol":"%s","timeframe":"%s",'
                '"reason":"no_channel_delivery_succeeded"}',
                alert.alert_id,
                candidate.candidate_id,
                candidate.symbol,
                candidate.timeframe,
            )

        # --- Write every constructed signal to the Passport ---
        # A channel failure or premium-route suppression must not erase the
        # quality evidence for the underlying detected event.
        try:
            from decimal import Decimal
            breakdown = {
                f.factor_name: ("UNKNOWN" if f.is_unknown else f"{f.score:.1f}/{f.max_score:.1f}")
                for f in confidence.factor_breakdown
            }
            entry_price = (
                candidate.entry_model.entry_zone_high + candidate.entry_model.entry_zone_low
            ) / 2 if hasattr(candidate.entry_model, 'entry_zone_high') else Decimal("0")
            self._journal.add_signal_entry(
                record_id=alert.alert_id,
                candidate_id=candidate.candidate_id,
                symbol=candidate.symbol,
                timeframe=candidate.timeframe,
                profile=candidate.profile,
                direction=candidate.direction,
                confidence_score=confidence.final_score,
                confidence_breakdown=breakdown,
                entry_price=entry_price,
                sl_price=candidate.invalidation_model.invalidation_level,
                tp_price=candidate.target_model.opposite_extreme_target,
                atr_quality=alert.atr_quality,
                key_level_evaluation=alert.key_level_evaluation,
                volume_profiles=(
                    alert.volume_profiles
                    or ((alert.volume_profile,) if alert.volume_profile is not None else None)
                ),
            )
        except Exception as jexc:
            logger.warning('{"event":"journal_signal_write_failed","error":"%s"}', str(jexc)[:120])

        try:
            from core.analytics.google_sheets_reporting import report_signal_formed
            report_signal_formed(
                alert,
                webhook_url=self._google_sheets_webhook_url,
            )
        except Exception as reporting_exc:  # noqa: BLE001
            logger.warning(
                '{"event":"google_sheets_signal_formed_failed",'
                '"alert_id":"%s","error":"%s"}',
                alert.alert_id,
                str(reporting_exc)[:160],
            )

        logger.info(
            '{"event":"alert_dispatched","alert_id":"%s","candidate_id":"%s",'
            '"symbol":"%s","timeframe":"%s","profile":"%s","direction":"%s",'
            '"confidence":%.2f,"channels_delivered":%d}',
            alert.alert_id, candidate.candidate_id,
            candidate.symbol, candidate.timeframe, candidate.profile,
            candidate.direction, confidence.final_score, delivered_count,
        )

        return alert

    def register_fvg_prediction(
        self,
        candidate: TradeCandidate,
        confidence: ConfidenceScore,
        atr_quality: Optional[ATRQualityResult] = None,
        quality_tightening: Optional[CRTQualityTighteningResult] = None,
    ) -> AlertEvent:
        """Register an FVG watch in the Signal Passport/outcome tracker.

        FVG Alert 1 is delivered by the Telegram-specific two-alert flow, so
        it does not pass through ``process``. This helper preserves the same
        immutable alert fields and analytics registration contract without
        re-sending the ordinary CRT alert.
        """
        alert = self._build_alert(
            candidate,
            confidence,
            atr_quality,
            quality_tightening,
        )
        self.tracker.register_prediction(
            alert_id=alert.alert_id,
            symbol=alert.symbol,
            timeframe=alert.timeframe,
            direction=alert.direction,
            target=float(alert.opposite_extreme_target),
            midpoint=float(alert.midpoint_target),
            sl=float(alert.invalidation_level),
            entry_price=float((alert.entry_zone_high + alert.entry_zone_low) / 2),
            telegram_message_id=None,
            chat_id=self._config.telegram.chat_id,
            candle_open_time=candidate.confidence_inputs.get("sweep_open_time", 0.0),
            subscriber_deliveries=[],
            feedback_metadata=self._feedback_metadata(alert, candidate),
        )
        try:
            self._journal.add_signal_entry(
                record_id=alert.alert_id,
                candidate_id=candidate.candidate_id,
                symbol=candidate.symbol,
                timeframe=candidate.timeframe,
                profile=candidate.profile,
                direction=candidate.direction,
                confidence_score=confidence.final_score,
                confidence_breakdown=alert.confidence_breakdown,
                entry_price=(alert.entry_zone_high + alert.entry_zone_low) / 2,
                sl_price=alert.invalidation_level,
                tp_price=alert.opposite_extreme_target,
                atr_quality=alert.atr_quality,
                key_level_evaluation=alert.key_level_evaluation,
                volume_profiles=(
                    alert.volume_profiles
                    or ((alert.volume_profile,) if alert.volume_profile is not None else None)
                ),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                '{"event":"fvg_passport_write_failed","alert_id":"%s",'
                '"candidate_id":"%s","error":"%s"}',
                alert.alert_id,
                candidate.candidate_id,
                str(exc)[:120],
            )
        logger.info(
            '{"event":"fvg_passport_registered","alert_id":"%s",'
            '"candidate_id":"%s","symbol":"%s","timeframe":"%s"}',
            alert.alert_id,
            candidate.candidate_id,
            candidate.symbol,
            candidate.timeframe,
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
        atr_quality: Optional[ATRQualityResult] = None,
        quality_tightening: Optional[CRTQualityTighteningResult] = None,
    ) -> AlertEvent:
        """Construct an AlertEvent from a candidate and its confidence score."""
        metadata = candidate.confidence_inputs
        raw_profiles = metadata.get("_volume_profile_results")
        if isinstance(raw_profiles, dict):
            volume_profiles = tuple(raw_profiles.values())
        elif isinstance(raw_profiles, (tuple, list)):
            volume_profiles = tuple(raw_profiles)
        else:
            legacy_profile = metadata.get("_volume_profile_result")
            volume_profiles = (legacy_profile,) if legacy_profile is not None else ()
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
            atr_quality=atr_quality,
            parent_at_htf_zone=metadata.get("parent_at_htf_zone"),
            key_level_evaluation=metadata.get("key_level_evaluation"),
            quality_tightening=quality_tightening,
            volume_profile=volume_profiles[0] if volume_profiles else None,
            volume_profiles=volume_profiles,
            volume_profile_confluence=metadata.get("volume_profile_confluence"),
            volume_profile_matches=tuple(metadata.get("_volume_profile_matches") or ()),
        )

    @staticmethod
    def _feedback_metadata(
        alert: AlertEvent,
        candidate: TradeCandidate,
        delivery_routes: Optional[set[str]] = None,
    ) -> dict:
        """Return stable setup metadata for resolved feedback records."""
        atr = alert.atr_quality
        key_level = alert.key_level_evaluation or {}
        nested = candidate.confidence_inputs.get("nested_crt_routing") or {}
        htf_momentum = candidate.confidence_inputs.get("htf_momentum") or {}
        htf_shadow = candidate.confidence_inputs.get("htf_momentum_shadow") or {}
        quality = alert.quality_tightening
        return {
            "signal_id": alert.alert_id,
            "created_at": alert.created_at.isoformat(),
            "profile": alert.profile,
            "direction": alert.direction,
            "classification": alert.classification,
            "confidence_score": round(alert.confidence_score, 2),
            "strategy_name": candidate.strategy_name,
            "atr_status": atr.status if atr is not None else "UNKNOWN",
            "atr_grade": atr.grade if atr is not None else "",
            "parent_atr_ratio": (
                str(atr.parent_atr_ratio) if atr and atr.parent_atr_ratio is not None else ""
            ),
            "sweep_atr_ratio": (
                str(atr.sweep_atr_ratio) if atr and atr.sweep_atr_ratio is not None else ""
            ),
            "key_level_status": key_level.get("status", ""),
            "key_level_zone": key_level.get(
                "parent_at_htf_zone", alert.parent_at_htf_zone
            ),
            "key_level_match_count": key_level.get("matched_level_count", ""),
            "quality_tightening_status": quality.status if quality else "UNKNOWN",
            "quality_tightening_would_suppress": (
                quality.would_suppress if quality else True
            ),
            "quality_tightening_reasons": (
                ",".join(quality.reasons) if quality else "quality_metadata_missing"
            ),
            "nested_route": nested.get("reason_code", ""),
            "nested_contained": nested.get("contained", ""),
            "controller_event_id": nested.get("controller_event_id", ""),
            "htf_controller_timeframe": htf_momentum.get("controller_timeframe", ""),
            "htf_bias": htf_momentum.get("bias", ""),
            "htf_relationship": htf_momentum.get("relationship", ""),
            "htf_run_length": htf_momentum.get("run_length", ""),
            "htf_strong_run": htf_momentum.get("strong_run", ""),
            "htf_base_score": htf_shadow.get("base_score", ""),
            "htf_proposed_score": htf_shadow.get("proposed_score", ""),
            "htf_adjustment": htf_shadow.get("adjustment", ""),
            "htf_configuration_snapshot_id": htf_shadow.get(
                "configuration_snapshot_id", ""
            ),
            "delivery_routes": ",".join(sorted(delivery_routes or set())),
        }

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
        if self._monitoring is not None:
            self._monitoring.observe_alert_suppressed(reason)
