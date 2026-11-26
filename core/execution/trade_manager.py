"""
ATLAX Trade Manager — Partial Profit Engine.

Manages open ATLAX positions after execution to implement the partial-profit
lifecycle:

  Step 1 (executed by ExecutionEngine): Full lot placed at market, SL at
    invalidation level, and TP2 at the approved extreme target. Midpoint and
    extreme targets are recorded here.

  Step 2 (this module, called every scan cycle):
    - If lot_size >= 2 × min_lot:
        • When price hits the midpoint_target → close 50% of the lot.
        • Move remaining position SL to entry price (breakeven).
        • Let the remaining 50% run with no TP, protected by breakeven SL.
    - If lot_size < 2 × min_lot (cannot split):
        • When price hits the midpoint_target → close 100% of the lot.
        • Treat midpoint as the only TP.

Authority: docs/11_EXECUTION_ENGINE.md (trade management phase)
           docs/10_RISK_ENGINE.md (no overriding risk controls)
           docs/13_CONFIGURATION.md (partial_profit_enabled, partial_profit_ratio)

Architectural boundaries:
  - TradeManager NEVER places new trades or overrides SL below approved level.
  - TradeManager ONLY modifies or closes positions that were opened by ATLAX
    (identified by magic_number match).
  - All MT5 calls are logged; all failures are non-fatal (position stays open).
  - Min lot is read from the MT5 symbol info; never hardcoded.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from decimal import ROUND_DOWN, Decimal
from typing import Any, Optional

from core.risk.prop_firm_protection import ProtectionAction, ProtectionRequest
from core.models.execution_quality import (
    ExecutionQualityEvent,
    ExecutionQualityEventType,
)
from core.market_data.mt5_symbol import resolve_mt5_symbol

logger = logging.getLogger("atlax.execution.trade_manager")

_STATE_FILE = "data/partial_profit_watches.json"

# MT5 TRADE_ACTION constants
_TRADE_ACTION_DEAL = 1   # Market order (used for partial close)
_TRADE_ACTION_SLTP = 6   # Modify SL/TP on open position

_ORDER_TYPE_BUY  = 0
_ORDER_TYPE_SELL = 1


@dataclass
class PartialProfitWatch:
    """
    Tracks one ATLAX open position for partial-profit management.

    Fields:
        ticket:         MT5 position ticket (unique identifier).
        symbol:         Instrument symbol (e.g., "EURUSD").
        direction:      "BUY" or "SELL".
        entry_price:    Actual fill price — used as breakeven SL level.
        midpoint:       Price level where 50% (or all, if too small) is closed.
        extreme_target: Final target for the remaining position (informational).
        original_lot:   Full lot size placed at open.
        magic_number:   ATLAX magic number (used to identify our positions).
        original_stop:  Approved initial stop used as the one-R risk distance.
        partial_taken:  True once the partial close has been executed.
        mfe_r:          Maximum favorable excursion measured in R.
        shadow_lock_logged: True once the Phase 2 counterfactual is recorded.
        last_lock_r:    Highest live protective lock applied in R.
        last_lock_price: Price of the highest live protective lock.
        last_momentum_close_time: Last source candle evaluated for Phase 3.
        registered_at:  UTC ISO timestamp of registration.
    """
    ticket:         int
    symbol:         str
    direction:      str       # "BUY" or "SELL"
    entry_price:    str       # Decimal as string
    midpoint:       str       # Decimal as string
    extreme_target: str       # Decimal as string
    original_lot:   str       # Decimal as string
    magic_number:   int
    original_stop:  str       = ""
    partial_taken:  bool      = False
    breakeven_moved: bool     = False
    tp1_inapplicable: bool    = False
    mfe_r:           str       = "0"
    shadow_lock_logged: bool   = False
    last_lock_r:     str       = "0"
    last_lock_price: str       = ""
    last_momentum_close_time: str = ""
    registered_at:  str       = ""
    execution_record_id: str  = ""
    candidate_id:   str       = ""
    timeframe:       str       = ""
    profile:         str       = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "PartialProfitWatch":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


class TradeManager:
    """
    Manages partial-profit lifecycle for ATLAX open positions.

    Usage (scanner calls on every cycle):
        trade_manager.check_and_manage(mt5, magic_number, filling_mode)
    """

    def __init__(
        self,
        state_file: str = _STATE_FILE,
        partial_profit_enabled: bool = True,
        partial_profit_ratio: float = 0.5,
        move_sl_to_breakeven: bool = True,
        deviation_points: int = 20,
        filling_mode: str = "IOC",
        state_store=None,
        profit_protection_config=None,
        prop_firm_protection_guard=None,
        monitoring=None,
        event_sink=None,
    ) -> None:
        self._state_file          = state_file
        self._state_store         = state_store
        self._partial_enabled     = partial_profit_enabled
        self._partial_ratio       = Decimal(str(partial_profit_ratio))
        self._move_sl_to_be       = move_sl_to_breakeven
        self._deviation           = deviation_points
        self._filling_mode        = filling_mode
        self._profit_protection_cfg = profit_protection_config
        self._prop_firm_protection_guard = prop_firm_protection_guard
        self._monitoring = monitoring
        self._event_sink = event_sink
        self._watches: dict[int, PartialProfitWatch] = {}  # ticket → watch
        self._momentum_candles: dict[tuple[str, str], tuple] = {}
        self._load_state()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_prop_firm_protection_guard(self, guard) -> None:
        """Replace the optional account-mode protection authorization snapshot."""
        self._prop_firm_protection_guard = guard

    def _observe_protective_action(self, action: str) -> None:
        observer = getattr(self._monitoring, "observe_protective_action", None)
        if callable(observer):
            observer(action)

    def _emit_quality_event(
        self,
        watch: PartialProfitWatch,
        position: Any,
        *,
        action: str,
        status: str,
        broker_retcode: Optional[int] = None,
        requested_price: Optional[Decimal] = None,
        actual_price: Optional[Decimal] = None,
        requested_volume: Optional[Decimal] = None,
        stop_loss: Optional[Decimal] = None,
        take_profit: Optional[Decimal] = None,
        reason: str = "",
    ) -> None:
        """Forward a sanitized broker action to the execution-quality ledger."""
        if not callable(self._event_sink):
            return
        try:
            raw_position_id = getattr(
                position,
                "identifier",
                getattr(position, "ticket", None),
            )
            position_id = int(raw_position_id) if raw_position_id is not None else None
            current_price = (
                actual_price
                if actual_price is not None
                else Decimal(str(getattr(position, "price_current")))
            )
            actual_volume = getattr(position, "volume", None)
            event = ExecutionQualityEvent.new(
                ExecutionQualityEventType.PROTECTION_ACTION,
                execution_record_id=watch.execution_record_id,
                candidate_id=watch.candidate_id,
                profile=watch.profile,
                symbol=watch.symbol,
                timeframe=watch.timeframe,
                direction=watch.direction,
                position_id=position_id,
                requested_price=(str(requested_price) if requested_price is not None else None),
                actual_price=str(current_price) if current_price is not None else None,
                requested_volume=(str(requested_volume) if requested_volume is not None else None),
                actual_volume=(str(actual_volume) if actual_volume is not None else None),
                stop_loss=(str(stop_loss) if stop_loss is not None else None),
                take_profit=(str(take_profit) if take_profit is not None else None),
                broker_retcode=broker_retcode,
                action=str(action),
                status=str(status),
                reason=str(reason)[:240],
            )
            self._event_sink(event)
        except Exception as exc:  # noqa: BLE001 - telemetry cannot change trade safety
            logger.warning(
                '{"event":"execution_quality_event_failed","ticket":%s,'
                '"error":"%s"}',
                getattr(position, "ticket", "UNKNOWN"),
                str(exc)[:160],
            )

    def register(
        self,
        ticket: int,
        symbol: str,
        direction: str,
        entry_price: Decimal,
        midpoint: Decimal,
        extreme_target: Decimal,
        lot_size: Decimal,
        magic_number: int,
        original_stop: Optional[Decimal] = None,
        execution_record_id: str = "",
        candidate_id: str = "",
        timeframe: str = "",
        profile: str = "",
    ) -> None:
        """
        Register a newly executed position for partial-profit tracking.

        Called by the scanner immediately after execution succeeds.
        """
        watch = PartialProfitWatch(
            ticket         = ticket,
            symbol         = symbol,
            direction      = direction,
            entry_price    = str(entry_price),
            midpoint       = str(midpoint),
            extreme_target = str(extreme_target),
            original_lot   = str(lot_size),
            magic_number   = magic_number,
            original_stop  = str(original_stop) if original_stop is not None else "",
            partial_taken  = False,
            registered_at  = datetime.now(timezone.utc).isoformat(),
            execution_record_id=str(execution_record_id),
            candidate_id=str(candidate_id),
            timeframe=str(timeframe),
            profile=str(profile),
        )
        self._watches[ticket] = watch
        self._save_state()
        logger.info(
            '{"event":"profit_protection_position_registered",'
            '"ticket":%d,"symbol":"%s","direction":"%s",'
            '"entry":"%s","original_stop":"%s","midpoint":"%s",'
            '"extreme":"%s","lot":"%s"}',
            ticket, symbol, direction, entry_price, original_stop or "UNKNOWN",
            midpoint, extreme_target, lot_size,
        )

    def update_closed_candles(
        self,
        symbol: str,
        timeframe: str,
        candles,
    ) -> None:
        """Provide the latest closed source candles for Phase 3 observation."""
        self._momentum_candles[(str(symbol).upper(), str(timeframe).upper())] = tuple(
            candle for candle in (candles or ())
            if bool(getattr(candle, "is_closed", False))
        )

    def check_and_manage(self, mt5: Any, magic_number: int) -> None:
        """
        Check all tracked positions and execute partial close when midpoint is hit.

        Called every scan cycle from scanner._process_instrument.
        mt5 must be the already-imported MetaTrader5 module (not None).
        """
        shadow_enabled = bool(
            self._profit_protection_cfg is not None
            and getattr(self._profit_protection_cfg, "enabled", False)
            and (
                getattr(self._profit_protection_cfg, "shadow_mode", False)
                or (
                    getattr(self._profit_protection_cfg, "momentum_loss_enabled", False)
                    and getattr(self._profit_protection_cfg, "momentum_shadow_mode", False)
                )
            )
        )
        profit_protection_enabled = bool(
            self._profit_protection_cfg is not None
            and getattr(self._profit_protection_cfg, "enabled", False)
        )
        if (
            not self._partial_enabled
            and not self._move_sl_to_be
            and not shadow_enabled
            and not profit_protection_enabled
        ):
            return
        if not self._watches:
            return

        # MT5's Python positions_get API does not provide a portable magic
        # keyword filter. Fetch the snapshot, then enforce ownership locally.
        all_positions = mt5.positions_get()
        if all_positions is None:
            logger.warning('{"event":"trade_manager_positions_fetch_failed"}')
            return
        positions = tuple(
            position
            for position in all_positions
            if int(getattr(position, "magic", magic_number) or magic_number) == magic_number
        )

        open_tickets = {p.ticket for p in positions}

        # Remove watches for positions that are no longer open
        closed_tickets = [t for t in self._watches if t not in open_tickets]
        for t in closed_tickets:
            logger.info(
                '{"event":"partial_watch_removed","ticket":%d,"reason":"position_closed"}', t
            )
            del self._watches[t]

        if closed_tickets:
            self._save_state()

        # Check each tracked position against its midpoint
        for position in positions:
            ticket = position.ticket
            if ticket not in self._watches:
                continue

            watch = self._watches[ticket]

            # Protect a position as soon as the broker reports positive live
            # profit only after the active approved target is near. This keeps
            # a small positive tick from turning into an early stop-out.
            if self._move_sl_to_be and not watch.breakeven_moved:
                if (
                    self._is_position_profitable(position, watch)
                    and self._target_proximity_reached(position, watch)
                ):
                    logger.info(
                        '{"event":"breakeven_positive_profit_detected",'
                        '"ticket":%d,"symbol":"%s","profit":"%s"}',
                        ticket,
                        watch.symbol,
                        str(getattr(position, "profit", "UNKNOWN")),
                    )
                    moved = self._move_sl_to_breakeven(
                        mt5,
                        position,
                        Decimal(watch.entry_price),
                        Decimal(watch.extreme_target),
                        magic_number,
                        direction=watch.direction,
                        watch=watch,
                    )
                    if moved:
                        watch = PartialProfitWatch(
                            **{**watch.to_dict(), "breakeven_moved": True}
                        )
                        self._watches[ticket] = watch
                        self._save_state()

            # MFE and momentum protection are configuration-gated. In live
            # mode the approved TIGHTEN_STOP path may submit only a protective
            # SL update; it never creates or reverses a position.
            watch = self._observe_mfe_profit_lock(mt5, position, watch)
            watch = self._observe_momentum_loss(mt5, position, watch)
            self._watches[ticket] = watch

            if not self._partial_enabled:
                continue

            if watch.partial_taken or watch.tp1_inapplicable:
                continue  # Already handled or TP1 cannot be profitable

            midpoint      = Decimal(watch.midpoint)
            current_bid   = Decimal(str(position.price_current))
            direction     = watch.direction

            tp1_is_favorable = (
                (direction == "BUY" and midpoint > Decimal(watch.entry_price))
                or (direction == "SELL" and midpoint < Decimal(watch.entry_price))
            )
            if not tp1_is_favorable:
                logger.info(
                    '{"event":"tp1_partial_close_skipped_unfavorable_fill",'
                    '"ticket":%d,"symbol":"%s","entry":"%s",'
                    '"tp1":"%s","direction":"%s"}',
                    ticket,
                    watch.symbol,
                    watch.entry_price,
                    watch.midpoint,
                    direction,
                )
                self._watches[ticket] = PartialProfitWatch(
                    **{**watch.to_dict(), "tp1_inapplicable": True}
                )
                self._save_state()
                continue

            midpoint_hit = (
                (direction == "BUY"  and current_bid >= midpoint) or
                (direction == "SELL" and current_bid <= midpoint)
            )

            if not midpoint_hit:
                continue

            if not self._is_position_profitable(position, watch):
                logger.debug(
                    '{"event":"tp1_partial_close_skipped_not_profitable",'
                    '"ticket":%d,"symbol":"%s","profit":"%s"}',
                    ticket,
                    watch.symbol,
                    str(getattr(position, "profit", "UNKNOWN")),
                )
                continue

            # Midpoint is hit — execute partial close
            self._execute_partial(mt5, position, watch, magic_number)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _is_position_profitable(position: Any, watch: PartialProfitWatch) -> bool:
        """Use broker PnL when available, with a price-only adapter fallback."""
        broker_profit = getattr(position, "profit", None)
        if broker_profit is not None:
            try:
                return Decimal(str(broker_profit)) > 0
            except (TypeError, ValueError, ArithmeticError):
                pass

        try:
            current = Decimal(str(position.price_current))
            entry = Decimal(watch.entry_price)
        except (AttributeError, TypeError, ValueError, ArithmeticError):
            return False
        if watch.direction == "BUY":
            return current > entry
        if watch.direction == "SELL":
            return current < entry
        return False

    def _active_protection_target(self, watch: PartialProfitWatch) -> Decimal:
        """Return the approved target currently relevant to protection.

        Profit protection always measures proximity to TP2, the hard final
        take-profit target. TP1 remains a partial-close target only: it must
        never activate a trailing or giveback stop before the position is near
        the final approved target.
        """
        return Decimal(watch.extreme_target)

    def _target_progress(self, position: Any, watch: PartialProfitWatch) -> Optional[Decimal]:
        """Return favorable progress toward the active approved target [0, 1]."""
        try:
            entry = Decimal(watch.entry_price)
            current = Decimal(str(position.price_current))
            target = self._active_protection_target(watch)
            target_distance = (
                target - entry if watch.direction == "BUY" else entry - target
            )
            favorable_distance = (
                current - entry if watch.direction == "BUY" else entry - current
            )
            if target_distance <= 0 or not target_distance.is_finite():
                return None
            progress = favorable_distance / target_distance
            return max(Decimal("0"), min(Decimal("1"), progress))
        except (AttributeError, TypeError, ValueError, ArithmeticError):
            return None

    def _target_proximity_reached(
        self,
        position: Any,
        watch: PartialProfitWatch,
    ) -> bool:
        """Gate protective stop tightening until the active target is near."""
        config = self._profit_protection_cfg
        if config is None or not getattr(config, "enabled", False):
            return True
        try:
            threshold = Decimal(
                str(getattr(config, "target_proximity_fraction", Decimal("0")))
            )
        except (TypeError, ValueError, ArithmeticError):
            return False
        progress = self._target_progress(position, watch)
        return (
            progress is not None
            and threshold.is_finite()
            and Decimal("0") <= threshold <= Decimal("1")
            and progress >= threshold
        )

    def _move_sl_to_profit_lock(
        self,
        mt5: Any,
        position: Any,
        direction: str,
        lock_price: Decimal,
        extreme_target: Decimal,
        magic_number: int,
        action: ProtectionAction = ProtectionAction.PROFIT_LOCK,
        watch: Optional[PartialProfitWatch] = None,
    ) -> bool:
        """Apply a protective lock without moving the broker stop backward."""
        try:
            current_price = Decimal(str(position.price_current))
            current_sl = Decimal(str(getattr(position, "sl", 0) or 0))
            if not current_price.is_finite() or not lock_price.is_finite():
                raise ArithmeticError("non-finite lock price")
        except (AttributeError, TypeError, ValueError, ArithmeticError):
            logger.warning(
                '{"event":"profit_lock_apply_failed","ticket":%d,'
                '"symbol":"%s","reason":"invalid_price_state"}',
                position.ticket,
                position.symbol,
            )
            return False

        if direction == "BUY":
            if lock_price > current_price:
                logger.info(
                    '{"event":"profit_lock_deferred","ticket":%d,'
                    '"symbol":"%s","reason":"lock_above_current_market"}',
                    position.ticket,
                    position.symbol,
                )
                return False
            if current_sl > 0 and lock_price <= current_sl:
                logger.info(
                    '{"event":"profit_lock_already_protected","ticket":%d,'
                    '"symbol":"%s","current_sl":"%s","lock_price":"%s"}',
                    position.ticket,
                    position.symbol,
                    current_sl,
                    lock_price,
                )
                return True
        elif direction == "SELL":
            if lock_price < current_price:
                logger.info(
                    '{"event":"profit_lock_deferred","ticket":%d,'
                    '"symbol":"%s","reason":"lock_below_current_market"}',
                    position.ticket,
                    position.symbol,
                )
                return False
            if current_sl > 0 and lock_price >= current_sl:
                logger.info(
                    '{"event":"profit_lock_already_protected","ticket":%d,'
                    '"symbol":"%s","current_sl":"%s","lock_price":"%s"}',
                    position.ticket,
                    position.symbol,
                    current_sl,
                    lock_price,
                )
                return True
        else:
            logger.warning(
                '{"event":"profit_lock_apply_failed","ticket":%d,'
                '"symbol":"%s","reason":"unknown_position_direction"}',
                position.ticket,
                position.symbol,
            )
            return False

        if not self._authorize_protection(
            action=action,
            position=position,
            direction=direction,
            current_sl=current_sl,
            requested_sl=lock_price,
        ):
            if watch is not None:
                self._emit_quality_event(
                    watch,
                    position,
                    action=action.value,
                    status="BLOCKED",
                    requested_price=lock_price,
                    actual_price=current_price,
                    stop_loss=lock_price,
                    take_profit=extreme_target,
                    reason="protection_authorization_blocked",
                )
            return False

        request = {
            "action": _TRADE_ACTION_SLTP,
            "symbol": position.symbol,
            "sl": float(lock_price),
            "tp": float(extreme_target),
            "position": position.ticket,
            "magic": magic_number,
        }
        result = mt5.order_send(request)
        if result is None or result.retcode not in (10009, 10008):
            err = mt5.last_error() if result is None else result.retcode
            logger.warning(
                '{"event":"profit_lock_apply_failed","ticket":%d,'
                '"symbol":"%s","retcode":"%s"}',
                position.ticket,
                position.symbol,
                err,
            )
            if watch is not None:
                self._emit_quality_event(
                    watch,
                    position,
                    action=action.value,
                    status="FAILED",
                    broker_retcode=(int(result.retcode) if result is not None else None),
                    requested_price=lock_price,
                    actual_price=current_price,
                    stop_loss=lock_price,
                    take_profit=extreme_target,
                    reason=f"broker_protection_update_failed:{err}",
                )
            return False

        logger.info(
            '{"event":"profit_lock_applied","ticket":%d,'
            '"symbol":"%s","lock_price":"%s","tp2":"%s"}',
            position.ticket,
            position.symbol,
            lock_price,
            extreme_target,
        )
        self._observe_protective_action(action.value)
        if watch is not None:
            self._emit_quality_event(
                watch,
                position,
                action=action.value,
                status="APPLIED",
                broker_retcode=int(result.retcode),
                requested_price=lock_price,
                actual_price=current_price,
                stop_loss=lock_price,
                take_profit=extreme_target,
            )
        return True

    def _observe_mfe_profit_lock(
        self,
        mt5: Any,
        position: Any,
        watch: PartialProfitWatch,
    ) -> PartialProfitWatch:
        """Track normalized MFE and record a Phase 2 counterfactual action.

        The initial stop is required to calculate one-R risk. Missing or
        invalid state is recorded as UNKNOWN and never guessed. When shadow
        mode is disabled, only a protective stop tightening is eligible.
        """
        config = self._profit_protection_cfg
        if config is None or not getattr(config, "enabled", False):
            return watch

        try:
            entry = Decimal(watch.entry_price)
            original_stop = Decimal(watch.original_stop)
            current = Decimal(str(position.price_current))
            initial_risk = abs(entry - original_stop)
            previous_mfe = Decimal(watch.mfe_r)
            if not all(
                value.is_finite()
                for value in (entry, original_stop, current, initial_risk, previous_mfe)
            ):
                raise ArithmeticError("non-finite profit-protection value")
        except (AttributeError, TypeError, ValueError, ArithmeticError):
            logger.info(
                '{"event":"profit_lock_unknown","ticket":%d,'
                '"symbol":"%s","reason":"invalid_entry_or_original_stop"}',
                watch.ticket,
                watch.symbol,
            )
            return watch

        if initial_risk <= 0:
            logger.info(
                '{"event":"profit_lock_unknown","ticket":%d,'
                '"symbol":"%s","reason":"non_positive_initial_risk"}',
                watch.ticket,
                watch.symbol,
            )
            return watch

        if watch.direction == "BUY":
            favorable_distance = current - entry
        elif watch.direction == "SELL":
            favorable_distance = entry - current
        else:
            logger.info(
                '{"event":"profit_lock_unknown","ticket":%d,'
                '"symbol":"%s","reason":"unknown_direction"}',
                watch.ticket,
                watch.symbol,
            )
            return watch

        current_r = max(Decimal("0"), favorable_distance / initial_risk)
        mfe_r = max(previous_mfe, current_r)
        if mfe_r != previous_mfe:
            watch = PartialProfitWatch(
                **{**watch.to_dict(), "mfe_r": str(mfe_r)}
            )
            self._watches[watch.ticket] = watch
            self._save_state()

        activation_r = Decimal(str(config.activation_r))
        minimum_lock_r = Decimal(str(config.minimum_lock_r))
        maximum_giveback_r = Decimal(str(config.maximum_giveback_r))

        # The live trailing stop ratchets behind the best favorable excursion;
        # it does not wait for a giveback before protecting a profitable move.
        if getattr(config, "trailing_stop_enabled", False):
            trailing_activation_r = Decimal(
                str(getattr(config, "trailing_activation_r", activation_r))
            )
            trailing_distance_r = Decimal(
                str(getattr(config, "trailing_distance_r", maximum_giveback_r))
            )
            trailing_step_r = Decimal(
                str(getattr(config, "trailing_step_r", minimum_lock_r))
            )
            last_lock_r = Decimal(watch.last_lock_r)
            trailing_lock_r = max(minimum_lock_r, mfe_r - trailing_distance_r)
            if (
                mfe_r >= trailing_activation_r
                and self._target_proximity_reached(position, watch)
                and trailing_lock_r <= current_r
                and trailing_lock_r > last_lock_r
                and (
                    last_lock_r == 0
                    or trailing_lock_r - last_lock_r >= trailing_step_r
                )
            ):
                trailing_lock_price = (
                    entry + (initial_risk * trailing_lock_r)
                    if watch.direction == "BUY"
                    else entry - (initial_risk * trailing_lock_r)
                )
                if getattr(config, "shadow_mode", False):
                    logger.info(
                        '{"event":"trailing_stop_would_apply","ticket":%d,'
                        '"symbol":"%s","direction":"%s","mfe_r":"%s",'
                        '"current_r":"%s","trail_distance_r":"%s",'
                        '"lock_r":"%s","lock_price":"%s","shadow_mode":true}',
                        watch.ticket,
                        watch.symbol,
                        watch.direction,
                        mfe_r,
                        current_r,
                        trailing_distance_r,
                        trailing_lock_r,
                        trailing_lock_price,
                    )
                else:
                    applied = self._move_sl_to_profit_lock(
                        mt5,
                        position,
                        watch.direction,
                        trailing_lock_price,
                        Decimal(watch.extreme_target),
                        watch.magic_number,
                        watch=watch,
                        action=ProtectionAction.PROFIT_LOCK,
                    )
                    if applied:
                        logger.info(
                            '{"event":"trailing_stop_applied","ticket":%d,'
                            '"symbol":"%s","direction":"%s",'
                            '"mfe_r":"%s","current_r":"%s",'
                            '"lock_r":"%s","lock_price":"%s"}',
                            watch.ticket,
                            watch.symbol,
                            watch.direction,
                            mfe_r,
                            current_r,
                            trailing_lock_r,
                            trailing_lock_price,
                        )
                        watch = PartialProfitWatch(
                            **{
                                **watch.to_dict(),
                                "last_lock_r": str(trailing_lock_r),
                                "last_lock_price": str(trailing_lock_price),
                            }
                        )
                        self._watches[watch.ticket] = watch
                        self._save_state()

        giveback_r = mfe_r - current_r
        if mfe_r < activation_r or giveback_r < maximum_giveback_r:
            return watch
        if not self._target_proximity_reached(position, watch):
            return watch

        lock_r = max(minimum_lock_r, mfe_r - maximum_giveback_r)
        # A stop above the current market would not be a valid protective
        # request, so wait until the current price can support the lock.
        if lock_r > current_r:
            return watch

        lock_price = (
            entry + (initial_risk * lock_r)
            if watch.direction == "BUY"
            else entry - (initial_risk * lock_r)
        )
        broker_profit = getattr(position, "profit", "UNKNOWN")
        if getattr(config, "shadow_mode", False):
            if watch.shadow_lock_logged:
                return watch
            logger.info(
                '{"event":"profit_lock_would_apply","ticket":%d,'
                '"symbol":"%s","direction":"%s","entry":"%s",'
                '"current_price":"%s","current_profit":"%s",'
                '"mfe_r":"%s","current_r":"%s","giveback_r":"%s",'
                '"lock_r":"%s","lock_price":"%s","shadow_mode":true}',
                watch.ticket,
                watch.symbol,
                watch.direction,
                entry,
                current,
                broker_profit,
                mfe_r,
                current_r,
                giveback_r,
                lock_r,
                lock_price,
            )
            watch = PartialProfitWatch(
                **{**watch.to_dict(), "shadow_lock_logged": True}
            )
            self._watches[watch.ticket] = watch
            self._save_state()
            return watch

        if lock_r <= Decimal(watch.last_lock_r):
            return watch
        applied = self._move_sl_to_profit_lock(
            mt5,
            position,
            watch.direction,
            lock_price,
            Decimal(watch.extreme_target),
            watch.magic_number,
            watch=watch,
            action=ProtectionAction.MOMENTUM_STOP,
        )
        if applied:
            watch = PartialProfitWatch(
                **{
                    **watch.to_dict(),
                    "last_lock_r": str(lock_r),
                    "last_lock_price": str(lock_price),
                }
            )
            self._watches[watch.ticket] = watch
            self._save_state()
        return watch

    def _observe_momentum_loss(
        self,
        mt5: Any,
        position: Any,
        watch: PartialProfitWatch,
    ) -> PartialProfitWatch:
        """Handle confirmed closed-candle momentum loss.

        A setup requires the configured number of consecutive closed candles
        against the position, a positive current-R floor, and the configured
        MFE giveback. Shadow mode records the action; live mode currently
        permits only the approved TIGHTEN_STOP path.
        """
        config = self._profit_protection_cfg
        if (
            config is None
            or not getattr(config, "enabled", False)
            or not getattr(config, "momentum_loss_enabled", False)
        ):
            return watch

        key = (
            str(watch.symbol).upper(),
            str(getattr(config, "momentum_source_timeframe", "")).upper(),
        )
        candles = self._momentum_candles.get(key, ())
        confirmation_bars = int(getattr(config, "confirmation_bars", 0))
        if len(candles) < confirmation_bars or confirmation_bars < 1:
            return watch

        confirmation = candles[-confirmation_bars:]
        latest = confirmation[-1]
        raw_close_time = getattr(latest, "close_time", None)
        if raw_close_time is None:
            return watch
        close_time = (
            raw_close_time.isoformat()
            if hasattr(raw_close_time, "isoformat")
            else str(raw_close_time)
        )
        if close_time == watch.last_momentum_close_time:
            return watch

        watch = PartialProfitWatch(
            **{**watch.to_dict(), "last_momentum_close_time": close_time}
        )
        self._watches[watch.ticket] = watch
        self._save_state()

        if watch.direction == "BUY":
            opposing = all(
                getattr(candle, "close", None) < getattr(candle, "open", None)
                for candle in confirmation
            )
        elif watch.direction == "SELL":
            opposing = all(
                getattr(candle, "close", None) > getattr(candle, "open", None)
                for candle in confirmation
            )
        else:
            return watch
        if not opposing:
            return watch

        try:
            entry = Decimal(watch.entry_price)
            original_stop = Decimal(watch.original_stop)
            current = Decimal(str(position.price_current))
            mfe_r = Decimal(watch.mfe_r)
            initial_risk = abs(entry - original_stop)
            if not all(
                value.is_finite()
                for value in (entry, original_stop, current, mfe_r, initial_risk)
            ) or initial_risk <= 0:
                return watch
            favorable_distance = (
                current - entry
                if watch.direction == "BUY"
                else entry - current
            )
            current_r = max(Decimal("0"), favorable_distance / initial_risk)
        except (AttributeError, TypeError, ValueError, ArithmeticError):
            return watch

        giveback_r = mfe_r - current_r
        activation_r = Decimal(str(config.activation_r))
        minimum_profit_r = Decimal(str(config.minimum_current_profit_r))
        maximum_giveback_r = Decimal(str(config.maximum_giveback_r))
        if (
            mfe_r < activation_r
            or current_r < minimum_profit_r
            or giveback_r < maximum_giveback_r
            or not self._target_proximity_reached(position, watch)
        ):
            return watch

        lock_r = max(Decimal(str(config.minimum_lock_r)), mfe_r - maximum_giveback_r)
        if lock_r > current_r:
            logger.info(
                '{"event":"momentum_exit_deferred", "ticket":%d,'
                '"symbol":"%s","reason":"lock_above_current_market",'
                '"current_r":"%s","lock_r":"%s",'
                '"source_close_time":"%s"}',
                watch.ticket,
                watch.symbol,
                current_r,
                lock_r,
                close_time,
            )
            return watch

        lock_price = (
            entry + (initial_risk * lock_r)
            if watch.direction == "BUY"
            else entry - (initial_risk * lock_r)
        )
        if getattr(config, "momentum_shadow_mode", False):
            logger.info(
                '{"event":"momentum_exit_would_apply","ticket":%d,'
                '"symbol":"%s","direction":"%s","action":"%s",'
                '"source_timeframe":"%s","source_close_time":"%s",'
                '"confirmation_bars":%d,"entry":"%s",'
                '"current_price":"%s","current_profit":"%s",'
                '"mfe_r":"%s","current_r":"%s","giveback_r":"%s",'
                '"lock_r":"%s","lock_price":"%s","shadow_mode":true}',
                watch.ticket,
                watch.symbol,
                watch.direction,
                config.momentum_action,
                config.momentum_source_timeframe,
                close_time,
                confirmation_bars,
                entry,
                current,
                getattr(position, "profit", "UNKNOWN"),
                mfe_r,
                current_r,
                giveback_r,
                lock_r,
                lock_price,
            )
            return watch

        if config.momentum_action != "TIGHTEN_STOP":
            logger.warning(
                '{"event":"momentum_exit_live_action_unsupported",'
                '"ticket":%d,"symbol":"%s","action":"%s"}',
                watch.ticket,
                watch.symbol,
                config.momentum_action,
            )
            return watch
        if lock_r <= Decimal(watch.last_lock_r):
            return watch
        applied = self._move_sl_to_profit_lock(
            mt5,
            position,
            watch.direction,
            lock_price,
            Decimal(watch.extreme_target),
            watch.magic_number,
            watch=watch,
        )
        if applied:
            watch = PartialProfitWatch(
                **{
                    **watch.to_dict(),
                    "last_lock_r": str(lock_r),
                    "last_lock_price": str(lock_price),
                }
            )
            self._watches[watch.ticket] = watch
            self._save_state()
        return watch

    def _execute_partial(
        self,
        mt5: Any,
        position: Any,
        watch: PartialProfitWatch,
        magic_number: int,
    ) -> None:
        """
        Close partial (or full) lot at midpoint and move SL to breakeven.
        """
        original_lot = Decimal(watch.original_lot)
        entry_price  = Decimal(watch.entry_price)
        symbol       = watch.symbol
        direction    = watch.direction

        # Get broker minimum lot for this symbol
        broker_symbol = resolve_mt5_symbol(mt5, symbol)
        if broker_symbol is None:
            logger.warning(
                '{"event":"partial_profit_symbol_unavailable","ticket":%d,'
                '"symbol":"%s"}',
                position.ticket,
                symbol,
            )
            return
        symbol_info = mt5.symbol_info(broker_symbol)
        min_lot = Decimal("0.01")
        lot_step = Decimal("0.01")
        if symbol_info:
            min_lot  = Decimal(str(symbol_info.volume_min))
            lot_step = Decimal(str(symbol_info.volume_step))

        # Determine close quantity
        partial_lot = (original_lot * self._partial_ratio).quantize(lot_step, rounding=ROUND_DOWN)
        can_split   = (original_lot - partial_lot) >= min_lot and partial_lot >= min_lot

        if not can_split:
            # Cannot split — close the full position at midpoint
            close_lot = original_lot
            is_full_close = True
            logger.info(
                '{"event":"partial_profit_full_close","ticket":%d,"reason":"lot_too_small",'
                '"lot":"%s"}',
                position.ticket, close_lot,
            )
        else:
            close_lot = partial_lot
            is_full_close = False

        # Build counter-direction close request
        close_type  = _ORDER_TYPE_SELL if direction == "BUY" else _ORDER_TYPE_BUY
        filling_int = 2 if self._filling_mode == "IOC" else 1

        # Get current ask/bid for the close price
        tick = mt5.symbol_info_tick(broker_symbol)
        if tick is None:
            logger.warning(
                '{"event":"partial_profit_tick_failed","ticket":%d,"symbol":"%s"}',
                position.ticket, symbol,
            )
            return

        close_price = tick.bid if direction == "BUY" else tick.ask

        if not self._authorize_protection(
            action=ProtectionAction.PARTIAL_CLOSE,
            position=position,
            direction=direction,
            current_sl=Decimal(str(getattr(position, "sl", 0) or 0)),
            requested_sl=None,
        ):
            self._emit_quality_event(
                watch,
                position,
                action=ProtectionAction.PARTIAL_CLOSE.value,
                status="BLOCKED",
                requested_price=Decimal(str(close_price)),
                requested_volume=close_lot,
                actual_price=Decimal(str(getattr(position, "price_current", close_price))),
                reason="protection_authorization_blocked",
            )
            return

        close_request = {
            "action":        _TRADE_ACTION_DEAL,
            "symbol":        symbol,
            "volume":        float(close_lot),
            "type":          close_type,
            "position":      position.ticket,
            "price":         close_price,
            "deviation":     self._deviation,
            "magic":         magic_number,
            "comment":       f"ATLAX_PP_{position.ticket}",
            "type_filling":  filling_int,
        }

        result = mt5.order_send(close_request)
        if result is None or result.retcode not in (10009, 10008, 10010):
            err = mt5.last_error() if result is None else result.retcode
            logger.error(
                '{"event":"partial_profit_close_failed","ticket":%d,"retcode":"%s"}',
                position.ticket, err,
            )
            self._emit_quality_event(
                watch,
                position,
                action=ProtectionAction.PARTIAL_CLOSE.value,
                status="FAILED",
                broker_retcode=(int(result.retcode) if result is not None else None),
                requested_price=Decimal(str(close_price)),
                requested_volume=close_lot,
                actual_price=Decimal(str(getattr(position, "price_current", close_price))),
                reason=f"broker_partial_close_failed:{err}",
            )
            return

        logger.info(
            '{"event":"partial_profit_taken","ticket":%d,"symbol":"%s",'
            '"closed_lot":"%s","is_full_close":%s,"midpoint":"%s"}',
            position.ticket, symbol, close_lot,
            str(is_full_close).lower(), watch.midpoint,
        )
        self._observe_protective_action("PARTIAL_CLOSE")
        self._emit_quality_event(
            watch,
            position,
            action=ProtectionAction.PARTIAL_CLOSE.value,
            status="APPLIED",
            broker_retcode=int(result.retcode),
            requested_price=Decimal(str(close_price)),
            requested_volume=close_lot,
            actual_price=Decimal(str(getattr(position, "price_current", close_price))),
            reason="partial_profit_target_reached",
        )

        updated_watch = {**watch.to_dict(), "partial_taken": True}

        # Positive-profit monitoring normally moves the stop before TP1. Keep
        # this fallback for a position that reaches TP1 between scan cycles.
        if (
            not is_full_close
            and self._move_sl_to_be
            and not watch.breakeven_moved
            and self._move_sl_to_breakeven(
                mt5,
                position,
                entry_price,
                Decimal(watch.extreme_target),
                magic_number,
                direction=direction,
                watch=watch,
            )
        ):
            updated_watch["breakeven_moved"] = True

        self._watches[position.ticket] = PartialProfitWatch(**updated_watch)
        self._save_state()

    def _move_sl_to_breakeven(
        self,
        mt5: Any,
        position: Any,
        entry_price: Decimal,
        extreme_target: Decimal,
        magic_number: int,
        direction: Optional[str] = None,
        watch: Optional[PartialProfitWatch] = None,
    ) -> bool:
        """
        Modify the remaining position's SL to the entry price (breakeven).
        """
        resolved_direction = direction or self._resolve_position_direction(position)
        stop_price = self._breakeven_price_with_costs(
            mt5,
            position,
            resolved_direction,
            entry_price,
        )
        if stop_price is None:
            return False

        try:
            current_sl = Decimal(str(getattr(position, "sl", 0) or 0))
        except (TypeError, ValueError, ArithmeticError):
            current_sl = Decimal("0")
        if (
            (resolved_direction == "BUY" and current_sl > 0 and stop_price <= current_sl)
            or (resolved_direction == "SELL" and current_sl > 0 and stop_price >= current_sl)
        ):
            logger.info(
                '{"event":"breakeven_already_protected","ticket":%d,'
                '"stop_price":"%s","current_sl":"%s"}',
                position.ticket,
                stop_price,
                current_sl,
            )
            return True

        if not self._authorize_protection(
            action=ProtectionAction.BREAKEVEN,
            position=position,
            direction=resolved_direction,
            current_sl=current_sl,
            requested_sl=stop_price,
        ):
            if watch is not None:
                self._emit_quality_event(
                    watch,
                    position,
                    action=ProtectionAction.BREAKEVEN.value,
                    status="BLOCKED",
                    requested_price=stop_price,
                    actual_price=Decimal(str(getattr(position, "price_current"))),
                    stop_loss=stop_price,
                    take_profit=extreme_target,
                    reason="protection_authorization_blocked",
                )
            return False

        sltp_request = {
            "action":   _TRADE_ACTION_SLTP,
            "symbol":   position.symbol,
            "sl":       float(stop_price),
            "tp":       float(extreme_target),   # preserve approved TP2
            "position": position.ticket,
            "magic":    magic_number,
        }

        result = mt5.order_send(sltp_request)
        if result is None or result.retcode not in (10009, 10008):
            err = mt5.last_error() if result is None else result.retcode
            logger.warning(
                '{"event":"breakeven_move_failed","ticket":%d,"retcode":"%s"}',
                position.ticket, err,
            )
            if watch is not None:
                self._emit_quality_event(
                    watch,
                    position,
                    action=ProtectionAction.BREAKEVEN.value,
                    status="FAILED",
                    broker_retcode=(int(result.retcode) if result is not None else None),
                    requested_price=stop_price,
                    actual_price=Decimal(str(getattr(position, "price_current"))),
                    stop_loss=stop_price,
                    take_profit=extreme_target,
                    reason=f"broker_breakeven_update_failed:{err}",
                )
            return False
        else:
            logger.info(
                '{"event":"breakeven_move_succeeded",'
                '"ticket":%d,"entry_price":"%s",'
                '"stop_price":"%s"}',
                position.ticket, entry_price, stop_price,
            )
            self._observe_protective_action(ProtectionAction.BREAKEVEN.value)
            if watch is not None:
                self._emit_quality_event(
                    watch,
                    position,
                    action=ProtectionAction.BREAKEVEN.value,
                    status="APPLIED",
                    broker_retcode=int(result.retcode),
                    requested_price=stop_price,
                    actual_price=Decimal(str(getattr(position, "price_current"))),
                    stop_loss=stop_price,
                    take_profit=extreme_target,
                )
            return True

    def _authorize_protection(
        self,
        *,
        action: ProtectionAction,
        position: Any,
        direction: str,
        current_sl: Decimal,
        requested_sl: Optional[Decimal],
    ) -> bool:
        """Apply the optional prop-firm authorization boundary."""
        guard = self._prop_firm_protection_guard
        if guard is None:
            return True
        try:
            raw_profit = getattr(position, "profit", None)
            current_profit = (
                Decimal(str(raw_profit))
                if raw_profit is not None
                else None
            )
            request = ProtectionRequest(
                action=action,
                direction=direction,
                current_sl=current_sl,
                requested_sl=requested_sl,
                current_price=Decimal(str(position.price_current)),
                current_profit=current_profit,
                ticket=int(position.ticket),
                symbol=str(position.symbol),
            )
            authorization = guard.authorize(request)
        except (AttributeError, TypeError, ValueError, ArithmeticError) as exc:
            logger.warning(
                '{"event":"prop_firm_protection_blocked",'
                '"action":"%s","reason":"guard_error:%s"}',
                action.value,
                str(exc)[:120],
            )
            return False
        if not getattr(authorization, "authorized", False):
            logger.warning(
                '{"event":"prop_firm_protection_blocked",'
                '"ticket":%s,"symbol":"%s","action":"%s",'
                '"profile_id":"%s","profile_version":"%s",'
                '"reason":"%s"}',
                position.ticket,
                position.symbol,
                action.value,
                getattr(authorization, "profile_id", None),
                getattr(authorization, "profile_version", None),
                str(getattr(authorization, "reason", "UNKNOWN"))[:200],
            )
            return False
        return True

    @staticmethod
    def _resolve_position_direction(position: Any) -> str:
        """Resolve MT5 BUY/SELL type without guessing unknown values."""
        raw = getattr(position, "direction", None)
        if raw is None:
            raw = getattr(position, "type", None)
        if raw in (0, "0", "BUY"):
            return "BUY"
        if raw in (1, "1", "SELL"):
            return "SELL"
        return ""

    def _breakeven_price_with_costs(
        self,
        mt5: Any,
        position: Any,
        direction: str,
        entry_price: Decimal,
    ) -> Optional[Decimal]:
        """Return entry plus validated broker costs in price units."""
        config = self._profit_protection_cfg
        if config is None or not getattr(config, "include_transaction_cost_buffer", False):
            return entry_price

        costs = self._position_cost_amount(mt5, position)
        if costs is None or costs <= 0:
            if costs is None:
                logger.warning(
                    '{"event":"breakeven_cost_buffer_unknown",'
                    '"ticket":%d,"symbol":"%s","fallback":"entry"}',
                    position.ticket,
                    position.symbol,
                )
            return entry_price

        if direction not in ("BUY", "SELL"):
            logger.warning(
                '{"event":"breakeven_cost_buffer_unknown",'
                '"ticket":%d,"symbol":"%s","reason":"unknown_direction",'
                '"fallback":"entry"}',
                position.ticket,
                position.symbol,
            )
            return entry_price

        try:
            symbol_info = mt5.symbol_info(position.symbol)
            tick_value = Decimal(str(getattr(symbol_info, "trade_tick_value")))
            tick_size = Decimal(str(getattr(symbol_info, "trade_tick_size")))
            volume = Decimal(str(getattr(position, "volume")))
            if (
                not tick_value.is_finite()
                or not tick_size.is_finite()
                or not volume.is_finite()
                or tick_value <= 0
                or tick_size <= 0
                or volume <= 0
            ):
                raise ArithmeticError("invalid broker tick economics")
            price_buffer = costs * tick_size / (tick_value * volume)
            if not price_buffer.is_finite() or price_buffer < 0:
                raise ArithmeticError("invalid cost price buffer")
        except (AttributeError, TypeError, ValueError, ArithmeticError):
            logger.warning(
                '{"event":"breakeven_cost_buffer_unknown",'
                '"ticket":%d,"symbol":"%s","reason":"tick_economics_unavailable",'
                '"fallback":"entry"}',
                position.ticket,
                position.symbol,
            )
            return entry_price

        stop_price = (
            entry_price + price_buffer
            if direction == "BUY"
            else entry_price - price_buffer
        )
        logger.info(
            '{"event":"breakeven_cost_buffer_calculated",'
            '"ticket":%d,"symbol":"%s","cost_amount":"%s",'
            '"price_buffer":"%s","stop_price":"%s"}',
            position.ticket,
            position.symbol,
            costs,
            price_buffer,
            stop_price,
        )
        return stop_price

    @staticmethod
    def _position_cost_amount(mt5: Any, position: Any) -> Optional[Decimal]:
        """Read broker costs without inventing a commission estimate."""
        values: list[Decimal] = []
        for field in ("commission", "swap", "fee"):
            raw = getattr(position, field, None)
            if raw is None:
                continue
            try:
                value = Decimal(str(raw))
                if value.is_finite():
                    values.append(value)
            except (TypeError, ValueError, ArithmeticError):
                continue

        identifier = getattr(position, "identifier", None)
        history_deals_get = getattr(mt5, "history_deals_get", None)
        if identifier is not None and callable(history_deals_get):
            try:
                deals = history_deals_get(position=int(identifier)) or ()
                deal_values: list[Decimal] = []
                for deal in deals:
                    for field in ("commission", "swap", "fee"):
                        raw = getattr(deal, field, None)
                        if raw is None:
                            continue
                        try:
                            value = Decimal(str(raw))
                            if value.is_finite():
                                deal_values.append(value)
                        except (TypeError, ValueError, ArithmeticError):
                            continue
                # Only prefer deal-level data if it is non-empty.
                # Never discard position-level costs when deal history is empty.
                if deal_values:
                    values = deal_values
            except (TypeError, ValueError, ArithmeticError, OSError):
                pass

        if not values:
            return None
        return max(Decimal("0"), -sum(values, Decimal("0")))

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _save_state(self) -> None:
        data = {str(ticket): watch.to_dict() for ticket, watch in self._watches.items()}
        if self._state_store is not None:
            self._state_store.set("execution.partial_profit_watches", data)
            return
        os.makedirs(os.path.dirname(self._state_file) or ".", exist_ok=True)
        try:
            with open(self._state_file, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except Exception as exc:
            logger.error(
                '{"event":"trade_manager_save_failed","error":"%s"}', str(exc)[:120]
            )

    def _load_state(self) -> None:
        if self._state_store is not None:
            data = self._state_store.get("execution.partial_profit_watches", {})
            if isinstance(data, dict):
                self._watches = {
                    int(ticket): PartialProfitWatch.from_dict(watch)
                    for ticket, watch in data.items()
                    if isinstance(watch, dict)
                }
            return
        if not os.path.exists(self._state_file):
            return
        try:
            with open(self._state_file, encoding="utf-8") as f:
                data: dict = json.load(f)
            self._watches = {
                int(t): PartialProfitWatch.from_dict(w)
                for t, w in data.items()
            }
            logger.info(
                '{"event":"trade_manager_state_loaded","watches":%d}',
                len(self._watches),
            )
        except Exception as exc:
            logger.warning(
                '{"event":"trade_manager_load_failed","error":"%s"}', str(exc)[:120]
            )
            self._watches = {}
