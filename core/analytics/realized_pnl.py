"""Broker-authoritative realized PnL calculations.

This module is for execution-side reconciliation only.  It does not score
signals, change risk, or decide whether a position should be closed.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable


_EXIT_DEAL_ENTRY_CODES = frozenset({1, 3})  # MT5 ENTRY_OUT / ENTRY_OUT_BY


@dataclass(frozen=True)
class RealizedPnl:
    status: str
    gross_pnl: Decimal | None
    costs: Decimal | None
    net_pnl: Decimal | None
    deals_count: int
    reason: str = ""


def _decimal(value: Any) -> Decimal | None:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return result if result.is_finite() else None


def calculate_price_pnl(
    *,
    entry_price: Decimal,
    exit_price: Decimal,
    direction: str,
    volume: Decimal,
    tick_size: Decimal,
    tick_value: Decimal,
    swap: Decimal = Decimal("0"),
    commission: Decimal = Decimal("0"),
    fee: Decimal = Decimal("0"),
) -> RealizedPnl:
    """Calculate net cash PnL from broker tick economics and signed costs."""
    values = (
        entry_price, exit_price, volume, tick_size, tick_value,
        swap, commission, fee,
    )
    if not all(isinstance(value, Decimal) and value.is_finite() for value in values):
        return RealizedPnl("UNKNOWN", None, None, None, 0, "non_finite_price_or_cost")
    if volume <= 0 or tick_size <= 0 or tick_value <= 0:
        return RealizedPnl("UNKNOWN", None, None, None, 0, "invalid_broker_tick_economics")

    normalized_direction = str(direction or "").upper()
    if normalized_direction == "BUY":
        price_delta = exit_price - entry_price
    elif normalized_direction == "SELL":
        price_delta = entry_price - exit_price
    else:
        return RealizedPnl("UNKNOWN", None, None, None, 0, "invalid_direction")

    gross = (price_delta / tick_size) * tick_value * volume
    costs = swap + commission + fee
    return RealizedPnl(
        status="OK",
        gross_pnl=gross,
        costs=costs,
        net_pnl=gross + costs,
        deals_count=1,
    )


def reconcile_mt5_exit_deals(
    deals: Iterable[Any],
    *,
    magic_number: int,
    position_id: int | None = None,
    order_id: int | None = None,
) -> RealizedPnl:
    """Sum broker-reported exit deals for one ATLAX execution context.

    MT5's reported ``profit`` plus signed ``swap``, ``commission`` and ``fee``
    is the authoritative cash result.  Entry deals and unrelated magic/order/
    position records are excluded.  Any malformed matching deal makes the
    reconciliation UNKNOWN rather than producing a partial result.
    """
    gross = Decimal("0")
    costs = Decimal("0")
    count = 0
    for deal in deals or ():
        try:
            if int(getattr(deal, "magic", -1)) != int(magic_number):
                continue
            if position_id is not None and int(getattr(deal, "position_id", -1)) != int(position_id):
                continue
            if order_id is not None and int(getattr(deal, "order", -1)) != int(order_id):
                continue
            if int(getattr(deal, "entry", -1)) not in _EXIT_DEAL_ENTRY_CODES:
                continue
        except (TypeError, ValueError):
            continue

        profit = _decimal(getattr(deal, "profit", None))
        swap = _decimal(getattr(deal, "swap", 0))
        commission = _decimal(getattr(deal, "commission", 0))
        fee = _decimal(getattr(deal, "fee", 0))
        if None in (profit, swap, commission, fee):
            return RealizedPnl("UNKNOWN", None, None, None, count, "malformed_exit_deal")
        gross += profit
        costs += swap + commission + fee
        count += 1

    if count == 0:
        return RealizedPnl("UNKNOWN", None, None, None, 0, "no_matching_exit_deals")
    return RealizedPnl(
        status="OK",
        gross_pnl=gross,
        costs=costs,
        net_pnl=gross + costs,
        deals_count=count,
    )
