"""Append-only execution-quality facts emitted by the execution boundary."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Optional


class ExecutionQualityEventType:
    """Stable event types for execution and position lifecycle facts."""

    EXECUTION_RESULT = "EXECUTION_RESULT"
    POSITION_LINKED = "POSITION_LINKED"
    PROTECTION_ACTION = "PROTECTION_ACTION"
    POSITION_REALIZED = "POSITION_REALIZED"
    CIRCUIT_BREAKER = "CIRCUIT_BREAKER"


def _optional_decimal_string(value: Any) -> Optional[str]:
    if value is None or value == "":
        return None
    try:
        parsed = Decimal(str(value))
    except (TypeError, ValueError, ArithmeticError):
        return None
    return str(parsed) if parsed.is_finite() else None


@dataclass(frozen=True)
class ExecutionQualityEvent:
    """Sanitized broker fact; never contains credentials or raw MT5 payloads."""

    event_id: str
    event_type: str
    execution_record_id: str = ""
    candidate_id: str = ""
    profile: str = ""
    symbol: str = ""
    timeframe: str = ""
    direction: str = ""
    position_id: Optional[int] = None
    requested_price: Optional[str] = None
    actual_price: Optional[str] = None
    requested_volume: Optional[str] = None
    actual_volume: Optional[str] = None
    spread: Optional[str] = None
    slippage: Optional[str] = None
    stop_loss: Optional[str] = None
    take_profit: Optional[str] = None
    broker_retcode: Optional[int] = None
    broker_comment: Optional[str] = None
    action: str = ""
    status: str = "UNKNOWN"
    latency_ms: Optional[int] = None
    gross_pnl: Optional[str] = None
    costs: Optional[str] = None
    net_pnl: Optional[str] = None
    reason: str = ""
    occurred_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not str(self.event_id).strip():
            raise ValueError("event_id is required")
        if not str(self.event_type).strip():
            raise ValueError("event_type is required")
        if self.occurred_at.tzinfo is None or self.occurred_at.utcoffset() is None:
            raise ValueError("occurred_at must be timezone-aware")
        if self.position_id is not None and int(self.position_id) < 0:
            raise ValueError("position_id must be non-negative")
        if self.latency_ms is not None and int(self.latency_ms) < 0:
            raise ValueError("latency_ms must be non-negative")

    @classmethod
    def new(cls, event_type: str, **values: Any) -> "ExecutionQualityEvent":
        """Create an event with a non-guessable id and UTC timestamp."""
        values.setdefault("event_id", uuid.uuid4().hex)
        values.setdefault("occurred_at", datetime.now(timezone.utc))
        return cls(event_type=event_type, **values)

    @classmethod
    def from_execution_record(cls, record: Any) -> "ExecutionQualityEvent":
        request = getattr(record, "mt5_request", None)
        attempted_at = getattr(record, "attempted_at", None)
        completed_at = getattr(record, "completed_at", None)
        latency_ms = None
        if isinstance(attempted_at, datetime) and isinstance(completed_at, datetime):
            latency_ms = max(0, int((completed_at - attempted_at).total_seconds() * 1000))
        return cls.new(
            ExecutionQualityEventType.EXECUTION_RESULT,
            execution_record_id=str(getattr(record, "record_id", "")),
            candidate_id=str(getattr(record, "candidate_id", "")),
            profile=str(getattr(record, "profile", "")),
            symbol=str(getattr(record, "symbol", "")),
            timeframe=str(getattr(record, "timeframe", "")),
            direction=str(getattr(getattr(record, "direction", ""), "value", getattr(record, "direction", ""))),
            requested_price=getattr(request, "price", None),
            requested_volume=getattr(request, "volume", None),
            stop_loss=getattr(request, "sl", None),
            take_profit=getattr(request, "tp", None),
            broker_retcode=getattr(record, "order_send_retcode", None),
            broker_comment=str(getattr(record, "broker_comment", "") or "")[:240] or None,
            status=str(getattr(getattr(record, "status", "UNKNOWN"), "value", getattr(record, "status", "UNKNOWN"))),
            latency_ms=latency_ms,
            reason=str(getattr(record, "error_reason", "") or "")[:240],
            occurred_at=completed_at or datetime.now(timezone.utc),
        )

    def to_dict(self) -> dict[str, Any]:
        result = {
            "event_id": self.event_id,
            "event_type": self.event_type,
            "execution_record_id": self.execution_record_id,
            "candidate_id": self.candidate_id,
            "profile": self.profile,
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "direction": self.direction,
            "position_id": self.position_id,
            "requested_price": self.requested_price,
            "actual_price": self.actual_price,
            "requested_volume": self.requested_volume,
            "actual_volume": self.actual_volume,
            "spread": self.spread,
            "slippage": self.slippage,
            "stop_loss": self.stop_loss,
            "take_profit": self.take_profit,
            "broker_retcode": self.broker_retcode,
            "broker_comment": self.broker_comment,
            "action": self.action,
            "status": self.status,
            "latency_ms": self.latency_ms,
            "gross_pnl": self.gross_pnl,
            "costs": self.costs,
            "net_pnl": self.net_pnl,
            "reason": self.reason[:240],
            "occurred_at": self.occurred_at.isoformat(),
            "metadata": dict(self.metadata),
        }
        return result


__all__ = ["ExecutionQualityEvent", "ExecutionQualityEventType"]
