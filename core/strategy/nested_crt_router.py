"""Containment-aware routing for nested CRT detector events."""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Optional

from core.models.candle import CandleSequence
from core.models.detector_output import CRTMetadata, DetectorOutput
from core.settings.nested_crt_config import (
    NestedCrtRelationshipConfig,
    NestedCrtRoutingConfig,
)


logger = logging.getLogger("atlax.strategy.nested_crt_router")


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _event_id(metadata: CRTMetadata) -> str:
    return (
        f"{metadata.parent_candle.symbol}:"
        f"{metadata.parent_candle.timeframe}:"
        f"{_utc(metadata.parent_candle.open_time).isoformat()}"
    )


@dataclass(frozen=True)
class NestedCrtController:
    symbol: str
    controller_timeframe: str
    event_id: str
    classification: str
    parent_open_time: str
    sweep_open_time: str
    detection_time: str
    parent_low: str
    parent_high: str
    sweep_low: str
    sweep_high: str
    status: str = "ACTIVE"
    last_observed_closed_time: Optional[str] = None
    expiry_reason: Optional[str] = None
    expired_at: Optional[str] = None

    @classmethod
    def from_output(cls, output: DetectorOutput) -> "NestedCrtController":
        metadata = output.metadata
        if metadata is None:
            raise ValueError("cannot create a nested CRT controller without metadata")
        return cls(
            symbol=output.symbol,
            controller_timeframe=output.timeframe,
            event_id=_event_id(metadata),
            classification=output.classification,
            parent_open_time=_utc(metadata.parent_candle.open_time).isoformat(),
            sweep_open_time=_utc(metadata.sweep_candle.open_time).isoformat(),
            detection_time=_utc(output.timestamp).isoformat(),
            parent_low=str(metadata.parent_candle.low),
            parent_high=str(metadata.parent_candle.high),
            sweep_low=str(metadata.sweep_candle.low),
            sweep_high=str(metadata.sweep_candle.high),
        )

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "NestedCrtController":
        return cls(**{field: raw.get(field) for field in cls.__dataclass_fields__})

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def range_low(self) -> Decimal:
        return Decimal(self.parent_low)

    @property
    def range_high(self) -> Decimal:
        return Decimal(self.parent_high)

    @property
    def sweep_low_price(self) -> Decimal:
        return Decimal(self.sweep_low)

    @property
    def sweep_high_price(self) -> Decimal:
        return Decimal(self.sweep_high)


@dataclass(frozen=True)
class NestedCrtRoutingDecision:
    action: str
    reason_code: str
    controller_event_id: Optional[str] = None
    contained: bool = False
    shadow: bool = False

    @property
    def suppress(self) -> bool:
        return self.action == "SUPPRESS"


