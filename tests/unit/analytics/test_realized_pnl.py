from decimal import Decimal
from types import SimpleNamespace

from core.analytics.realized_pnl import calculate_price_pnl, reconcile_mt5_exit_deals


def test_price_pnl_uses_direction_tick_economics_and_signed_costs():
    result = calculate_price_pnl(
        entry_price=Decimal("1.1000"),
        exit_price=Decimal("1.1100"),
        direction="BUY",
        volume=Decimal("0.5"),
        tick_size=Decimal("0.0001"),
        tick_value=Decimal("1.00"),
        swap=Decimal("-0.50"),
        commission=Decimal("-1.00"),
        fee=Decimal("-0.25"),
    )

    assert result.status == "OK"
    assert result.gross_pnl == Decimal("50.0")
    assert result.costs == Decimal("-1.75")
    assert result.net_pnl == Decimal("48.25")


def test_sell_price_pnl_reverses_price_direction():
    result = calculate_price_pnl(
        entry_price=Decimal("1.1000"),
        exit_price=Decimal("1.0900"),
        direction="SELL",
        volume=Decimal("1"),
        tick_size=Decimal("0.0001"),
        tick_value=Decimal("1"),
    )

    assert result.net_pnl == Decimal("100")


def test_mt5_reconciliation_filters_to_matching_exit_deals_and_includes_costs():
    deals = [
        SimpleNamespace(magic=123, position_id=88, order=9, entry=0, profit=0, swap=0, commission=0, fee=0),
        SimpleNamespace(magic=123, position_id=88, order=9, entry=1, profit=40, swap=-2, commission=-1, fee=0),
        SimpleNamespace(magic=999, position_id=88, order=9, entry=1, profit=500, swap=0, commission=0, fee=0),
        SimpleNamespace(magic=123, position_id=77, order=9, entry=1, profit=500, swap=0, commission=0, fee=0),
    ]

    result = reconcile_mt5_exit_deals(
        deals,
        magic_number=123,
        position_id=88,
        order_id=9,
    )

    assert result.status == "OK"
    assert result.deals_count == 1
    assert result.gross_pnl == Decimal("40")
    assert result.costs == Decimal("-3")
    assert result.net_pnl == Decimal("37")


def test_mt5_reconciliation_returns_unknown_without_matching_exit():
    result = reconcile_mt5_exit_deals(
        [SimpleNamespace(magic=123, position_id=88, order=9, entry=0, profit=0)],
        magic_number=123,
        position_id=88,
        order_id=9,
    )

    assert result.status == "UNKNOWN"
    assert result.net_pnl is None
    assert result.reason == "no_matching_exit_deals"
