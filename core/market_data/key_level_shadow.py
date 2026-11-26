"""Auditable shadow-mode counters for the V1 key-level evaluator."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

from core.models.key_level import KeyLevelEvaluation


logger = logging.getLogger("atlax.key_level_shadow")


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
        "zone_true": 0,
        "zone_false": 0,
        "zone_unknown": 0,
        "matched_levels": 0,
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
        "zone_counts": {"true": 0, "false": 0, "unknown": 0},
        "matched_levels": 0,
        "by_context": {},
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


class KeyLevelShadowLedger:
    """Persist shadow observations without affecting trading decisions."""

    STATE_KEY = "key_level.shadow_stats"

    def __init__(self, runtime_store: Any) -> None:
        self._runtime_store = runtime_store
        self._state = self._load()

    def observe_evaluation(self, evaluation: KeyLevelEvaluation) -> dict[str, Any]:
        """Record one newly observed parent evaluation and return a report."""
        state = self._state
        status = evaluation.status
        state["evaluations"] += 1
        state["status_counts"][status] = state["status_counts"].get(status, 0) + 1
        state["matched_levels"] += evaluation.matched_level_count

        if evaluation.parent_at_htf_zone is True:
            zone_key = "true"
        elif evaluation.parent_at_htf_zone is False:
            zone_key = "false"
        else:
            zone_key = "unknown"
        state["zone_counts"][zone_key] += 1

        context_key = f"{evaluation.symbol}|{evaluation.parent_timeframe}"
        context = state["by_context"].setdefault(context_key, _empty_context())
        context["evaluations"] += 1
        context[status.lower()] += 1
        context["zone_" + zone_key] += 1
        context["matched_levels"] += evaluation.matched_level_count
        self._touch_and_save()
        return self.report()

    def observe_confidence(
        self,
        *,
        evaluation: KeyLevelEvaluation,
        current_score: float,
        score_if_true: Optional[float],
        score_if_false: Optional[float],
        score_without: Optional[float] = None,
        minimum_threshold: Optional[float] = None,
    ) -> dict[str, Any]:
        """Record live and counterfactual scores for one candidate/profile."""
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

        context_key = f"{evaluation.symbol}|{evaluation.parent_timeframe}"
        context = self._state["by_context"].setdefault(context_key, _empty_context())
        for key in (
            "confidence_samples",
            "current_below_threshold",
            "without_below_threshold",
            "threshold_crossings",
        ):
            context.setdefault(key, 0)
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
        return self.report()

    def report(self) -> dict[str, Any]:
        """Return JSON-safe counters and derived rates."""
        evaluations = int(self._state["evaluations"])
        valid = int(self._state["status_counts"].get("VALID", 0))
        zone_true = int(self._state["zone_counts"].get("true", 0))
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
            "zone_counts": dict(self._state["zone_counts"]),
            "matched_levels": self._state["matched_levels"],
            "rates": {
                "valid": rate(valid, evaluations),
                "unknown": rate(self._state["status_counts"].get("UNKNOWN", 0), evaluations),
                "zone_true": rate(zone_true, valid),
            },
            "by_context": {
                key: dict(value)
                for key, value in self._state["by_context"].items()
            },
            "confidence": confidence_report,
        }

    def _load(self) -> dict[str, Any]:
        try:
            raw = self._runtime_store.get(self.STATE_KEY, None)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                '{"event":"key_level_shadow_state_load_failed","reason":"%s"}',
                str(exc)[:160],
            )
            return _empty_state()
        if not isinstance(raw, dict):
            return _empty_state()
        # A malformed or old runtime value must not block the scanner.
        state = _empty_state()
        for key in ("version", "updated_at", "evaluations", "matched_levels"):
            if key in raw:
                state[key] = raw[key]
        for key in ("status_counts", "zone_counts", "confidence", "by_context"):
            if isinstance(raw.get(key), dict):
                state[key].update(raw[key])
        return state

    def _touch_and_save(self) -> None:
        self._state["updated_at"] = datetime.now(timezone.utc).isoformat()
        try:
            self._runtime_store.set(self.STATE_KEY, self._state)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                '{"event":"key_level_shadow_state_save_failed","reason":"%s"}',
                str(exc)[:160],
            )
