"""
Auditable shadow-mode counters for the Volume Profile Confluence feature.

Follows the same persistent-ledger pattern as ``KeyLevelShadowLedger``.
Persists all counters to ``runtime_store`` so that accumulated statistics
survive scanner restarts and can be inspected via MCP tools or analytics.

Shadow observations must never change Telegram delivery, subscriber
routing, approval, risk, or execution. They exist solely for
post-hoc analysis of how Volume Profile confluence *would have*
affected live signals.

Authority:
    docs/40_VOLUME_PROFILE_CONFLUENCE_PLAN.md §7.1 (Shadow Stage)
    docs/40_VOLUME_PROFILE_CONFLUENCE_PLAN.md §9 Phase 4
    docs/decisions/ADR-017.md
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

from core.models.volume_profile_result import VolumeProfileResult, VolumeProfileStatus

logger = logging.getLogger("atlax.volume_profile_shadow")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _below_threshold(
    score: Optional[float],
    threshold: Optional[float],
) -> Optional[bool]:
    if score is None or threshold is None:
        return None
    try:
        return float(score) < float(threshold)
    except (TypeError, ValueError):
        return None


def _empty_context() -> dict[str, int]:
    return {
        "evaluations": 0,
        "valid": 0,
        "unknown": 0,
        "confluence_true": 0,
        "confluence_false": 0,
        "matched_level_count": 0,
        "confidence_samples": 0,
        "current_below_threshold": 0,
        "without_below_threshold": 0,
        "threshold_crossings": 0,
    }


def _empty_state() -> dict[str, Any]:
    return {
        "version": 1,
        "updated_at": None,
        "evaluations": 0,
        "status_counts": {"VALID": 0, "UNKNOWN": 0},
        "confluence_counts": {"true": 0, "false": 0, "unknown": 0},
        "matched_level_count": 0,
        "by_context": {},
        "by_period": {},
        "confidence": {
            "samples": 0,
            "current_total": 0.0,
            "if_true_total": 0.0,
            "if_false_total": 0.0,
            "without_total": 0.0,
            "if_true_samples": 0,
            "if_false_samples": 0,
            "without_samples": 0,
            "current_below_threshold": 0,
            "without_below_threshold": 0,
            "threshold_crossings": 0,
        },
    }


# ---------------------------------------------------------------------------
# Shadow Ledger
# ---------------------------------------------------------------------------


class VolumeProfileShadowLedger:
    """Persist Volume Profile shadow observations without affecting trading.

    Two observation methods track different aspects of the shadow:

    - ``observe_evaluation`` — records each profile evaluation result
      (VALID/UNKNOWN, match count, period breakdown).
    - ``observe_confidence`` — records counterfactual confidence scores
      showing how the VP factor *would have* changed the live score.

    All state is persisted through ``runtime_store`` so statistics
    survive scanner restarts.
    """

    STATE_KEY = "volume_profile.shadow_stats"

    def __init__(self, runtime_store: Any) -> None:
        self._runtime_store = runtime_store
        self._state = self._load()

    # ------------------------------------------------------------------
    # Evaluation observation
    # ------------------------------------------------------------------

    def observe_evaluation(
        self,
        result: VolumeProfileResult,
        *,
        matched_level_count: int = 0,
    ) -> dict[str, Any]:
        """Record one Volume Profile evaluation and return the full report.

        Args:
            result: The VolumeProfileResult from the calculator.
            matched_level_count: Number of VP levels that matched the
                parent candle in the Key-Level Evaluator (0 when UNKNOWN
                or no match).

        Returns:
            The current cumulative shadow report (JSON-safe dict).
        """
        state = self._state
        status = result.status.value
        state["evaluations"] += 1
        state["status_counts"][status] = state["status_counts"].get(status, 0) + 1
        state["matched_level_count"] += matched_level_count

        # Confluence classification
        if result.status == VolumeProfileStatus.UNKNOWN:
            confluence_key = "unknown"
        elif matched_level_count > 0:
            confluence_key = "true"
        else:
            confluence_key = "false"
        state["confluence_counts"][confluence_key] += 1

        # By-context (symbol|timeframe)
        context_key = f"{result.symbol}|{result.source_timeframe}"
        context = state["by_context"].setdefault(context_key, _empty_context())
        context["evaluations"] += 1
        context[status.lower()] += 1
        if confluence_key == "true":
            context["confluence_true"] += 1
        elif confluence_key == "false":
            context["confluence_false"] += 1
        context["matched_level_count"] += matched_level_count

        # By-period (PREVIOUS_DAY / PREVIOUS_WEEK)
        period_key = result.period_label
        period = state["by_period"].setdefault(period_key, {
            "evaluations": 0,
            "valid": 0,
            "unknown": 0,
            "confluence_true": 0,
            "confluence_false": 0,
        })
        period["evaluations"] += 1
        period[status.lower()] += 1
        if confluence_key == "true":
            period["confluence_true"] += 1
        elif confluence_key == "false":
            period["confluence_false"] += 1

        self._touch_and_save()

        logger.info(
            '{"event":"volume_profile_shadow_eval","symbol":"%s",'
            '"period":"%s","status":"%s","confluence":"%s",'
            '"matched_levels":%d,"snapshot_id":"%s"}',
            result.symbol,
            result.period_label,
            status,
            confluence_key,
            matched_level_count,
            result.configuration_snapshot_id,
        )

        return self.report()

    # ------------------------------------------------------------------
    # Counterfactual confidence observation
    # ------------------------------------------------------------------

    def observe_confidence(
        self,
        *,
        result: VolumeProfileResult,
        current_score: float,
        score_if_true: Optional[float],
        score_if_false: Optional[float],
        score_without: Optional[float] = None,
        minimum_threshold: Optional[float] = None,
    ) -> dict[str, Any]:
        """Record live and counterfactual scores for one candidate/profile.

        Follows the same pattern as ``KeyLevelShadowLedger.observe_confidence``:
        - ``current_score``: The actual live confidence score.
        - ``score_if_true``: Score if VP confluence factor were True.
        - ``score_if_false``: Score if VP confluence factor were False.
        - ``score_without``: Score if VP factor were omitted (None/UNKNOWN).
        - ``minimum_threshold``: The profile's minimum confidence threshold.

        Tracks threshold crossings — cases where the presence or absence
        of the VP factor would have changed whether the signal crosses
        the confidence threshold.

        Returns:
            The current cumulative shadow report (JSON-safe dict).
        """
        confidence = self._state["confidence"]
        confidence["samples"] += 1
        confidence["current_total"] += float(current_score)

        if score_if_true is not None:
            confidence["if_true_samples"] += 1
            confidence["if_true_total"] += float(score_if_true)
        if score_if_false is not None:
            confidence["if_false_samples"] += 1
            confidence["if_false_total"] += float(score_if_false)
        if score_without is not None:
            confidence["without_samples"] += 1
            confidence["without_total"] += float(score_without)

        current_below = _below_threshold(current_score, minimum_threshold)
        without_below = _below_threshold(score_without, minimum_threshold)
        if current_below is True:
            confidence["current_below_threshold"] += 1
        if without_below is True:
            confidence["without_below_threshold"] += 1
        if (
            current_below is not None
            and without_below is not None
            and current_below != without_below
        ):
            confidence["threshold_crossings"] += 1

        # Per-context confidence tracking
        context_key = f"{result.symbol}|{result.source_timeframe}"
        context = self._state["by_context"].setdefault(context_key, _empty_context())
        context["confidence_samples"] += 1
        if current_below is True:
            context["current_below_threshold"] += 1
        if without_below is True:
            context["without_below_threshold"] += 1
        if (
            current_below is not None
            and without_below is not None
            and current_below != without_below
        ):
            context["threshold_crossings"] += 1

        self._touch_and_save()

        logger.info(
            '{"event":"volume_profile_shadow_confidence","symbol":"%s",'
            '"period":"%s","current_score":%.2f,"score_if_true":%s,'
            '"score_if_false":%s,"score_without":%s,'
            '"threshold":%.2f,"crossing":%s}',
            result.symbol,
            result.period_label,
            current_score,
            f"{score_if_true:.2f}" if score_if_true is not None else "null",
            f"{score_if_false:.2f}" if score_if_false is not None else "null",
            f"{score_without:.2f}" if score_without is not None else "null",
            minimum_threshold if minimum_threshold is not None else 0.0,
            "true" if (current_below is not None and without_below is not None
                       and current_below != without_below) else "false",
        )

        return self.report()

    # ------------------------------------------------------------------
    # Report
    # ------------------------------------------------------------------

    def report(self) -> dict[str, Any]:
        """Return JSON-safe counters and derived rates."""
        evaluations = int(self._state["evaluations"])
        valid = int(self._state["status_counts"].get("VALID", 0))
        confluence_true = int(self._state["confluence_counts"].get("true", 0))
        confidence = self._state["confidence"]

        def rate(value: int, denominator: int) -> float:
            return round(value / denominator, 4) if denominator else 0.0

        confidence_report = dict(confidence)
        samples = int(confidence.get("samples", 0))
        without_samples = int(confidence.get("without_samples", 0))
        confidence_report.update(
            {
                "average_current": round(
                    float(confidence.get("current_total", 0.0)) / samples, 2
                )
                if samples
                else None,
                "average_without": round(
                    float(confidence.get("without_total", 0.0)) / without_samples, 2
                )
                if without_samples
                else None,
                "average_delta_current_vs_without": round(
                    (
                        float(confidence.get("current_total", 0.0)) / samples
                        - float(confidence.get("without_total", 0.0)) / without_samples
                    ),
                    2,
                )
                if samples and without_samples
                else None,
                "threshold_crossing_rate": round(
                    int(confidence.get("threshold_crossings", 0)) / samples, 4
                )
                if samples
                else 0.0,
            }
        )

        return {
            "version": self._state["version"],
            "updated_at": self._state["updated_at"],
            "evaluations": evaluations,
            "status_counts": dict(self._state["status_counts"]),
            "confluence_counts": dict(self._state["confluence_counts"]),
            "matched_level_count": self._state["matched_level_count"],
            "rates": {
                "valid": rate(valid, evaluations),
                "unknown": rate(
                    self._state["status_counts"].get("UNKNOWN", 0), evaluations
                ),
                "confluence_true": rate(confluence_true, valid),
            },
            "by_context": {
                key: dict(value)
                for key, value in self._state["by_context"].items()
            },
            "by_period": {
                key: dict(value)
                for key, value in self._state["by_period"].items()
            },
            "confidence": confidence_report,
        }

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _load(self) -> dict[str, Any]:
        try:
            raw = self._runtime_store.get(self.STATE_KEY, None)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                '{"event":"volume_profile_shadow_state_load_failed","reason":"%s"}',
                str(exc)[:160],
            )
            return _empty_state()
        if not isinstance(raw, dict):
            return _empty_state()
        state = _empty_state()
        for key in ("version", "updated_at", "evaluations", "matched_level_count"):
            if key in raw:
                state[key] = raw[key]
        for key in ("status_counts", "confluence_counts", "confidence", "by_context", "by_period"):
            if isinstance(raw.get(key), dict):
                state[key].update(raw[key])
        return state

    def _touch_and_save(self) -> None:
        self._state["updated_at"] = datetime.now(timezone.utc).isoformat()
        try:
            self._runtime_store.set(self.STATE_KEY, self._state)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                '{"event":"volume_profile_shadow_state_save_failed","reason":"%s"}',
                str(exc)[:160],
            )
