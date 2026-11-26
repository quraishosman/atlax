import logging
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock

from core.execution.trade_manager import TradeManager
from core.settings.profit_protection_config import ProfitProtectionConfig
from core.risk.prop_firm_protection import (
    PropFirmProtectionGuard,
    PropFirmProtectionSnapshot,
)


def _position(*, price_current: str, profit: str):
    return SimpleNamespace(
        ticket=17,
        symbol="EURUSD",
        direction=0,
        price_open=1.10000,
        price_current=Decimal(price_current),
        profit=Decimal(profit),
    )


def _manager(tmp_path: str) -> TradeManager:
    manager = TradeManager(
        state_file=str(tmp_path / "watches.json"),
        partial_profit_enabled=True,
        move_sl_to_breakeven=True,
    )
    manager.register(
        ticket=17,
        symbol="EURUSD",
        direction="BUY",
        entry_price=Decimal("1.10000"),
        midpoint=Decimal("1.10500"),
        extreme_target=Decimal("1.11000"),
        lot_size=Decimal("1.00"),
        magic_number=20260101,
    )
    return manager


def _mt5(position):
    mt5 = MagicMock()
    mt5.positions_get.return_value = [position]
    mt5.order_send.return_value = SimpleNamespace(retcode=10009)
    return mt5


def test_positive_profit_moves_stop_to_breakeven_before_tp1(tmp_path):
    manager = _manager(tmp_path)
    mt5 = _mt5(_position(price_current="1.10100", profit="0.50"))

    manager.check_and_manage(mt5, 20260101)

    request = mt5.order_send.call_args.args[0]
    assert request["action"] == 6
    assert request["sl"] == 1.1
    assert request["tp"] == 1.11


def test_protective_action_is_emitted_to_execution_quality_sink(tmp_path):
    events = []
    manager = TradeManager(
        state_file=str(tmp_path / "watches.json"),
        partial_profit_enabled=True,
        move_sl_to_breakeven=True,
        event_sink=events.append,
    )
    manager.register(
        ticket=17,
        symbol="EURUSD",
        direction="BUY",
        entry_price=Decimal("1.10000"),
        midpoint=Decimal("1.10500"),
        extreme_target=Decimal("1.11000"),
        lot_size=Decimal("1.00"),
        magic_number=20260101,
        execution_record_id="execution-1",
        candidate_id="candidate-1",
        timeframe="M15",
        profile="day_trader",
    )
    mt5 = _mt5(_position(price_current="1.10100", profit="0.50"))

    manager.check_and_manage(mt5, 20260101)

    assert len(events) == 1
    assert events[0].event_type == "PROTECTION_ACTION"
    assert events[0].execution_record_id == "execution-1"
    assert events[0].action == "BREAKEVEN"
    assert events[0].status == "APPLIED"
    assert events[0].broker_retcode == 10009


def test_tp1_touch_does_not_close_a_losing_position(tmp_path):
    manager = _manager(tmp_path)
    mt5 = _mt5(_position(price_current="1.10500", profit="-0.01"))

    manager.check_and_manage(mt5, 20260101)

    mt5.order_send.assert_not_called()


def test_profit_protection_uses_tp2_not_tp1_for_target_proximity(tmp_path):
    manager = _manager(tmp_path)
    manager._profit_protection_cfg = SimpleNamespace(
        enabled=True,
        target_proximity_fraction=0.80,
    )
    position = _position(price_current="1.10400", profit="4.00")
    watch = manager._watches[17]

    assert manager._active_protection_target(watch) == Decimal("1.11000")
    assert manager._target_progress(position, watch) == Decimal("0.4")
    assert manager._target_proximity_reached(position, watch) is False


def test_fill_beyond_tp1_disables_unfavorable_tp1_management(tmp_path):
    manager = _manager(tmp_path)
    mt5 = _mt5(_position(price_current="1.10700", profit="0.20"))
    manager._watches[17].midpoint = "1.10500"
    manager._watches[17].entry_price = "1.10600"

    manager.check_and_manage(mt5, 20260101)

    # Only the protective SL modification is sent; TP1 is behind the fill.
    assert mt5.order_send.call_count == 1
    assert mt5.order_send.call_args.args[0]["action"] == 6
    assert manager._watches[17].tp1_inapplicable is True


