"""Append closed replay candles and synchronize Passport outcomes.

The Signal Passport journal is authoritative for WIN, LOSS, and expiry. This
module only adds the same closed MT5 candles seen by the scanner and mirrors a
validated journal state into the replay record. It never derives a win or loss
from chart movement itself.
"""

from __future__ import annotations

import logging
from dataclasses import replace
from datetime import datetime, timezone
from typing import Any, Protocol

from core.models.candle import CandleSequence
from core.models.signal_replay import (
    ReplayCandle,
    ReplayCandleRole,
    ReplayLifecycleStatus,
    ReplayOutcome,
    ReplaySnapshot,
)


logger = logging.getLogger("atlax.analytics.signal_replay")


class ReplayOutcomeRepository(Protocol):
    def list(self) -> list[ReplaySnapshot]:
        ...

    def update_outcome(self, snapshot: ReplaySnapshot) -> ReplaySnapshot:
        ...


class JournalOutcomeReader(Protocol):
    def get_signal_outcome(self, record_id: str) -> dict[str, Any] | None:
        ...


def _parse_utc(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


class ReplayOutcomeService:
    """Synchronize replay outcome evidence for one closed scanner sequence."""

    _TERMINAL_STATUSES = {
        ReplayLifecycleStatus.RESOLVED,
        ReplayLifecycleStatus.EXPIRED,
    }

    def __init__(
        self,
        repository: ReplayOutcomeRepository,
        journal: JournalOutcomeReader,
        *,
        enabled: bool = True,
    ) -> None:
        self._repository = repository
        self._journal = journal
        self._enabled = bool(enabled)

    def sync_sequence(self, closed_sequence: CandleSequence) -> int:
        """Append eligible candles and apply authoritative journal outcomes."""
        if not self._enabled:
            return 0
        try:
            snapshots = self._repository.list()
        except Exception as exc:  # noqa: BLE001 - analytics must not block scanning
            logger.error(
                '{"event":"signal_replay_outcome_list_failed",'
                '"reason":"%s"}',
                str(exc)[:160],
            )
            return 0

        updated_count = 0
        for snapshot in snapshots:
            if (
                snapshot.symbol.upper() != closed_sequence.symbol.upper()
                or snapshot.source_timeframe.upper()
                != closed_sequence.timeframe.upper()
            ):
                continue
            if self._sync_snapshot(snapshot, closed_sequence):
                updated_count += 1
        return updated_count

    def _sync_snapshot(
        self,
        snapshot: ReplaySnapshot,
        closed_sequence: CandleSequence,
    ) -> bool:
        try:
            outcome_state = self._journal.get_signal_outcome(snapshot.signal_id)
        except Exception as exc:  # noqa: BLE001 - unknown is safer than guessing
            logger.warning(
                '{"event":"signal_replay_outcome_read_failed",'
                '"signal_id":"%s","reason":"%s"}',
                snapshot.signal_id,
                str(exc)[:160],
            )
            outcome_state = None

        if outcome_state is not None:
            state_symbol = str(outcome_state.get("symbol", "")).upper()
            state_timeframe = str(outcome_state.get("timeframe", "")).upper()
            if state_symbol and state_symbol != snapshot.symbol.upper():
                return self._mark_inconsistent(snapshot, "journal_symbol_mismatch")
            if state_timeframe and state_timeframe != snapshot.source_timeframe.upper():
                return self._mark_inconsistent(snapshot, "journal_timeframe_mismatch")

        if snapshot.lifecycle_status in self._TERMINAL_STATUSES:
            self._log_terminal_conflict(snapshot, outcome_state)
            return False

        outcome_candles = self._append_candles(snapshot, closed_sequence)
        lifecycle, final_outcome, outcome_at, invalidation_reason = (
            self._validated_journal_state(snapshot, outcome_state)
        )

        if (
            outcome_candles == snapshot.outcome_candles
            and lifecycle == snapshot.lifecycle_status
            and final_outcome == snapshot.final_outcome
            and outcome_at == snapshot.outcome_at
            and invalidation_reason == snapshot.invalidation_reason
        ):
            return False

        try:
            updated = replace(
                snapshot,
                outcome_candles=outcome_candles,
                lifecycle_status=lifecycle,
                final_outcome=final_outcome,
                outcome_at=outcome_at,
                invalidation_reason=invalidation_reason,
            )
            self._repository.update_outcome(updated)
        except Exception as exc:  # noqa: BLE001 - analytics must not block scanning
            logger.error(
                '{"event":"signal_replay_outcome_update_failed",'
                '"signal_id":"%s","reason":"%s"}',
                snapshot.signal_id,
                str(exc)[:160],
            )
            return False

        logger.info(
            '{"event":"signal_replay_outcome_updated",'
            '"signal_id":"%s","status":"%s","outcome":"%s",'
            '"outcome_candles":%d}',
            updated.signal_id,
            updated.lifecycle_status.value,
            updated.final_outcome.value,
            len(updated.outcome_candles),
        )
        return True

    @staticmethod
    def _append_candles(
        snapshot: ReplaySnapshot,
        closed_sequence: CandleSequence,
    ) -> tuple[ReplayCandle, ...]:
        existing = list(snapshot.outcome_candles)
        existing_open_times = {item.candle.open_time for item in existing}
        remaining = snapshot.outcome_forward_bars - len(existing)
        if remaining <= 0:
            return tuple(existing)

        additions = []
        for candle in closed_sequence.candles:
            if not candle.is_closed:
                continue
            if candle.symbol != snapshot.symbol or candle.timeframe != snapshot.source_timeframe:
                continue
            if candle.close_time <= snapshot.formation_cutoff_at:
                continue
            if candle.open_time in existing_open_times:
                continue
            additions.append(ReplayCandle(candle, ReplayCandleRole.OUTCOME))
            existing_open_times.add(candle.open_time)
            if len(additions) >= remaining:
                break
        return tuple(existing + additions)

    @classmethod
    def _validated_journal_state(
        cls,
        snapshot: ReplaySnapshot,
        state: dict[str, Any] | None,
    ) -> tuple[
        ReplayLifecycleStatus,
        ReplayOutcome,
        datetime | None,
        str,
    ]:
        if not state:
            return (
                snapshot.lifecycle_status,
                snapshot.final_outcome,
                snapshot.outcome_at,
                snapshot.invalidation_reason,
            )

        status = str(state.get("signal_status", "")).upper()
        final = str(state.get("final_outcome", "")).upper()
        reason = str(state.get("exit_reason", "")).lower()
        outcome_at = _parse_utc(state.get("outcome_at"))

        if reason == "tp2_hit" and status == "RESOLVED" and final == "WIN":
            if outcome_at is None or outcome_at < snapshot.formation_cutoff_at:
                return ReplayLifecycleStatus.UNKNOWN, ReplayOutcome.UNKNOWN, None, ""
            return ReplayLifecycleStatus.RESOLVED, ReplayOutcome.WIN, outcome_at, ""
        if reason == "sl_hit" and status == "RESOLVED" and final == "LOSS":
            if outcome_at is None or outcome_at < snapshot.formation_cutoff_at:
                return ReplayLifecycleStatus.UNKNOWN, ReplayOutcome.UNKNOWN, None, ""
            return (
                ReplayLifecycleStatus.RESOLVED,
                ReplayOutcome.LOSS,
                outcome_at,
                str(state.get("invalidation_reason") or "stop_loss_hit"),
            )
        if reason == "signal_expired" and status == "EXPIRED" and final == "UNKNOWN":
            return ReplayLifecycleStatus.EXPIRED, ReplayOutcome.UNKNOWN, outcome_at, ""

        if status in {"RESOLVED", "EXPIRED"} or final in {"WIN", "LOSS"}:
            return ReplayLifecycleStatus.UNKNOWN, ReplayOutcome.UNKNOWN, None, ""
        return (
            snapshot.lifecycle_status,
            snapshot.final_outcome,
            snapshot.outcome_at,
            snapshot.invalidation_reason,
        )

    def _mark_inconsistent(self, snapshot: ReplaySnapshot, reason: str) -> bool:
        if snapshot.lifecycle_status is ReplayLifecycleStatus.UNKNOWN:
            return False
        try:
            updated = replace(
                snapshot,
                lifecycle_status=ReplayLifecycleStatus.UNKNOWN,
                final_outcome=ReplayOutcome.UNKNOWN,
                outcome_at=None,
                invalidation_reason="",
            )
            self._repository.update_outcome(updated)
        except Exception as exc:  # noqa: BLE001
            logger.error(
                '{"event":"signal_replay_outcome_consistency_failed",'
                '"signal_id":"%s","reason":"%s","error":"%s"}',
                snapshot.signal_id,
                reason,
                str(exc)[:120],
            )
            return False
        logger.warning(
            '{"event":"signal_replay_outcome_unknown",'
            '"signal_id":"%s","reason":"%s"}',
            snapshot.signal_id,
            reason,
        )
        return True

    @staticmethod
    def _log_terminal_conflict(
        snapshot: ReplaySnapshot,
        state: dict[str, Any] | None,
    ) -> None:
        if not state:
            return
        journal_final = str(state.get("final_outcome", "UNKNOWN")).upper()
        if snapshot.final_outcome.value == journal_final:
            return
        logger.error(
            '{"event":"signal_replay_terminal_conflict",'
            '"signal_id":"%s","replay_outcome":"%s",'
            '"journal_outcome":"%s"}',
            snapshot.signal_id,
            snapshot.final_outcome.value,
            journal_final,
        )
