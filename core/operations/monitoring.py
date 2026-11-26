"""Persisted counters for ATLAX operational delivery monitoring.

The ledger records routing outcomes only. It does not alter detector,
confidence, risk, or execution decisions.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any


logger = logging.getLogger("atlax.operations.monitoring")


def _empty_state() -> dict[str, Any]:
    return {
        "version": 1,
        "updated_at": None,
        "alert_suppressed": 0,
        "alert_suppressed_by_reason": {},
        "subscriber_delivery": {
            "delivered": 0,
            "suppressed": 0,
            "suppressed_by_reason": {},
        },
        "nested_crt": {
            "suppressed": 0,
            "shadow_suppressed": 0,
            "allowed": 0,
            "controller_expired": 0,
            "by_reason": {},
        },
        "standalone_noise": {
            "evaluated": 0,
            "unknown": 0,
            "candidate": 0,
            "would_suppress": 0,
        },
        "crt_quality_tightening": {
            "evaluated": 0,
            "unknown": 0,
            "would_suppress": 0,
            "by_reason": {},
        },
        "htf_momentum": {
            "evaluated": 0,
            "aligned": 0,
            "opposing": 0,
            "sideways": 0,
            "unknown": 0,
            "one_candle_runs": 0,
            "strong_runs": 0,
            "score_samples": 0,
            "base_score_total": 0.0,
            "proposed_score_total": 0.0,
            "adjustment_total": 0.0,
        },
        "volume_profile": {
            "calculated": 0,
            "unknown": 0,
            "matches": 0,
            "no_match": 0,
            "cache_hits": 0,
        },
        "account_mode": {
            "mode_changes": 0,
            "verification_failures": 0,
            "unknown_contexts": 0,
            "blocked_entries": 0,
            "resized_entries": 0,
            "protective_actions": 0,
            "protection_context_refreshes": 0,
            "by_reason": {},
        },
        "circuit_breaker": {
            "transitions": 0,
            "blocked": 0,
            "paused": 0,
            "unknown": 0,
            "by_reason": {},
        },
        "reconciliation": {
            "evaluated": 0,
            "blocked": 0,
            "matched": 0,
            "unknown": 0,
            "by_status": {},
        },
        "prop_firm_profiles": {
            "evaluated": 0,
            "approved": 0,
            "stale": 0,
            "review_required": 0,
            "invalid": 0,
            "missing": 0,
            "unknown": 0,
            "by_status": {},
        },
    }


class OperationalMetricsLedger:
    """Persist delivery counters through the configured runtime store."""

    STATE_KEY = "operations.metrics"

    def __init__(self, runtime_store: Any) -> None:
        self._runtime_store = runtime_store
        self._state = self._load()

    def observe_alert_suppressed(self, reason: str) -> None:
        """Count an alert rejected by the alert-engine gates."""
        self._state["alert_suppressed"] += 1
        self._increment_reason(self._state["alert_suppressed_by_reason"], reason)
        self._touch_and_save()

    def observe_subscriber_suppressed(self, reason: str) -> None:
        """Count a subscriber delivery rejected by a routing entitlement."""
        delivery = self._state["subscriber_delivery"]
        delivery["suppressed"] += 1
        self._increment_reason(delivery["suppressed_by_reason"], reason)
        self._touch_and_save()

    def observe_subscriber_delivered(self) -> None:
        """Count a subscriber message accepted by Telegram."""
        self._state["subscriber_delivery"]["delivered"] += 1
        self._touch_and_save()

    def observe_nested_decision(
        self,
        reason: str,
        *,
        suppressed: bool,
        shadow: bool = False,
    ) -> None:
        """Count an enforced or shadow nested-CRT routing decision."""
        nested = self._state["nested_crt"]
        if suppressed:
            nested["suppressed"] += 1
        elif shadow:
            nested["shadow_suppressed"] += 1
        else:
            nested["allowed"] += 1
        self._increment_reason(nested["by_reason"], reason)
        self._touch_and_save()

    def observe_nested_controller_expired(self, reason: str) -> None:
        """Count a controller lifecycle expiry."""
        nested = self._state["nested_crt"]
        nested["controller_expired"] += 1
        self._increment_reason(nested["by_reason"], reason)
        self._touch_and_save()

    def observe_standalone_noise(self, status: str, *, candidate: bool) -> None:
        """Count audit-only weak standalone CRT observations."""
        noise = self._state["standalone_noise"]
        noise["evaluated"] += 1
        if str(status).upper() == "UNKNOWN":
            noise["unknown"] += 1
        if candidate:
            noise["candidate"] += 1
        self._touch_and_save()

    def observe_crt_quality_tightening(
        self,
        status: str,
        *,
        would_suppress: bool,
        reasons: tuple[str, ...] = (),
    ) -> None:
        """Count counterfactual CRT quality-policy evaluations."""
        quality = self._state["crt_quality_tightening"]
        quality["evaluated"] += 1
        if str(status).upper() == "UNKNOWN":
            quality["unknown"] += 1
        if would_suppress:
            quality["would_suppress"] += 1
        for reason in reasons:
            self._increment_reason(quality["by_reason"], reason)
        self._touch_and_save()

    def observe_htf_momentum(
        self,
        relationship: str,
        *,
        run_length: int,
        base_score: float | None = None,
        proposed_score: float | None = None,
    ) -> None:
        """Count shadow H1 context without affecting routing or execution."""
        momentum = self._state["htf_momentum"]
        momentum["evaluated"] += 1
        relationship_key = str(relationship or "UNKNOWN").lower()
        if relationship_key not in ("aligned", "opposing", "sideways", "unknown"):
            relationship_key = "unknown"
        momentum[relationship_key] += 1
        if int(run_length) == 1:
            momentum["one_candle_runs"] += 1
        if int(run_length) >= 2:
            momentum["strong_runs"] += 1
        if base_score is not None and proposed_score is not None:
            momentum["score_samples"] += 1
            momentum["base_score_total"] += float(base_score)
            momentum["proposed_score_total"] += float(proposed_score)
            momentum["adjustment_total"] += float(proposed_score) - float(base_score)
        self._touch_and_save()

    def observe_volume_profile(
        self,
        result: Any,
        *,
        matched_level_count: int,
        cache_hit: bool = False,
    ) -> None:
        """Record profile status and match/cache counters."""
        volume = self._state["volume_profile"]
        status = str(getattr(getattr(result, "status", None), "value", getattr(result, "status", "UNKNOWN")))
        if status == "VALID":
            volume["calculated"] += 1
            if matched_level_count > 0:
                volume["matches"] += 1
            else:
                volume["no_match"] += 1
        else:
            volume["unknown"] += 1
        if cache_hit:
            volume["cache_hits"] += 1
        self._touch_and_save()

    def observe_account_mode_change(self, mode: str) -> None:
        """Count an administrator-selected account risk-mode transition."""
        account_mode = self._state["account_mode"]
        account_mode["mode_changes"] += 1
        self._increment_reason(account_mode["by_reason"], f"mode:{mode}")
        self._touch_and_save()

    def observe_account_mode_verification(self, status: str, reason: str) -> None:
        """Count account/profile verification results without changing policy."""
        account_mode = self._state["account_mode"]
        normalized = str(status or "UNKNOWN").upper()
        if normalized == "UNKNOWN":
            account_mode["unknown_contexts"] += 1
        if normalized not in {"VERIFIED", "NOT_REQUIRED"}:
            account_mode["verification_failures"] += 1
        self._increment_reason(account_mode["by_reason"], reason)
        self._touch_and_save()

    def observe_prop_firm_blocked(self, reason: str) -> None:
        """Count a live Prop-Firm entry blocked by a safety gate."""
        account_mode = self._state["account_mode"]
        account_mode["blocked_entries"] += 1
        self._increment_reason(account_mode["by_reason"], reason)
        self._touch_and_save()

    def observe_protection_context_refresh(self) -> None:
        """Count a verified live protection context installed for execution."""
        self._state["account_mode"]["protection_context_refreshes"] += 1
        self._touch_and_save()

    def observe_prop_firm_resized(self) -> None:
        """Count an approved entry whose risk was capped by the budget."""
        account_mode = self._state["account_mode"]
        account_mode["resized_entries"] += 1
        self._increment_reason(account_mode["by_reason"], "budget_risk_cap")
        self._touch_and_save()

    def observe_protective_action(self, action: str) -> None:
        """Count a broker-confirmed protective action on an ATLAX position."""
        account_mode = self._state["account_mode"]
        account_mode["protective_actions"] += 1
        self._increment_reason(account_mode["by_reason"], f"protective:{action}")
        self._touch_and_save()

    def observe_circuit_breaker(self, state: str, reason: str) -> None:
        """Count a persisted new-entry circuit-breaker transition."""
        breaker = self._state["circuit_breaker"]
        breaker["transitions"] += 1
        normalized = str(state or "UNKNOWN").upper()
        if normalized == "BLOCKED":
            breaker["blocked"] += 1
        elif normalized == "PAUSED":
            breaker["paused"] += 1
        elif normalized == "UNKNOWN":
            breaker["unknown"] += 1
        self._increment_reason(breaker["by_reason"], reason)
        self._touch_and_save()

    def observe_reconciliation(self, status: str) -> None:
        """Count one broker-to-ATLAX reconciliation result."""
        reconciliation = self._state["reconciliation"]
        reconciliation["evaluated"] += 1
        normalized = str(status or "UNKNOWN").upper()
        self._increment_reason(reconciliation["by_status"], normalized)
        if normalized == "MATCHED":
            reconciliation["matched"] += 1
        if normalized in {"CONFLICT", "UNKNOWN"}:
            reconciliation["blocked"] += 1
        if normalized == "UNKNOWN":
            reconciliation["unknown"] += 1
        self._touch_and_save()

    def observe_prop_firm_profile(self, status: str) -> None:
        """Count a source-backed Prop-Firm profile monitor transition."""
        profiles = self._state["prop_firm_profiles"]
        profiles["evaluated"] += 1
        normalized = str(status or "UNKNOWN").upper()
        key = {
            "REVIEW_REQUIRED": "review_required",
            "APPROVED": "approved",
            "STALE": "stale",
            "INVALID": "invalid",
            "MISSING": "missing",
            "UNKNOWN": "unknown",
        }.get(normalized, "unknown")
        profiles[key] += 1
        self._increment_reason(profiles["by_status"], normalized)
        self._touch_and_save()

    def report(self) -> dict[str, Any]:
        """Return a JSON-safe snapshot for admin reporting and diagnostics."""
        return {
            "version": self._state["version"],
            "updated_at": self._state["updated_at"],
            "alert_suppressed": int(self._state["alert_suppressed"]),
            "alert_suppressed_by_reason": dict(self._state["alert_suppressed_by_reason"]),
            "subscriber_delivery": {
                "delivered": int(self._state["subscriber_delivery"]["delivered"]),
                "suppressed": int(self._state["subscriber_delivery"]["suppressed"]),
                "suppressed_by_reason": dict(
                    self._state["subscriber_delivery"]["suppressed_by_reason"]
                ),
            },
            "nested_crt": {
                "suppressed": int(self._state["nested_crt"]["suppressed"]),
                "shadow_suppressed": int(self._state["nested_crt"]["shadow_suppressed"]),
                "allowed": int(self._state["nested_crt"]["allowed"]),
                "controller_expired": int(
                    self._state["nested_crt"]["controller_expired"]
                ),
                "by_reason": dict(self._state["nested_crt"]["by_reason"]),
            },
            "standalone_noise": {
                "evaluated": int(self._state["standalone_noise"]["evaluated"]),
                "unknown": int(self._state["standalone_noise"]["unknown"]),
                "candidate": int(self._state["standalone_noise"]["candidate"]),
                "would_suppress": int(
                    self._state["standalone_noise"]["would_suppress"]
                ),
            },
            "crt_quality_tightening": {
                "evaluated": int(
                    self._state["crt_quality_tightening"]["evaluated"]
                ),
                "unknown": int(
                    self._state["crt_quality_tightening"]["unknown"]
                ),
                "would_suppress": int(
                    self._state["crt_quality_tightening"]["would_suppress"]
                ),
                "by_reason": dict(
                    self._state["crt_quality_tightening"]["by_reason"]
                ),
            },
            "htf_momentum": {
                key: int(self._state["htf_momentum"].get(key, 0))
                for key in (
                    "evaluated",
                    "aligned",
                    "opposing",
                    "sideways",
                    "unknown",
                    "one_candle_runs",
                    "strong_runs",
                    "score_samples",
                )
            },
            "htf_momentum_scores": {
                "base_score_total": round(float(self._state["htf_momentum"].get("base_score_total", 0.0)), 2),
                "proposed_score_total": round(float(self._state["htf_momentum"].get("proposed_score_total", 0.0)), 2),
                "adjustment_total": round(float(self._state["htf_momentum"].get("adjustment_total", 0.0)), 2),
            },
            "volume_profile": {
                key: int(self._state["volume_profile"].get(key, 0))
                for key in ("calculated", "unknown", "matches", "no_match", "cache_hits")
            },
            "account_mode": {
                key: int(self._state["account_mode"].get(key, 0))
                for key in (
                    "mode_changes",
                    "verification_failures",
                    "unknown_contexts",
                    "blocked_entries",
                    "resized_entries",
                    "protective_actions",
                    "protection_context_refreshes",
                )
            }
            | {"by_reason": dict(self._state["account_mode"].get("by_reason", {}))},
            "circuit_breaker": {
                key: int(self._state["circuit_breaker"].get(key, 0))
                for key in ("transitions", "blocked", "paused", "unknown")
            }
            | {"by_reason": dict(self._state["circuit_breaker"].get("by_reason", {}))},
            "reconciliation": {
                key: int(self._state["reconciliation"].get(key, 0))
                for key in ("evaluated", "blocked", "matched", "unknown")
            }
            | {"by_status": dict(self._state["reconciliation"].get("by_status", {}))},
            "prop_firm_profiles": {
                key: int(self._state["prop_firm_profiles"].get(key, 0))
                for key in (
                    "evaluated",
                    "approved",
                    "stale",
                    "review_required",
                    "invalid",
                    "missing",
                    "unknown",
                )
            }
            | {
                "by_status": dict(
                    self._state["prop_firm_profiles"].get("by_status", {})
                )
            },
        }

    @staticmethod
    def _increment_reason(counter: dict[str, int], reason: str) -> None:
        key = str(reason or "unknown")[:120]
        counter[key] = int(counter.get(key, 0)) + 1

    def _load(self) -> dict[str, Any]:
        try:
            raw = self._runtime_store.get(self.STATE_KEY, None)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                '{"event":"operations_metrics_load_failed","reason":"%s"}',
                str(exc)[:160],
            )
            return _empty_state()
        if not isinstance(raw, dict):
            return _empty_state()

        state = _empty_state()
        if raw.get("version") is not None:
            state["version"] = raw["version"]
        state["updated_at"] = raw.get("updated_at")
        try:
            state["alert_suppressed"] = max(0, int(raw.get("alert_suppressed", 0)))
        except (TypeError, ValueError):
            pass
        if isinstance(raw.get("alert_suppressed_by_reason"), dict):
            state["alert_suppressed_by_reason"] = {
                str(key)[:120]: max(0, int(value))
                for key, value in raw["alert_suppressed_by_reason"].items()
                if isinstance(value, (int, float))
            }
        raw_delivery = raw.get("subscriber_delivery")
        if isinstance(raw_delivery, dict):
            for key in ("delivered", "suppressed"):
                try:
                    state["subscriber_delivery"][key] = max(0, int(raw_delivery.get(key, 0)))
                except (TypeError, ValueError):
                    pass
            if isinstance(raw_delivery.get("suppressed_by_reason"), dict):
                state["subscriber_delivery"]["suppressed_by_reason"] = {
                    str(reason)[:120]: max(0, int(value))
                    for reason, value in raw_delivery["suppressed_by_reason"].items()
                    if isinstance(value, (int, float))
                }
        raw_nested = raw.get("nested_crt")
        if isinstance(raw_nested, dict):
            for key in ("suppressed", "shadow_suppressed", "allowed", "controller_expired"):
                try:
                    state["nested_crt"][key] = max(0, int(raw_nested.get(key, 0)))
                except (TypeError, ValueError):
                    pass
            if isinstance(raw_nested.get("by_reason"), dict):
                state["nested_crt"]["by_reason"] = {
                    str(reason)[:120]: max(0, int(value))
                    for reason, value in raw_nested["by_reason"].items()
                    if isinstance(value, (int, float))
                }
        raw_noise = raw.get("standalone_noise")
        if isinstance(raw_noise, dict):
            for key in ("evaluated", "unknown", "candidate", "would_suppress"):
                try:
                    state["standalone_noise"][key] = max(
                        0, int(raw_noise.get(key, 0))
                    )
                except (TypeError, ValueError):
                    pass
        raw_quality = raw.get("crt_quality_tightening")
        if isinstance(raw_quality, dict):
            for key in ("evaluated", "unknown", "would_suppress"):
                try:
                    state["crt_quality_tightening"][key] = max(
                        0, int(raw_quality.get(key, 0))
                    )
                except (TypeError, ValueError):
                    pass
            if isinstance(raw_quality.get("by_reason"), dict):
                state["crt_quality_tightening"]["by_reason"] = {
                    str(reason)[:120]: max(0, int(value))
                    for reason, value in raw_quality["by_reason"].items()
                    if isinstance(value, (int, float))
                }
        raw_momentum = raw.get("htf_momentum")
        if isinstance(raw_momentum, dict):
            for key in (
                "evaluated",
                "aligned",
                "opposing",
                "sideways",
                "unknown",
                "one_candle_runs",
                "strong_runs",
                "score_samples",
            ):
                try:
                    state["htf_momentum"][key] = max(0, int(raw_momentum.get(key, 0)))
                except (TypeError, ValueError):
                    pass
            for key in ("base_score_total", "proposed_score_total", "adjustment_total"):
                try:
                    value = float(raw_momentum.get(key, 0.0))
                    state["htf_momentum"][key] = (
                        value if key == "adjustment_total" else max(0.0, value)
                    )
                except (TypeError, ValueError):
                    pass
        raw_volume = raw.get("volume_profile")
        if isinstance(raw_volume, dict):
            for key in ("calculated", "unknown", "matches", "no_match", "cache_hits"):
                try:
                    state["volume_profile"][key] = max(0, int(raw_volume.get(key, 0)))
                except (TypeError, ValueError):
                    pass
        raw_account_mode = raw.get("account_mode")
        if isinstance(raw_account_mode, dict):
            for key in (
                "mode_changes",
                "verification_failures",
                "unknown_contexts",
                "blocked_entries",
                "resized_entries",
                "protective_actions",
                "protection_context_refreshes",
            ):
                try:
                    state["account_mode"][key] = max(
                        0, int(raw_account_mode.get(key, 0))
                    )
                except (TypeError, ValueError):
                    pass
            if isinstance(raw_account_mode.get("by_reason"), dict):
                state["account_mode"]["by_reason"] = {
                    str(reason)[:120]: max(0, int(value))
                    for reason, value in raw_account_mode["by_reason"].items()
                    if isinstance(value, (int, float))
                }
        raw_breaker = raw.get("circuit_breaker")
        if isinstance(raw_breaker, dict):
            for key in ("transitions", "blocked", "paused", "unknown"):
                try:
                    state["circuit_breaker"][key] = max(
                        0, int(raw_breaker.get(key, 0))
                    )
                except (TypeError, ValueError):
                    pass
            if isinstance(raw_breaker.get("by_reason"), dict):
                state["circuit_breaker"]["by_reason"] = {
                    str(reason)[:120]: max(0, int(value))
                    for reason, value in raw_breaker["by_reason"].items()
                    if isinstance(value, (int, float))
                }
        raw_reconciliation = raw.get("reconciliation")
        if isinstance(raw_reconciliation, dict):
            for key in ("evaluated", "blocked", "matched", "unknown"):
                try:
                    state["reconciliation"][key] = max(
                        0, int(raw_reconciliation.get(key, 0))
                    )
                except (TypeError, ValueError):
                    pass
            if isinstance(raw_reconciliation.get("by_status"), dict):
                state["reconciliation"]["by_status"] = {
                    str(status)[:120]: max(0, int(value))
                    for status, value in raw_reconciliation["by_status"].items()
                    if isinstance(value, (int, float))
                }
        raw_profiles = raw.get("prop_firm_profiles")
        if isinstance(raw_profiles, dict):
            for key in (
                "evaluated",
                "approved",
                "stale",
                "review_required",
                "invalid",
                "missing",
                "unknown",
            ):
                try:
                    state["prop_firm_profiles"][key] = max(
                        0, int(raw_profiles.get(key, 0))
                    )
                except (TypeError, ValueError):
                    pass
            if isinstance(raw_profiles.get("by_status"), dict):
                state["prop_firm_profiles"]["by_status"] = {
                    str(status)[:120]: max(0, int(value))
                    for status, value in raw_profiles["by_status"].items()
                    if isinstance(value, (int, float))
                }
        return state

    def _touch_and_save(self) -> None:
        self._state["updated_at"] = datetime.now(timezone.utc).isoformat()
        try:
            self._runtime_store.set(self.STATE_KEY, self._state)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                '{"event":"operations_metrics_save_failed","reason":"%s"}',
                str(exc)[:160],
            )
