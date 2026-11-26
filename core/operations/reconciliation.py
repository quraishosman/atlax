"""Deterministic comparison of ATLAX execution records with MT5 state."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable


RECONCILIATION_STATUSES = frozenset(
    {
        "MATCHED",
        "MISSING_FROM_ATLAX",
        "MISSING_FROM_MT5",
        "MANUAL_OR_UNKNOWN",
        "CONFLICT",
        "UNKNOWN",
    }
)


@dataclass(frozen=True)
class ReconciliationItem:
    status: str
    subject: str
    execution_record_id: str = ""
    broker_id: str = ""
    reason: str = ""

    def to_dict(self) -> dict[str, str]:
        return {
            "status": self.status,
            "subject": self.subject,
            "execution_record_id": self.execution_record_id,
            "broker_id": self.broker_id,
            "reason": self.reason[:240],
        }


@dataclass(frozen=True)
class ReconciliationReport:
    status: str
    observed_at: datetime
    matched: int
    missing_from_atlax: int
    missing_from_mt5: int
    manual_or_unknown: int
    conflicts: int
    unknown: int
    items: tuple[ReconciliationItem, ...] = ()

    def __post_init__(self) -> None:
        if self.status not in RECONCILIATION_STATUSES:
            raise ValueError(f"Unsupported reconciliation status: {self.status}")
        if self.observed_at.tzinfo is None or self.observed_at.utcoffset() is None:
            raise ValueError("Reconciliation timestamp must be timezone-aware")

    @property
    def blocks_new_entries(self) -> bool:
        return self.status in {"CONFLICT", "UNKNOWN"}

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "observed_at": self.observed_at.astimezone(timezone.utc).isoformat(),
            "blocks_new_entries": self.blocks_new_entries,
            "counts": {
                "matched": self.matched,
                "missing_from_atlax": self.missing_from_atlax,
                "missing_from_mt5": self.missing_from_mt5,
                "manual_or_unknown": self.manual_or_unknown,
                "conflicts": self.conflicts,
                "unknown": self.unknown,
            },
            "items": [item.to_dict() for item in self.items[:100]],
        }


def _value(item: Any, *names: str) -> Any:
    if isinstance(item, dict):
        for name in names:
            if name in item:
                return item[name]
        return None
    for name in names:
        if hasattr(item, name):
            return getattr(item, name)
    return None


def _text(value: Any) -> str:
    return str(value or "").strip()


def _decimal(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return parsed if parsed.is_finite() else None


def _same_decimal(left: Any, right: Any) -> bool | None:
    parsed_left = _decimal(left)
    parsed_right = _decimal(right)
    if parsed_left is None or parsed_right is None:
        return None
    return parsed_left == parsed_right


def _broker_id(item: Any, *, pending: bool) -> str:
    names = ("ticket", "order") if pending else ("identifier", "ticket")
    for name in names:
        value = _value(item, name)
        if value not in (None, "", 0):
            return str(value)
    return ""


def _magic_matches(item: Any, magic_number: int) -> bool | None:
    value = _value(item, "magic")
    if value is None:
        return None
    try:
        return int(value) == int(magic_number)
    except (TypeError, ValueError):
        return None


def _direction_from_position(item: Any) -> str | None:
    value = _value(item, "type")
    try:
        numeric = int(value)
    except (TypeError, ValueError):
        return None
    if numeric == 0:
        return "BUY"
    if numeric == 1:
        return "SELL"
    return None


def _direction_from_record(record: dict[str, Any]) -> str | None:
    value = _text(record.get("direction")).upper()
    if value in {"BUY", "SELL"}:
        return value
    request = record.get("mt5_request")
    if isinstance(request, dict):
        order_type = _text(request.get("order_type")).upper()
        if "BUY" in order_type:
            return "BUY"
        if "SELL" in order_type:
            return "SELL"
    return None


def _compare_position(record: dict[str, Any], position: Any) -> tuple[str, str]:
    request = record.get("mt5_request")
    if not isinstance(request, dict):
        return "UNKNOWN", "execution request payload is unavailable"

    symbol = _text(request.get("symbol"))
    broker_symbol = _text(_value(position, "symbol"))
    if not symbol or not broker_symbol:
        return "UNKNOWN", "symbol is unavailable"
    if symbol != broker_symbol:
        return "CONFLICT", "broker symbol does not match execution record"

    expected_direction = _direction_from_record(record)
    actual_direction = _direction_from_position(position)
    if expected_direction is None or actual_direction is None:
        return "UNKNOWN", "position direction is unavailable"
    if expected_direction != actual_direction:
        return "CONFLICT", "broker direction does not match execution record"

    comparisons = (
        ("volume", request.get("volume"), _value(position, "volume")),
        ("sl", request.get("sl"), _value(position, "sl")),
        ("tp", request.get("tp"), _value(position, "tp")),
    )
    for field, expected, actual in comparisons:
        same = _same_decimal(expected, actual)
        if same is None:
            return "UNKNOWN", f"{field} is unavailable or malformed"
        if not same:
            return "CONFLICT", f"broker {field} does not match execution record"
    return "MATCHED", "broker position matches the execution record"


def _record_matches_broker(record: dict[str, Any], broker_item: Any, *, pending: bool) -> bool:
    expected_id = record.get("mt5_order_id") if pending else record.get("mt5_position_id")
    actual_id = _broker_id(broker_item, pending=pending)
    if expected_id not in (None, "", 0) and actual_id:
        return str(expected_id) == actual_id
    if pending:
        order_id = record.get("mt5_order_id")
        return order_id not in (None, "", 0) and actual_id == str(order_id)
    position_id = record.get("mt5_position_id")
    return position_id not in (None, "", 0) and actual_id == str(position_id)


def _overall_status(items: Iterable[ReconciliationItem]) -> str:
    statuses = [item.status for item in items]
    for status in ("CONFLICT", "UNKNOWN", "MISSING_FROM_ATLAX", "MISSING_FROM_MT5", "MANUAL_OR_UNKNOWN"):
        if status in statuses:
            return status
    return "MATCHED"


def reconcile_trade_state(
    execution_records: Iterable[dict[str, Any]],
    positions: Iterable[Any],
    pending_orders: Iterable[Any],
    *,
    magic_number: int,
    observed_at: datetime | None = None,
) -> ReconciliationReport:
    """Compare known active executions with broker positions and orders.

    This function never infers ownership from symbol, direction, or timing.
    A broker item without the ATLAX magic is outside the owned set; a broker
    item with the ATLAX magic but no matching execution is a missing ledger
    record. Missing broker identifiers remain UNKNOWN instead of being guessed.
    """
    now = observed_at or datetime.now(timezone.utc)
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("Reconciliation timestamp must be timezone-aware")

    records = [record for record in execution_records if isinstance(record, dict)]
    broker_positions = list(positions or ())
    broker_orders = list(pending_orders or ())
    items: list[ReconciliationItem] = []
    matched_position_ids: set[str] = set()
    matched_order_ids: set[str] = set()

    for record in records:
        record_id = _text(record.get("execution_record_id"))
        position = next(
            (item for item in broker_positions if _record_matches_broker(record, item, pending=False)),
            None,
        )
        order = next(
            (item for item in broker_orders if _record_matches_broker(record, item, pending=True)),
            None,
        )
        if position is not None:
            broker_id = _broker_id(position, pending=False)
            matched_position_ids.add(broker_id)
            status, reason = _compare_position(record, position)
            items.append(ReconciliationItem(status, "position", record_id, broker_id, reason))
            continue
        if order is not None:
            broker_id = _broker_id(order, pending=True)
            matched_order_ids.add(broker_id)
            items.append(
                ReconciliationItem(
                    "MATCHED",
                    "pending_order",
                    record_id,
                    broker_id,
                    "broker pending order matches the execution record",
                )
            )
            continue

        known_broker_id = any(
            record.get(key) not in (None, "", 0)
            for key in ("mt5_position_id", "mt5_order_id", "mt5_deal_id")
        )
        status = "MISSING_FROM_MT5" if known_broker_id else "UNKNOWN"
        reason = (
            "execution identifier is not present in current broker state"
            if known_broker_id
            else "execution has no broker identifier to reconcile"
        )
        items.append(ReconciliationItem(status, "execution", record_id, "", reason))

    for position in broker_positions:
        magic = _magic_matches(position, magic_number)
        broker_id = _broker_id(position, pending=False)
        if magic is None or not broker_id:
            items.append(
                ReconciliationItem(
                    "UNKNOWN", "position", broker_id=broker_id,
                    reason="broker position magic or identifier is unavailable",
                )
            )
        elif broker_id not in matched_position_ids:
            items.append(
                ReconciliationItem(
                    "MISSING_FROM_ATLAX", "position", broker_id=broker_id,
                    reason="ATLAX magic position has no matching execution record",
                )
            )

    for order in broker_orders:
        magic = _magic_matches(order, magic_number)
        broker_id = _broker_id(order, pending=True)
        if magic is None or not broker_id:
            items.append(
                ReconciliationItem(
                    "UNKNOWN", "pending_order", broker_id=broker_id,
                    reason="broker pending-order magic or identifier is unavailable",
                )
            )
        elif broker_id not in matched_order_ids:
            items.append(
                ReconciliationItem(
                    "MISSING_FROM_ATLAX", "pending_order", broker_id=broker_id,
                    reason="ATLAX magic pending order has no matching execution record",
                )
            )

    counts = {
        status: sum(item.status == status for item in items)
        for status in RECONCILIATION_STATUSES
    }
    return ReconciliationReport(
        status=_overall_status(items),
        observed_at=now,
        matched=counts["MATCHED"],
        missing_from_atlax=counts["MISSING_FROM_ATLAX"],
        missing_from_mt5=counts["MISSING_FROM_MT5"],
        manual_or_unknown=counts["MANUAL_OR_UNKNOWN"],
        conflicts=counts["CONFLICT"],
        unknown=counts["UNKNOWN"],
        items=tuple(items),
    )
