"""Capture immutable replay evidence from the live alert pipeline.

This service records the exact closed MT5 candles already consumed by the
detector. It does not detect CRTs, rescore candidates, or fetch replacement
market data. Capture failures are isolated from alert delivery because replay
is an analytics capability, not a delivery gate.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Mapping, Protocol

from core.models.alert_event import AlertEvent
from core.models.candle import CandleSequence
from core.models.detector_output import DetectorOutput
from core.models.signal_replay import (
    ReplayAvailability,
    ReplayCandle,
    ReplayCandleRole,
    ReplayLifecycleStatus,
    ReplayOutcome,
    ReplaySnapshot,
)


logger = logging.getLogger("atlax.analytics.signal_replay")


class ReplaySnapshotRepository(Protocol):
    """Minimal repository contract required by the capture service."""

    def upsert(self, snapshot: ReplaySnapshot) -> ReplaySnapshot:
        ...


class ReplayCaptureRejected(ValueError):
    """Raised internally when the live event cannot form a valid snapshot."""


def _utc(value: datetime, name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ReplayCaptureRejected(f"{name}_not_utc")
    return value.astimezone(timezone.utc)


def _find_unique_index(sequence: CandleSequence, expected: Any, name: str) -> int:
    matches = [
        index
        for index, candle in enumerate(sequence.candles)
        if candle == expected
    ]
    if len(matches) != 1:
        raise ReplayCaptureRejected(f"{name}_not_unique_in_closed_sequence")
    return matches[0]


def _optional_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError, ArithmeticError):
        return None


class ReplayCaptureService:
    """Persist one replay snapshot for each accepted alert event."""

    def __init__(
        self,
        repository: ReplaySnapshotRepository,
        *,
        enabled: bool = True,
        context_bars_before_parent: int = 30,
        outcome_forward_bars: int = 50,
    ) -> None:
        if context_bars_before_parent < 0:
            raise ValueError("context_bars_before_parent must be >= 0")
        if outcome_forward_bars < 0:
            raise ValueError("outcome_forward_bars must be >= 0")
        self._repository = repository
        self._enabled = bool(enabled)
        self._context_bars_before_parent = context_bars_before_parent
        self._outcome_forward_bars = outcome_forward_bars

    @property
    def enabled(self) -> bool:
        return self._enabled

    def capture(
        self,
        alert: AlertEvent | Any | None,
        detector_output: DetectorOutput,
        closed_sequence: CandleSequence | None,
        *,
        rule_context: Mapping[str, Any] | None = None,
        higher_timeframe_sequence: CandleSequence | None = None,
        higher_timeframe_sequences: tuple[CandleSequence, ...] = (),
    ) -> ReplaySnapshot | None:
        """Capture source-timeframe formation evidence without blocking alerts."""
        if not self._enabled:
            return None
        if alert is None:
            logger.debug(
                '{"event":"signal_replay_capture_skipped",'
                '"reason":"alert_not_delivered"}'
            )
            return None

        try:
            snapshot = self._build_snapshot(
                alert,
                detector_output,
                closed_sequence,
                rule_context=rule_context,
                higher_timeframe_sequence=higher_timeframe_sequence,
                higher_timeframe_sequences=higher_timeframe_sequences,
            )
            stored = self._repository.upsert(snapshot)
            logger.info(
                '{"event":"signal_replay_capture_succeeded",'
                '"signal_id":"%s","snapshot_id":"%s",'
                '"symbol":"%s","timeframe":"%s",'
                '"context_bars":%d,"formation_candles":%d}',
                stored.signal_id,
                stored.snapshot_id,
                stored.symbol,
                stored.source_timeframe,
                stored.context_bars_before_parent,
                len(stored.formation_candles),
            )
            return stored
        except ReplayCaptureRejected as exc:
            logger.info(
                '{"event":"signal_replay_capture_skipped",'
                '"signal_id":"%s","reason":"%s"}',
                getattr(alert, "alert_id", ""),
                str(exc)[:120],
            )
        except Exception as exc:  # noqa: BLE001 - analytics must not block alerts
            logger.error(
                '{"event":"signal_replay_capture_failed",'
                '"signal_id":"%s","reason":"%s"}',
                getattr(alert, "alert_id", ""),
                str(exc)[:160],
            )
        return None

    def _build_snapshot(
        self,
        alert: AlertEvent | Any,
        detector_output: DetectorOutput,
        closed_sequence: CandleSequence | None,
        *,
        rule_context: Mapping[str, Any] | None,
        higher_timeframe_sequence: CandleSequence | None,
        higher_timeframe_sequences: tuple[CandleSequence, ...],
    ) -> ReplaySnapshot:
        if closed_sequence is None:
            raise ReplayCaptureRejected("closed_sequence_unavailable")
        metadata = getattr(detector_output, "metadata", None)
        if metadata is None:
            raise ReplayCaptureRejected("detector_metadata_unavailable")
        confirmation = getattr(metadata, "confirmation_candle", None)
        if confirmation is None:
            raise ReplayCaptureRejected("confirmation_candle_missing")

        parent = metadata.parent_candle
        sweep = metadata.sweep_candle
        parent_index = _find_unique_index(closed_sequence, parent, "parent_candle")
        sweep_index = _find_unique_index(closed_sequence, sweep, "sweep_candle")
        confirmation_index = _find_unique_index(
            closed_sequence,
            confirmation,
            "confirmation_candle",
        )
        if (sweep_index, confirmation_index) != (parent_index + 1, parent_index + 2):
            raise ReplayCaptureRejected("crt_candles_not_consecutive")
        if parent_index < self._context_bars_before_parent:
            raise ReplayCaptureRejected("insufficient_context_candles")

        source_candles = closed_sequence.candles
        selected = source_candles[
            parent_index - self._context_bars_before_parent : confirmation_index + 1
        ]
        if any(not candle.is_closed for candle in selected):
            raise ReplayCaptureRejected("formation_contains_open_candle")
        if any(
            candle.symbol != closed_sequence.symbol
            or candle.timeframe != closed_sequence.timeframe
            for candle in selected
        ):
            raise ReplayCaptureRejected("formation_source_identity_mismatch")

        formation_candles = tuple(
            ReplayCandle(
                candle,
                (
                    ReplayCandleRole.PARENT
                    if index == self._context_bars_before_parent
                    else ReplayCandleRole.SWEEP
                    if index == self._context_bars_before_parent + 1
                    else ReplayCandleRole.CONFIRMATION
                    if index == self._context_bars_before_parent + 2
                    else ReplayCandleRole.CONTEXT
                ),
            )
            for index, candle in enumerate(selected)
        )
        created_at = _utc(alert.created_at, "alert_created_at")
        formation_cutoff_at = _utc(confirmation.close_time, "formation_cutoff_at")
        captured_at = datetime.now(timezone.utc)
        if created_at < formation_cutoff_at:
            raise ReplayCaptureRejected("alert_created_before_formation_cutoff")

        context = self._rule_context(alert, detector_output, rule_context)
        source_sequences = list(higher_timeframe_sequences)
        if higher_timeframe_sequence is not None:
            source_sequences.insert(0, higher_timeframe_sequence)
        higher_timeframe_candles = tuple(
            ReplayCandle(candle, ReplayCandleRole.HTF_CONTEXT)
            for sequence in source_sequences
            for candle in sequence.candles
            if candle.is_closed
            and candle.symbol == closed_sequence.symbol
            and candle.close_time <= formation_cutoff_at
        )
        return ReplaySnapshot(
            snapshot_id=f"{alert.alert_id}:replay:v1",
            signal_id=str(alert.alert_id),
            snapshot_version="1.0",
            symbol=closed_sequence.symbol,
            source_timeframe=closed_sequence.timeframe,
            source="MT5",
            created_at=created_at,
            captured_at=captured_at,
            formation_cutoff_at=formation_cutoff_at,
            context_bars_before_parent=self._context_bars_before_parent,
            outcome_forward_bars=self._outcome_forward_bars,
            formation_candles=formation_candles,
            higher_timeframe_candles=higher_timeframe_candles,
            outcome_candles=(),
            rule_context=context,
            availability=ReplayAvailability.AVAILABLE,
            lifecycle_status=ReplayLifecycleStatus.OPEN,
            final_outcome=ReplayOutcome.UNKNOWN,
        )

    @staticmethod
    def _rule_context(
        alert: AlertEvent | Any,
        detector_output: DetectorOutput,
        supplied: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        """Keep only deterministic, approved metadata in the immutable hash."""
        atr = getattr(alert, "atr_quality", None)
        key_level = getattr(alert, "key_level_evaluation", None) or {}
        quality = getattr(alert, "quality_tightening", None)
        context: dict[str, Any] = {
            "detector": str(getattr(detector_output, "detector", "")),
            "classification": str(getattr(alert, "classification", "UNKNOWN")),
            "direction": str(getattr(alert, "direction", "UNKNOWN")),
            "profile": str(getattr(alert, "profile", "")),
            "confidence_score": str(getattr(alert, "confidence_score", "")),
            "atr_status": str(getattr(atr, "status", "UNKNOWN")),
            "atr_grade": str(getattr(atr, "grade", "")),
            "parent_atr_ratio": (
                str(getattr(atr, "parent_atr_ratio", "")) if atr else ""
            ),
            "sweep_atr_ratio": (
                str(getattr(atr, "sweep_atr_ratio", "")) if atr else ""
            ),
            "key_level_status": str(key_level.get("status", "UNKNOWN")),
            "key_level_zone": key_level.get(
                "parent_at_htf_zone", getattr(alert, "parent_at_htf_zone", None)
            ),
            "key_level_match_count": _optional_int(
                key_level.get("matched_level_count")
            ),
            "quality_tightening_status": str(
                getattr(quality, "status", "UNKNOWN")
            ),
            "quality_tightening_would_suppress": (
                bool(getattr(quality, "would_suppress")) if quality else None
            ),
            "htf_context_status": "NOT_CAPTURED_PHASE_3",
            "entry_zone_high": str(getattr(alert, "entry_zone_high", "")),
            "entry_zone_low": str(getattr(alert, "entry_zone_low", "")),
            "invalidation_level": str(getattr(alert, "invalidation_level", "")),
            "midpoint_target": str(getattr(alert, "midpoint_target", "")),
            "opposite_extreme_target": str(
                getattr(alert, "opposite_extreme_target", "")
            ),
        }
        if supplied:
            # The caller may add already-sanitized, reviewable provenance. Do
            # not accept arbitrary nested runtime objects into the snapshot.
            for key in (
                "nested_route",
                "nested_contained",
                "controller_event_id",
                "htf_momentum_relationship",
                "htf_momentum_bias",
            ):
                if key in supplied and isinstance(supplied[key], (str, int, bool)):
                    context[key] = supplied[key]
            recipients = supplied.get("authorized_chat_ids")
            if isinstance(recipients, (list, tuple)):
                context["authorized_chat_ids"] = sorted(
                    {
                        str(item).strip()
                        for item in recipients
                        if str(item).strip()
                    }
                )
            routes = supplied.get("delivery_routes")
            if isinstance(routes, (list, tuple)):
                context["delivery_routes"] = sorted(
                    {
                        str(item).strip().lower()
                        for item in routes
                        if str(item).strip()
                    }
                )
        return context