class NestedCrtRouter:
    """Maintain active controllers and evaluate nested event routing."""

    STATE_KEY = "nested_crt.controllers"

    def __init__(
        self,
        config: Optional[NestedCrtRoutingConfig],
        runtime_store: Any,
        monitoring: Any = None,
    ) -> None:
        self._config = config or NestedCrtRoutingConfig(False, True, ())
        self._runtime_store = runtime_store
        self._monitoring = monitoring
        self._controllers = self._load()

    @property
    def enabled(self) -> bool:
        return self._config.enabled and bool(self._config.relationships)

    def is_controller_timeframe(self, timeframe: str) -> bool:
        """Return whether a timeframe can seed a nested controller."""
        normalized = str(timeframe).upper()
        return self.enabled and any(
            relationship.controller_timeframe == normalized
            for relationship in self._config.relationships
        )

    def update_market(self, sequence: CandleSequence) -> None:
        """Expire a controller using subsequent closed candles only."""
        if not self.enabled:
            return
        for relationship in self._config.relationships:
            if sequence.timeframe != relationship.controller_timeframe:
                continue
            key = self._controller_key(sequence.symbol, relationship)
            controller = self._controllers.get(key)
            if controller is None or controller.status != "ACTIVE":
                continue
            last_observed = (
                _utc(datetime.fromisoformat(controller.last_observed_closed_time))
                if controller.last_observed_closed_time
                else None
            )
            detection_time = _utc(datetime.fromisoformat(controller.detection_time))
            for candle in sequence.candles:
                close_time = _utc(candle.close_time)
                if close_time <= detection_time or (
                    last_observed is not None and close_time <= last_observed
                ):
                    continue
                if controller.classification == "bullish_crt":
                    if candle.high >= controller.range_high:
                        self._expire(key, controller, "nested_controller_mitigated", close_time)
                        break
                    if candle.close < controller.sweep_low_price:
                        self._expire(key, controller, "nested_controller_invalidated", close_time)
                        break
                else:
                    if candle.low <= controller.range_low:
                        self._expire(key, controller, "nested_controller_mitigated", close_time)
                        break
                    if candle.close > controller.sweep_high_price:
                        self._expire(key, controller, "nested_controller_invalidated", close_time)
                        break
                controller = NestedCrtController(
                    **{
                        **controller.to_dict(),
                        "last_observed_closed_time": close_time.isoformat(),
                    }
                )
                self._controllers[key] = controller
            self._save()

    def route(self, output: DetectorOutput) -> NestedCrtRoutingDecision:
        """Return the routing decision for one detected CRT event."""
        if not self.enabled or not output.detected or output.metadata is None:
            return NestedCrtRoutingDecision("ALLOW", "nested_routing_not_applicable")

        relationship = self._relationship_for(output.timeframe)
        if relationship is None:
            return NestedCrtRoutingDecision("ALLOW", "nested_no_relationship")

        key = self._controller_key(output.symbol, relationship)
        controller = self._controllers.get(key)
        if output.timeframe == relationship.controller_timeframe:
            if controller is None or controller.status != "ACTIVE":
                self._controllers[key] = NestedCrtController.from_output(output)
                self._save()
                logger.info(
                    '{"event":"nested_crt_controller_started","symbol":"%s",'
                    '"controller_timeframe":"%s","controller_event_id":"%s",'
                    '"classification":"%s"}',
                    output.symbol,
                    output.timeframe,
                    self._controllers[key].event_id,
                    output.classification,
                )
                return NestedCrtRoutingDecision("ALLOW", "nested_controller_started")

            if _event_id(output.metadata) == controller.event_id:
                return NestedCrtRoutingDecision(
                    "ALLOW",
                    "nested_controller_already_active",
                    controller_event_id=controller.event_id,
                )

            contained = self._contained(output.metadata, controller)
            if contained:
                if relationship.same_timeframe_policy == "suppress_inside":
                    return self._decision(
                        output,
                        "nested_m15_contained",
                        controller,
                        contained=True,
                    )
                return NestedCrtRoutingDecision(
                    "ALLOW",
                    "nested_m15_contained_allowed",
                    controller_event_id=controller.event_id,
                    contained=True,
                )

            self._controllers[key] = NestedCrtController.from_output(output)
            self._save()
            logger.info(
                '{"event":"nested_crt_controller_replaced","symbol":"%s",'
                '"controller_timeframe":"%s","old_event_id":"%s",'
                '"new_event_id":"%s","reason_code":"nested_controller_replaced_outside"}',
                output.symbol,
                output.timeframe,
                controller.event_id,
                self._controllers[key].event_id,
            )
            return NestedCrtRoutingDecision(
                "ALLOW",
                "nested_controller_replaced_outside",
                controller_event_id=controller.event_id,
                contained=False,
                shadow=self._config.shadow_mode,
            )

        if output.timeframe not in relationship.child_timeframes:
            return NestedCrtRoutingDecision("ALLOW", "nested_no_relationship")
        if controller is None or controller.status != "ACTIVE":
            return NestedCrtRoutingDecision("ALLOW", "nested_no_active_controller")

        metadata = output.metadata
        if _utc(metadata.sweep_candle.close_time) < _utc(
            datetime.fromisoformat(controller.detection_time)
        ):
            return NestedCrtRoutingDecision(
                "ALLOW",
                "nested_candidate_before_controller",
                controller_event_id=controller.event_id,
            )
        contained = self._contained(metadata, controller)
        if not contained:
            return NestedCrtRoutingDecision(
                "ALLOW",
                "nested_outside_controller",
                controller_event_id=controller.event_id,
            )
        if relationship.child_policy == "suppress_inside":
            return self._decision(
                output,
                "nested_child_contained",
                controller,
                contained=True,
            )
        if output.classification == controller.classification:
            return self._decision(
                output,
                "nested_m5_aligned_bias",
                controller,
                contained=True,
            )
        return self._decision(
            output,
            "nested_m5_opposing_bias",
            controller,
            contained=True,
        )

    def report(self) -> dict[str, dict[str, Any]]:
        return {
            key: controller.to_dict()
            for key, controller in self._controllers.items()
            if controller.status == "ACTIVE"
        }

    def _decision(
        self,
        output: DetectorOutput,
        reason_code: str,
        controller: NestedCrtController,
        *,
        contained: bool,
    ) -> NestedCrtRoutingDecision:
        should_suppress = reason_code in {
            "nested_m15_contained",
            "nested_m5_opposing_bias",
            "nested_child_contained",
        }
        action = "SUPPRESS" if should_suppress else "ALLOW"
        if self._config.shadow_mode:
            action = "ALLOW"
        decision = NestedCrtRoutingDecision(
            action=action,
            reason_code=reason_code,
            controller_event_id=controller.event_id,
            contained=contained,
            shadow=self._config.shadow_mode and should_suppress,
        )
        self._record_decision(decision)
        logger.info(
            '{"event":"nested_crt_routing_decision","symbol":"%s",'
            '"candidate_timeframe":"%s","controller_timeframe":"%s",'
            '"candidate_classification":"%s","controller_classification":"%s",'
            '"contained":%s,"action":"%s","reason_code":"%s",' 
            '"shadow":%s,"controller_event_id":"%s"}',
            output.symbol,
            output.timeframe,
            controller.controller_timeframe,
            output.classification,
            controller.classification,
            str(contained).lower(),
            decision.action,
            reason_code,
            str(decision.shadow).lower(),
            controller.event_id,
        )
        return decision

    def _record_decision(self, decision: NestedCrtRoutingDecision) -> None:
        if self._monitoring is not None:
            self._monitoring.observe_nested_decision(
                decision.reason_code,
                suppressed=decision.suppress,
                shadow=decision.shadow,
            )

    @staticmethod
    def _contained(metadata: CRTMetadata, controller: NestedCrtController) -> bool:
        parent = metadata.parent_candle
        if parent.low < controller.range_low or parent.high > controller.range_high:
            return False
        if not controller.range_low <= metadata.sweep_candle.close <= controller.range_high:
            return False
        confirmation = metadata.confirmation_candle
        return confirmation is None or (
            controller.range_low <= confirmation.close <= controller.range_high
        )

    def _relationship_for(self, timeframe: str) -> Optional[NestedCrtRelationshipConfig]:
        normalized = str(timeframe).upper()
        for relationship in self._config.relationships:
            if normalized == relationship.controller_timeframe:
                return relationship
        for relationship in self._config.relationships:
            if normalized in relationship.child_timeframes:
                return relationship
        return None

    @staticmethod
    def _controller_key(
        symbol: str,
        relationship: NestedCrtRelationshipConfig,
    ) -> str:
        return f"{symbol.upper()}|{relationship.controller_timeframe}"

    def _expire(
        self,
        key: str,
        controller: NestedCrtController,
        reason: str,
        close_time: datetime,
    ) -> None:
        expired = NestedCrtController(
            **{
                **controller.to_dict(),
                "status": "EXPIRED",
                "last_observed_closed_time": close_time.isoformat(),
                "expiry_reason": reason,
                "expired_at": datetime.now(timezone.utc).isoformat(),
            }
        )
        self._controllers.pop(key, None)
        if self._monitoring is not None:
            self._monitoring.observe_nested_controller_expired(reason)
        logger.info(
            '{"event":"nested_crt_controller_expired","symbol":"%s",'
            '"controller_timeframe":"%s","controller_event_id":"%s",'
            '"reason_code":"%s"}',
            expired.symbol,
            expired.controller_timeframe,
            expired.event_id,
            reason,
        )

    def _load(self) -> dict[str, NestedCrtController]:
        try:
            raw = self._runtime_store.get(self.STATE_KEY, {})
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                '{"event":"nested_crt_state_load_failed","reason":"%s"}',
                str(exc)[:160],
            )
            return {}
        controllers = raw.get("controllers", {}) if isinstance(raw, dict) else {}
        loaded: dict[str, NestedCrtController] = {}
        for key, value in controllers.items():
            if not isinstance(value, dict) or value.get("status") != "ACTIVE":
                continue
            try:
                loaded[str(key)] = NestedCrtController.from_dict(value)
            except (KeyError, TypeError, ValueError, InvalidOperation):
                logger.warning(
                    '{"event":"nested_crt_state_record_invalid","key":"%s"}',
                    str(key)[:120],
                )
        return loaded

    def _save(self) -> None:
        try:
            self._runtime_store.set(
                self.STATE_KEY,
                {
                    "version": 1,
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                    "controllers": {
                        key: value.to_dict()
                        for key, value in self._controllers.items()
                        if value.status == "ACTIVE"
                    },
                },
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                '{"event":"nested_crt_state_save_failed","reason":"%s"}',
                str(exc)[:160],
            )
