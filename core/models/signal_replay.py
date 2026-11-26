"""Immutable Signal Replay snapshot models.

Authority: docs/39_SIGNAL_REPLAY_PLAN.md

Replay is an analytics representation of the exact closed MT5 candles already
used by the live pipeline. It does not detect setups or recalculate trading
decisions.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from enum import Enum
from typing import Any, Mapping

from core.models.candle import Candle


class ReplayCandleRole(str, Enum):
    """Role used by the V1 chart renderer."""

    CONTEXT = "CONTEXT"
    PARENT = "PARENT"
    SWEEP = "SWEEP"
    CONFIRMATION = "CONFIRMATION"
    HTF_CONTEXT = "HTF_CONTEXT"
    OUTCOME = "OUTCOME"


class ReplayAvailability(str, Enum):
    """Whether a valid snapshot can be rendered."""

    AVAILABLE = "AVAILABLE"
    PENDING = "PENDING"
    UNAVAILABLE = "UNAVAILABLE"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class ReplayLifecycleStatus(str, Enum):
    """Existing Signal Passport lifecycle values used by replay."""

    OPEN = "OPEN"
    RESOLVED = "RESOLVED"
    EXPIRED = "EXPIRED"
    UNKNOWN = "UNKNOWN"


class ReplayOutcome(str, Enum):
    """Existing outcomes plus a reserved future breakeven value."""

    WIN = "WIN"
    LOSS = "LOSS"
    BREAKEVEN = "BREAKEVEN"
    UNKNOWN = "UNKNOWN"


def _require_text(value: object, name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{name} is required")
    return text


def _require_utc(value: datetime, name: str) -> None:
    if not isinstance(value, datetime):
        raise ValueError(f"{name} must be a datetime")
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError(f"{name} must be timezone-aware UTC")


def _iso(value: datetime) -> str:
    _require_utc(value, "timestamp")
    return value.astimezone(timezone.utc).isoformat()


def _decimal(value: Decimal, name: str) -> str:
    if not isinstance(value, Decimal):
        raise ValueError(f"{name} must be Decimal")
    return str(value)


def _parse_timestamp(value: object, name: str) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{name} must be an ISO timestamp") from exc
    _require_utc(parsed, name)
    return parsed.astimezone(timezone.utc)


def _parse_candle(value: Mapping[str, Any]) -> ReplayCandle:
    if not isinstance(value, Mapping):
        raise ValueError("replay candle payload must be an object")
    try:
        candle = Candle(
            symbol=str(value["symbol"]),
            timeframe=str(value["timeframe"]),
            open_time=_parse_timestamp(value["open_time"], "candle.open_time"),
            close_time=_parse_timestamp(value["close_time"], "candle.close_time"),
            open=Decimal(str(value["open"])),
            high=Decimal(str(value["high"])),
            low=Decimal(str(value["low"])),
            close=Decimal(str(value["close"])),
            is_closed=bool(value["is_closed"]),
            volume=Decimal(str(value["volume"]))
            if value.get("volume") not in (None, "")
            else None,
        )
        role = ReplayCandleRole(str(value["role"]))
    except (KeyError, TypeError, ValueError, ArithmeticError) as exc:
        raise ValueError("invalid replay candle payload") from exc
    return ReplayCandle(candle, role)


def _json_value(value: Any) -> Any:
    """Convert approved metadata values into deterministic JSON values."""
    if value is None or isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return _iso(value)
    if isinstance(value, Mapping):
        return {
            str(key): _json_value(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    raise ValueError(
        f"rule_context contains unsupported value type: {type(value).__name__}"
    )


@dataclass(frozen=True)
class ReplayCandle:
    """A closed market candle with an explicit replay role."""

    candle: Candle
    role: ReplayCandleRole

    def __post_init__(self) -> None:
        if not isinstance(self.candle, Candle):
            raise ValueError("ReplayCandle.candle must be a Candle")
        if not isinstance(self.role, ReplayCandleRole):
            try:
                object.__setattr__(self, "role", ReplayCandleRole(str(self.role)))
            except ValueError as exc:
                raise ValueError(f"unsupported replay candle role: {self.role}") from exc
        if not self.candle.is_closed:
            raise ValueError("replay snapshots may contain closed candles only")
        _require_utc(self.candle.open_time, "candle.open_time")
        _require_utc(self.candle.close_time, "candle.close_time")
        if self.candle.close_time <= self.candle.open_time:
            raise ValueError("candle.close_time must be after candle.open_time")

    def to_payload(self) -> dict[str, Any]:
        candle = self.candle
        return {
            "role": self.role.value,
            "symbol": candle.symbol,
            "timeframe": candle.timeframe,
            "open_time": _iso(candle.open_time),
            "close_time": _iso(candle.close_time),
            "open": _decimal(candle.open, "candle.open"),
            "high": _decimal(candle.high, "candle.high"),
            "low": _decimal(candle.low, "candle.low"),
            "close": _decimal(candle.close, "candle.close"),
            "volume": str(candle.volume) if candle.volume is not None else None,
            "is_closed": True,
        }


@dataclass(frozen=True)
class ReplaySnapshot:
    """Validated immutable formation evidence for one Signal Passport record."""

    snapshot_id: str
    signal_id: str
    snapshot_version: str
    symbol: str
    source_timeframe: str
    source: str
    created_at: datetime
    captured_at: datetime
    formation_cutoff_at: datetime
    context_bars_before_parent: int
    outcome_forward_bars: int
    formation_candles: tuple[ReplayCandle, ...]
    higher_timeframe_candles: tuple[ReplayCandle, ...] = ()
    outcome_candles: tuple[ReplayCandle, ...] = ()
    rule_context: Mapping[str, Any] = field(default_factory=dict)
    availability: ReplayAvailability = ReplayAvailability.AVAILABLE
    lifecycle_status: ReplayLifecycleStatus = ReplayLifecycleStatus.OPEN
    final_outcome: ReplayOutcome = ReplayOutcome.UNKNOWN
    outcome_at: datetime | None = None
    invalidation_reason: str = ""
    failure_reason: str = ""
    snapshot_hash: str = field(init=False)

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> "ReplaySnapshot":
        """Rehydrate a validated snapshot from a local or PostgreSQL record."""
        if not isinstance(payload, Mapping):
            raise ValueError("replay snapshot payload must be an object")
        snapshot = cls(
            snapshot_id=str(payload.get("snapshot_id", "")),
            signal_id=str(payload.get("signal_id", "")),
            snapshot_version=str(payload.get("snapshot_version", "")),
            symbol=str(payload.get("symbol", "")),
            source_timeframe=str(payload.get("source_timeframe", "")),
            source=str(payload.get("source", "")),
            created_at=_parse_timestamp(payload.get("created_at"), "created_at"),
            captured_at=_parse_timestamp(payload.get("captured_at"), "captured_at"),
            formation_cutoff_at=_parse_timestamp(
                payload.get("formation_cutoff_at"), "formation_cutoff_at"
            ),
            context_bars_before_parent=int(payload.get("context_bars_before_parent", -1)),
            outcome_forward_bars=int(payload.get("outcome_forward_bars", -1)),
            formation_candles=tuple(
                _parse_candle(item) for item in payload.get("formation_candles", [])
            ),
            higher_timeframe_candles=tuple(
                _parse_candle(item)
                for item in payload.get("higher_timeframe_candles", [])
            ),
            outcome_candles=tuple(
                _parse_candle(item) for item in payload.get("outcome_candles", [])
            ),
            rule_context=payload.get("rule_context") or {},
            availability=payload.get("availability", ReplayAvailability.AVAILABLE),
            lifecycle_status=payload.get("lifecycle_status", ReplayLifecycleStatus.OPEN),
            final_outcome=payload.get("final_outcome", ReplayOutcome.UNKNOWN),
            outcome_at=(
                _parse_timestamp(payload["outcome_at"], "outcome_at")
                if payload.get("outcome_at")
                else None
            ),
            invalidation_reason=str(payload.get("invalidation_reason", "")),
            failure_reason=str(payload.get("failure_reason", "")),
        )
        supplied_hash = str(payload.get("snapshot_hash", "")).strip()
        if supplied_hash and supplied_hash != snapshot.snapshot_hash:
            raise ValueError("replay snapshot hash mismatch")
        return snapshot

    def __post_init__(self) -> None:
        object.__setattr__(self, "snapshot_id", _require_text(self.snapshot_id, "snapshot_id"))
        object.__setattr__(self, "signal_id", _require_text(self.signal_id, "signal_id"))
        object.__setattr__(self, "snapshot_version", _require_text(self.snapshot_version, "snapshot_version"))
        object.__setattr__(self, "symbol", _require_text(self.symbol, "symbol"))
        object.__setattr__(self, "source_timeframe", _require_text(self.source_timeframe, "source_timeframe"))
        source = _require_text(self.source, "source").upper()
        if source != "MT5":
            raise ValueError("replay snapshots must use MT5 candle data")
        object.__setattr__(self, "source", source)

        for name, value in (
            ("created_at", self.created_at),
            ("captured_at", self.captured_at),
            ("formation_cutoff_at", self.formation_cutoff_at),
        ):
            _require_utc(value, name)
        if self.outcome_at is not None:
            _require_utc(self.outcome_at, "outcome_at")
        if self.created_at < self.formation_cutoff_at:
            raise ValueError("created_at cannot precede formation_cutoff_at")
        if self.captured_at < self.created_at:
            raise ValueError("captured_at cannot precede created_at")

        if self.context_bars_before_parent < 0:
            raise ValueError("context_bars_before_parent cannot be negative")
        if self.outcome_forward_bars < 0:
            raise ValueError("outcome_forward_bars cannot be negative")

        for field_name, enum_type in (
            ("availability", ReplayAvailability),
            ("lifecycle_status", ReplayLifecycleStatus),
            ("final_outcome", ReplayOutcome),
        ):
            value = getattr(self, field_name)
            if not isinstance(value, enum_type):
                try:
                    object.__setattr__(self, field_name, enum_type(str(value)))
                except ValueError as exc:
                    raise ValueError(f"invalid {field_name}: {value}") from exc

        formation = tuple(self.formation_candles)
        higher_timeframe = tuple(self.higher_timeframe_candles)
        outcome = tuple(self.outcome_candles)
        object.__setattr__(self, "formation_candles", formation)
        object.__setattr__(self, "higher_timeframe_candles", higher_timeframe)
        object.__setattr__(self, "outcome_candles", outcome)

        if not formation:
            raise ValueError("formation_candles are required")
        if len(formation) < self.context_bars_before_parent + 3:
            raise ValueError("formation_candles do not contain the configured CRT sequence")

        role_indexes = {
            role: [index for index, item in enumerate(formation) if item.role is role]
            for role in (
                ReplayCandleRole.PARENT,
                ReplayCandleRole.SWEEP,
                ReplayCandleRole.CONFIRMATION,
            )
        }
        if any(len(indexes) != 1 for indexes in role_indexes.values()):
            raise ValueError("formation must contain exactly one parent, sweep, and confirmation candle")
        parent_index = role_indexes[ReplayCandleRole.PARENT][0]
        sweep_index = role_indexes[ReplayCandleRole.SWEEP][0]
        confirmation_index = role_indexes[ReplayCandleRole.CONFIRMATION][0]
        if parent_index != self.context_bars_before_parent:
            raise ValueError("parent candle is not after the configured context window")
        if (sweep_index, confirmation_index) != (parent_index + 1, parent_index + 2):
            raise ValueError("parent, sweep, and confirmation candles must be consecutive")
        if len(formation) != confirmation_index + 1:
            raise ValueError("formation may contain context candles before the three-candle CRT only")
        if any(item.role is not ReplayCandleRole.CONTEXT for item in formation[:parent_index]):
            raise ValueError("candles before the parent must have CONTEXT role")

        self._validate_source_candles(formation, self.source_timeframe)
        confirmation = formation[confirmation_index].candle
        if self.formation_cutoff_at < confirmation.close_time:
            raise ValueError("formation_cutoff_at cannot precede confirmation close")
        if any(item.candle.close_time > self.formation_cutoff_at for item in formation):
            raise ValueError("formation contains a candle after the formation cutoff")

        for item in higher_timeframe:
            if item.role is not ReplayCandleRole.HTF_CONTEXT:
                raise ValueError("higher-timeframe candles must have HTF_CONTEXT role")
            self._validate_candle_identity(item, self.symbol)
            if item.candle.close_time > self.formation_cutoff_at:
                raise ValueError(
                    "higher-timeframe candles cannot be after the formation cutoff"
                )

        if len(outcome) > self.outcome_forward_bars:
            raise ValueError("outcome_candles exceed outcome_forward_bars")
        self._validate_source_candles(outcome, self.source_timeframe, allow_outcome=True)
        if any(item.candle.open_time < self.formation_cutoff_at for item in outcome):
            raise ValueError("outcome candles must begin at or after the formation cutoff")

        if self.lifecycle_status is ReplayLifecycleStatus.RESOLVED:
            if self.final_outcome not in {
                ReplayOutcome.WIN,
                ReplayOutcome.LOSS,
                ReplayOutcome.BREAKEVEN,
            }:
                raise ValueError("RESOLVED replay requires a final outcome")
            if self.outcome_at is None:
                raise ValueError("RESOLVED replay requires outcome_at")
        elif self.final_outcome is not ReplayOutcome.UNKNOWN:
            raise ValueError("non-resolved replay cannot claim a final outcome")
        if self.final_outcome is ReplayOutcome.WIN and self.invalidation_reason:
            raise ValueError("winning replay cannot contain an invalidation reason")

        context = _json_value(dict(self.rule_context))
        object.__setattr__(self, "rule_context", context)
        object.__setattr__(self, "snapshot_hash", self._compute_hash())

    def _validate_candle_identity(self, item: ReplayCandle, symbol: str) -> None:
        if item.candle.symbol != symbol:
            raise ValueError("all snapshot candles must use the Passport symbol")

    def _validate_source_candles(
        self,
        items: tuple[ReplayCandle, ...],
        timeframe: str,
        *,
        allow_outcome: bool = False,
    ) -> None:
        previous_open: datetime | None = None
        for item in items:
            allowed_role = {ReplayCandleRole.CONTEXT}
            if allow_outcome:
                allowed_role = {ReplayCandleRole.OUTCOME}
            if item.role not in allowed_role and not (
                not allow_outcome
                and item.role
                in {
                    ReplayCandleRole.PARENT,
                    ReplayCandleRole.SWEEP,
                    ReplayCandleRole.CONFIRMATION,
                }
            ):
                raise ValueError("source-timeframe candle has an invalid replay role")
            self._validate_candle_identity(item, self.symbol)
            if item.candle.timeframe != timeframe:
                raise ValueError("source-timeframe candle does not match source_timeframe")
            if previous_open is not None and item.candle.open_time <= previous_open:
                raise ValueError("snapshot candles must be ordered oldest first")
            previous_open = item.candle.open_time

    def _immutable_payload(self) -> dict[str, Any]:
        formation = [item.to_payload() for item in self.formation_candles]
        higher_timeframe = [item.to_payload() for item in self.higher_timeframe_candles]
        parent_index = self.parent_index
        return {
            "snapshot_id": self.snapshot_id,
            "signal_id": self.signal_id,
            "snapshot_version": self.snapshot_version,
            "symbol": self.symbol,
            "source_timeframe": self.source_timeframe,
            "source": self.source,
            "created_at": _iso(self.created_at),
            # captured_at records when the repository stored the snapshot. It
            # is immutable once stored, but must not make an identical retry
            # look like changed formation evidence.
            "formation_cutoff_at": _iso(self.formation_cutoff_at),
            "context_bars_before_parent": self.context_bars_before_parent,
            "outcome_forward_bars": self.outcome_forward_bars,
            "formation_candles": formation,
            "higher_timeframe_candles": higher_timeframe,
            "parent_high": _decimal(self.parent_high, "parent_high"),
            "parent_low": _decimal(self.parent_low, "parent_low"),
            "parent_index": parent_index,
            "rule_context": self.rule_context,
        }

    def _compute_hash(self) -> str:
        canonical = json.dumps(
            self._immutable_payload(),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode("utf-8")
        return hashlib.sha256(canonical).hexdigest()

    @property
    def parent_index(self) -> int:
        return next(
            index
            for index, item in enumerate(self.formation_candles)
            if item.role is ReplayCandleRole.PARENT
        )

    @property
    def sweep_index(self) -> int:
        return self.parent_index + 1

    @property
    def confirmation_index(self) -> int:
        return self.parent_index + 2

    @property
    def parent_high(self) -> Decimal:
        return self.formation_candles[self.parent_index].candle.high

    @property
    def parent_low(self) -> Decimal:
        return self.formation_candles[self.parent_index].candle.low

    def to_payload(self) -> dict[str, Any]:
        """Return the validated storage/rendering payload."""
        payload = self._immutable_payload()
        payload.update(
            {
                "captured_at": _iso(self.captured_at),
                "availability": self.availability.value,
                "lifecycle_status": self.lifecycle_status.value,
                "final_outcome": self.final_outcome.value,
                "outcome_at": _iso(self.outcome_at) if self.outcome_at else None,
                "invalidation_reason": self.invalidation_reason,
                "failure_reason": self.failure_reason,
                "outcome_candles": [item.to_payload() for item in self.outcome_candles],
                "snapshot_hash": self.snapshot_hash,
            }
        )
        return payload