def test_mfe_profit_lock_is_shadow_only(tmp_path, caplog):
    caplog.set_level(logging.INFO, logger="atlax.execution.trade_manager")
    config = ProfitProtectionConfig(
        enabled=True,
        shadow_mode=True,
        activation_r=0.25,
        minimum_lock_r=0.05,
        maximum_giveback_r=0.20,
        confirmation_bars=2,
        momentum_loss_enabled=True,
        momentum_shadow_mode=True,
        momentum_source_timeframe="H1",
        minimum_current_profit_r=0.05,
        momentum_action="TIGHTEN_STOP",
        close_remaining_on_momentum_loss=False,
        include_transaction_cost_buffer=True,
    )
    manager = TradeManager(
        state_file=str(tmp_path / "watches.json"),
        partial_profit_enabled=False,
        move_sl_to_breakeven=False,
        profit_protection_config=config,
    )
    manager.register(
        ticket=17,
        symbol="EURUSD",
        direction="BUY",
        entry_price=Decimal("1.10000"),
        midpoint=Decimal("1.10500"),
        extreme_target=Decimal("1.11000"),
        lot_size=Decimal("1.00"),
        magic_number=20260101,
        original_stop=Decimal("1.09000"),
    )
    mt5 = _mt5(_position(price_current="1.11000", profit="10.00"))

    manager.check_and_manage(mt5, 20260101)
    mt5.positions_get.return_value = [
        _position(price_current="1.10800", profit="8.00")
    ]
    manager.check_and_manage(mt5, 20260101)

    mt5.order_send.assert_not_called()
    assert manager._watches[17].mfe_r == "1"
    assert manager._watches[17].shadow_lock_logged is True
    assert "profit_lock_would_apply" in caplog.text


def test_live_profit_lock_tightens_stop_and_preserves_tp2(tmp_path):
    config = ProfitProtectionConfig(
        enabled=True,
        shadow_mode=False,
        activation_r=0.25,
        minimum_lock_r=0.05,
        maximum_giveback_r=0.20,
        confirmation_bars=2,
        momentum_loss_enabled=False,
        momentum_shadow_mode=True,
        momentum_source_timeframe="H1",
        minimum_current_profit_r=0.05,
        momentum_action="TIGHTEN_STOP",
        close_remaining_on_momentum_loss=False,
        include_transaction_cost_buffer=True,
    )
    manager = TradeManager(
        state_file=str(tmp_path / "watches.json"),
        partial_profit_enabled=False,
        move_sl_to_breakeven=False,
        profit_protection_config=config,
    )
    manager.register(
        ticket=17,
        symbol="EURUSD",
        direction="BUY",
        entry_price=Decimal("1.10000"),
        midpoint=Decimal("1.10500"),
        extreme_target=Decimal("1.11000"),
        lot_size=Decimal("1.00"),
        magic_number=20260101,
        original_stop=Decimal("1.09000"),
    )
    mt5 = _mt5(_position(price_current="1.11000", profit="10.00"))
    manager.check_and_manage(mt5, 20260101)
    mt5.positions_get.return_value = [
        _position(price_current="1.10800", profit="8.00")
    ]

    manager.check_and_manage(mt5, 20260101)

    request = mt5.order_send.call_args.args[0]
    assert request["action"] == 6
    assert request["sl"] == 1.108
    assert request["tp"] == 1.11


def test_live_trailing_stop_ratchets_with_mfe_and_preserves_tp2(tmp_path):
    config = ProfitProtectionConfig(
        enabled=True,
        shadow_mode=False,
        activation_r=0.25,
        minimum_lock_r=0.05,
        maximum_giveback_r=0.20,
        confirmation_bars=2,
        momentum_loss_enabled=False,
        momentum_shadow_mode=False,
        momentum_source_timeframe="H1",
        minimum_current_profit_r=0.05,
        momentum_action="TIGHTEN_STOP",
        close_remaining_on_momentum_loss=False,
        include_transaction_cost_buffer=False,
        trailing_stop_enabled=True,
        trailing_activation_r=0.25,
        trailing_distance_r=0.20,
        trailing_step_r=0.05,
    )
    manager = TradeManager(
        state_file=str(tmp_path / "watches.json"),
        partial_profit_enabled=False,
        move_sl_to_breakeven=False,
        profit_protection_config=config,
    )
    manager.register(
        ticket=17,
        symbol="EURUSD",
        direction="BUY",
        entry_price=Decimal("1.10000"),
        midpoint=Decimal("1.10500"),
        extreme_target=Decimal("1.11000"),
        lot_size=Decimal("1.00"),
        magic_number=20260101,
        original_stop=Decimal("1.09000"),
    )
    mt5 = _mt5(_position(price_current="1.10800", profit="8.00"))

    manager.check_and_manage(mt5, 20260101)
    mt5.positions_get.return_value = [
        _position(price_current="1.10900", profit="9.00")
    ]
    manager.check_and_manage(mt5, 20260101)

    assert mt5.order_send.call_count == 2
    first_request = mt5.order_send.call_args_list[0].args[0]
    second_request = mt5.order_send.call_args_list[1].args[0]
    assert first_request["sl"] == 1.106
    assert second_request["sl"] == 1.107
    assert first_request["tp"] == 1.11
    assert second_request["tp"] == 1.11
    assert manager._watches[17].last_lock_r == "0.7"


