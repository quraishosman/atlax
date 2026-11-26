"""Shadow-only Prop-Firm validation beside the live risk path.

The validator evaluates the same candidate and account snapshot with an
optional, fully validated ``PropFirmRiskContext``. It records what the
Prop-Firm guard would have done, but never replaces the live evaluation,
changes a lot size, blocks an order, or calls MT5.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Callable, Optional

from core.risk.loss_budget import PropFirmRiskContext

logger = logging.getLogger("atlax.risk.prop_firm_shadow")

SHADOW_STATE_KEY = "prop_firm.shadow_validation"
_MAX_SAMPLES = 50


def _text(value: Any, limit: int = 240) -> str:
    return str(value or "")[:limit]


def _decimal_text(value: Any) -> Optional[str]:
    if value is None:
        return None
    try:
        return str(Decimal(str(value)))
    except (ArithmeticError, TypeError, ValueError):
        return None


class PropFirmShadowValidator:
    """Persist restart-safe comparison evidence without affecting execution."""

    def __init__(
        self,
        risk_engine: Any,
        risk_config: Any,
        runtime_store: Any,
        *,
        context_provider: Optional[Callable[[Any, Any], Any]] = None,
    ) -> None:
        self._risk_engine = risk_engine
        self._risk_config = risk_config
        self._runtime_store = runtime_store
        self._context_provider = context_provider

    def _empty_state(self) -> dict[str, Any]:
        return {
            "status": "NOT_CONFIGURED" if not callable(self._context_provider) else "SHADOW",
            "evaluated": 0,
            "unknown": 0,
            "would_block": 0,
            "would_resize": 0,
            "would_allow": 0,
            "would_increase": 0,
            "no_change": 0,
            "last_evaluated_at": None,
            "last_event": None,
            "samples": [],
        }

    def _load_state(self) -> dict[str, Any]:
        raw = self._runtime_store.get(SHADOW_STATE_KEY, {})
        if not isinstance(raw, dict):
            return self._empty_state()
        state = self._empty_state()
        for key in (
            "status",
            "evaluated",
            "unknown",
            "would_block",
            "would_resize",
            "would_allow",
            "would_increase",
            "no_change",
            "last_evaluated_at",
            "last_event",
            "samples",
        ):
            if key in raw:
                state[key] = raw[key]
        for key in (
            "evaluated",
            "unknown",
            "would_block",
            "would_resize",
            "would_allow",
            "would_increase",
            "no_change",
        ):
            try:
                state[key] = max(0, int(state[key]))
            except (TypeError, ValueError):
                state[key] = 0
        if not isinstance(state["samples"], list):
            state["samples"] = []
        state["samples"] = state["samples"][-_MAX_SAMPLES:]
        return state

    def _save_state(self, state: dict[str, Any]) -> None:
        self._runtime_store.set(SHADOW_STATE_KEY, state)

    def _unknown(
        self,
        state: dict[str, Any],
        *,
        candidate: Any,
        reason: str,
    ) -> dict[str, Any]:
        now = datetime.now(timezone.utc).isoformat()
        event = {
            "candidate_id": _text(getattr(candidate, "candidate_id", ""), 120),
            "symbol": _text(getattr(candidate, "symbol", ""), 40),
            "timeframe": _text(getattr(candidate, "timeframe", ""), 20),
            "status": "UNKNOWN",
            "category": "unknown",
            "reason": _text(reason),
            "evaluated_at": now,
        }
        state["status"] = "SHADOW"
        state["evaluated"] += 1
        state["unknown"] += 1
        state["last_evaluated_at"] = now
        state["last_event"] = event
        state["samples"].append(event)
        state["samples"] = state["samples"][-_MAX_SAMPLES:]
        self._save_state(state)
        logger.warning(
            '{"event":"prop_firm_shadow_unknown","candidate_id":"%s","reason":"%s"}',
            event["candidate_id"],
            event["reason"],
        )
        return event

    def observe(self, candidate: Any, account_state: Any, current_evaluation: Any) -> dict[str, Any] | None:
        """Compare a shadow evaluation and return evidence, never a live decision."""
        if not callable(self._context_provider):
            return None
        state = self._load_state()
        try:
            context = self._context_provider(candidate, account_state)
        except Exception as exc:  # noqa: BLE001 - shadow must not affect live flow
            return self._unknown(state, candidate=candidate, reason=f"context_provider_error: {_text(exc)}")
        if not isinstance(context, PropFirmRiskContext):
            return self._unknown(
                state,
                candidate=candidate,
                reason="validated PropFirmRiskContext is unavailable",
            )

        try:
            shadow_evaluation = self._risk_engine.evaluate(
                candidate,
                self._risk_config,
                account_state,
                context,
            )
        except Exception as exc:  # noqa: BLE001 - shadow must not affect live flow
            return self._unknown(state, candidate=candidate, reason=f"shadow_evaluation_error: {_text(exc)}")

        current_approved = bool(getattr(current_evaluation, "approved", False))
        shadow_approved = bool(getattr(shadow_evaluation, "approved", False))
        current_lot = _decimal_text(getattr(current_evaluation, "lot_size", None))
        shadow_lot = _decimal_text(getattr(shadow_evaluation, "lot_size", None))
        category = "no_change"
        if current_approved and not shadow_approved:
            category = "would_block"
        elif not current_approved and shadow_approved:
            category = "would_allow"
        elif current_approved and shadow_approved:
            current_value = Decimal(current_lot) if current_lot is not None else None
            shadow_value = Decimal(shadow_lot) if shadow_lot is not None else None
            if current_value is None or shadow_value is None:
                return self._unknown(
                    state,
                    candidate=candidate,
                    reason="lot-size comparison is unavailable",
                )
            if shadow_value < current_value:
                category = "would_resize"
            elif shadow_value > current_value:
                category = "would_increase"

        now = datetime.now(timezone.utc).isoformat()
        event = {
            "candidate_id": _text(getattr(candidate, "candidate_id", ""), 120),
            "symbol": _text(getattr(candidate, "symbol", ""), 40),
            "timeframe": _text(getattr(candidate, "timeframe", ""), 20),
            "status": "SHADOW",
            "category": category,
            "current_approved": current_approved,
            "shadow_approved": shadow_approved,
            "current_lot_size": current_lot,
            "shadow_lot_size": shadow_lot,
            "shadow_rejection_code": _text(getattr(shadow_evaluation, "rejection_code", ""), 80),
            "shadow_reason": _text(getattr(shadow_evaluation, "reason", "")),
            "profile_id": _text(context.profile.profile_id, 120),
            "profile_version": _text(context.profile.version, 80),
            "evaluated_at": now,
        }
        state["status"] = "SHADOW"
        state["evaluated"] += 1
        state[category] += 1
        state["last_evaluated_at"] = now
        state["last_event"] = event
        state["samples"].append(event)
        state["samples"] = state["samples"][-_MAX_SAMPLES:]
        self._save_state(state)
        logger.info(
            '{"event":"prop_firm_shadow_evaluated","candidate_id":"%s",'
            '"category":"%s","current_approved":%s,"shadow_approved":%s}',
            event["candidate_id"],
            category,
            str(current_approved).lower(),
            str(shadow_approved).lower(),
        )
        return event

    def report(self) -> dict[str, Any]:
        """Return aggregate shadow evidence for the admin review surface."""
        state = self._load_state()
        return {
            key: value
            for key, value in state.items()
            if key != "samples"
        } | {"sample_count": len(state["samples"])}