def test_trailing_stop_retries_after_broker_rejection(tmp_path):
    config = ProfitProtectionConfig(
        enabled=True,
        shadow_mode=False,
        activation_r=0.25,
        minimum_lock_r=0.05,
        maximum_giveback_r=0.20,
        confirmation_bars=2,
        momentum_loss_enabled=False,
        momentum_shadow_mode=False,
        momentum_source_timeframe="H1",
        minimum_current_profit_r=0.05,
        momentum_action="TIGHTEN_STOP",
        close_remaining_on_momentum_loss=False,
        include_transaction_cost_buffer=False,
        trailing_stop_enabled=True,
        trailing_activation_r=0.25,
        trailing_distance_r=0.20,
        trailing_step_r=0.05,
    )
    manager = TradeManager(
        state_file=str(tmp_path / "watches.json"),
        partial_profit_enabled=False,
        move_sl_to_breakeven=False,
        profit_protection_config=config,
    )
    manager.register(
        ticket=17,
        symbol="EURUSD",
        direction="BUY",
        entry_price=Decimal("1.10000"),
        midpoint=Decimal("1.10500"),
        extreme_target=Decimal("1.11000"),
        lot_size=Decimal("1.00"),
        magic_number=20260101,
        original_stop=Decimal("1.09000"),
    )
    mt5 = _mt5(_position(price_current="1.10800", profit="8.00"))
    mt5.order_send.side_effect = [
        SimpleNamespace(retcode=10016),
        SimpleNamespace(retcode=10009),
    ]

    manager.check_and_manage(mt5, 20260101)
    manager.check_and_manage(mt5, 20260101)

    assert mt5.order_send.call_count == 2
    assert manager._watches[17].last_lock_r == "0.6"


def test_breakeven_uses_available_broker_cost_buffer(tmp_path):
    config = SimpleNamespace(include_transaction_cost_buffer=True)
    manager = TradeManager(
        state_file=str(tmp_path / "watches.json"),
        profit_protection_config=config,
    )
    mt5 = MagicMock()
    mt5.order_send.return_value = SimpleNamespace(retcode=10009)
    mt5.symbol_info.return_value = SimpleNamespace(
        trade_tick_value=Decimal("10"),
        trade_tick_size=Decimal("0.0001"),
    )
    position = SimpleNamespace(
        ticket=17,
        symbol="EURUSD",
        sl=Decimal("1.09000"),
        commission=Decimal("-2"),
        swap=Decimal("0"),
        volume=Decimal("1"),
    )

    moved = manager._move_sl_to_breakeven(
        mt5,
        position,
        Decimal("1.10000"),
        Decimal("1.11000"),
        20260101,
        direction="BUY",
    )

    assert moved is True
    request = mt5.order_send.call_args.args[0]
    assert request["sl"] == 1.10002
    assert request["tp"] == 1.11


def test_prop_firm_guard_allows_fresh_protective_breakeven(tmp_path):
    guard = PropFirmProtectionGuard(
        PropFirmProtectionSnapshot(
            profile_id="example-funded-usd",
            profile_version="2026-09-19.1",
            observed_at=datetime.now(timezone.utc),
            remaining_overall_buffer=Decimal("120"),
        )
    )
    manager = TradeManager(
        state_file=str(tmp_path / "watches.json"),
        prop_firm_protection_guard=guard,
    )
    mt5 = _mt5(_position(price_current="1.10100", profit="0.50"))

    moved = manager._move_sl_to_breakeven(
        mt5,
        mt5.positions_get()[0],
        Decimal("1.10000"),
        Decimal("1.11000"),
        20260101,
        direction="BUY",
    )

    assert moved is True
    assert mt5.order_send.call_args.args[0]["sl"] == 1.1


def test_prop_firm_guard_blocks_stale_protective_breakeven(tmp_path):
    guard = PropFirmProtectionGuard(
        PropFirmProtectionSnapshot(
            profile_id="example-funded-usd",
            profile_version="2026-09-19.1",
            observed_at=datetime(2020, 1, 1, tzinfo=timezone.utc),
            remaining_overall_buffer=Decimal("120"),
        )
    )
    manager = TradeManager(
        state_file=str(tmp_path / "watches.json"),
        prop_firm_protection_guard=guard,
    )
    mt5 = _mt5(_position(price_current="1.10100", profit="0.50"))

    moved = manager._move_sl_to_breakeven(
        mt5,
        mt5.positions_get()[0],
        Decimal("1.10000"),
        Decimal("1.11000"),
        20260101,
        direction="BUY",
    )

    assert moved is False
    mt5.order_send.assert_not_called()


def test_live_momentum_tightens_stop_after_closed_candle_confirmation(tmp_path):
    config = ProfitProtectionConfig(
        enabled=True,
        shadow_mode=False,
        activation_r=0.25,
        minimum_lock_r=0.05,
        maximum_giveback_r=0.20,
        confirmation_bars=2,
        momentum_loss_enabled=True,
        momentum_shadow_mode=False,
        momentum_source_timeframe="H1",
        minimum_current_profit_r=0.05,
        momentum_action="TIGHTEN_STOP",
        close_remaining_on_momentum_loss=False,
        include_transaction_cost_buffer=False,
    )
    manager = TradeManager(
        state_file=str(tmp_path / "watches.json"),
        partial_profit_enabled=False,
        move_sl_to_breakeven=False,
        profit_protection_config=config,
    )
    manager.register(
        ticket=17,
        symbol="EURUSD",
        direction="BUY",
        entry_price=Decimal("1.10000"),
        midpoint=Decimal("1.10500"),
        extreme_target=Decimal("1.11000"),
        lot_size=Decimal("1.00"),
        magic_number=20260101,
        original_stop=Decimal("1.09000"),
    )
    watch = manager._watches[17]
    watch.mfe_r = "1.0"
    candle_one = SimpleNamespace(
        open=Decimal("1.10200"), close=Decimal("1.10100"),
        is_closed=True, close_time="2026-09-19T10:00:00+00:00",
    )
    candle_two = SimpleNamespace(
        open=Decimal("1.10100"), close=Decimal("1.10050"),
        is_closed=True, close_time="2026-09-19T11:00:00+00:00",
    )
    manager.update_closed_candles("EURUSD", "H1", (candle_one, candle_two))
    mt5 = _mt5(_position(price_current="1.10800", profit="8.00"))

    manager._observe_momentum_loss(mt5, mt5.positions_get()[0], watch)

    request = mt5.order_send.call_args.args[0]
    assert request["action"] == 6
    assert request["sl"] == 1.108
    assert request["tp"] == 1.11


def test_closed_candle_momentum_loss_is_shadow_only(tmp_path, caplog):
    caplog.set_level(logging.INFO, logger="atlax.execution.trade_manager")
    config = ProfitProtectionConfig(
        enabled=True,
        shadow_mode=True,
        activation_r=0.25,
        minimum_lock_r=0.05,
        maximum_giveback_r=0.20,
        confirmation_bars=2,
        momentum_loss_enabled=True,
        momentum_shadow_mode=True,
        momentum_source_timeframe="H1",
        minimum_current_profit_r=0.05,
        momentum_action="TIGHTEN_STOP",
        close_remaining_on_momentum_loss=False,
        include_transaction_cost_buffer=True,
    )
    manager = TradeManager(
        state_file=str(tmp_path / "watches.json"),
        partial_profit_enabled=False,
        move_sl_to_breakeven=False,
        profit_protection_config=config,
    )
    manager.register(
        ticket=17,
        symbol="EURUSD",
        direction="BUY",
        entry_price=Decimal("1.10000"),
        midpoint=Decimal("1.10500"),
        extreme_target=Decimal("1.11000"),
        lot_size=Decimal("1.00"),
        magic_number=20260101,
        original_stop=Decimal("1.09000"),
    )
    candle_one = SimpleNamespace(
        open=Decimal("1.10200"), close=Decimal("1.10100"),
        is_closed=True, close_time="2026-09-19T10:00:00+00:00",
    )
    candle_two = SimpleNamespace(
        open=Decimal("1.10100"), close=Decimal("1.10050"),
        is_closed=True, close_time="2026-09-19T11:00:00+00:00",
    )
    manager.update_closed_candles("EURUSD", "H1", (candle_one, candle_two))
    mt5 = _mt5(_position(price_current="1.11000", profit="10.00"))
    manager.check_and_manage(mt5, 20260101)

    candle_three = SimpleNamespace(
        open=Decimal("1.10200"), close=Decimal("1.10100"),
        is_closed=True, close_time="2026-09-19T12:00:00+00:00",
    )
    manager.update_closed_candles(
        "EURUSD", "H1", (candle_one, candle_two, candle_three)
    )
    mt5.positions_get.return_value = [
        _position(price_current="1.10800", profit="8.00")
    ]
    manager.check_and_manage(mt5, 20260101)

    mt5.order_send.assert_not_called()
    assert "momentum_exit_would_apply" in caplog.text
