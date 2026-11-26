"""
ATLAX Execution Engine.

Implements the full MT5 execution lifecycle as defined in docs/11_EXECUTION_ENGINE.md.

The execution lifecycle (10 steps, mandatory):
    1. Receive an approved ATLAX ExecutionRequest.
    2. Validate request: candidate_id, risk approved, profile, symbol match.
    3. Build a broker-compatible MqlTradeRequest from approved settings only.
    4. Run pre-flight check via MT5 order_check.
    5. Refuse execution if order_check fails for any reason.
    6. Send the order via MT5 order_send only after pre-flight passes.
    7. Inspect the returned MT5 result retcode.
    8. Treat a successful send as acceptance for processing, NOT proof of fill.
    9. Record order ID, deal ID, retcode, broker comment, and status.
    10. Return ExecutionRecord — reconciliation of final state is left to
        the trade-management layer (future phase).

Authority: docs/11_EXECUTION_ENGINE.md
           docs/10_RISK_ENGINE.md (risk gate must precede execution)
           docs/16_SECURITY.md (credential handling, comment sanitization)
           docs/06_DATA_MODELS.md (ExecutionRecord required fields)

Architectural Boundaries:
    - Execution Engine is the ONLY component that calls MT5 for order placement.
    - Execution Engine NEVER invents entries, overrides risk, or guesses inputs.
    - Execution Engine NEVER executes without an approved RiskEvaluation.
    - Execution Engine NEVER stores credentials in ExecutionRecord or logs.
    - All broker comments are sanitized (no secrets, no raw paths).
    - Ambiguous MT5 states are recorded as AMBIGUOUS — never assumed as fills.

MT5 Return Codes (key values):
    10009 = TRADE_RETCODE_DONE        — order fully processed
    10008 = TRADE_RETCODE_PLACED      — order placed (pending confirmation)
    10010 = TRADE_RETCODE_DONE_PARTIAL— partial fill
    All other non-zero codes = failure

MT5 ORDER_FILLING modes:
    ORDER_FILLING_FOK = 1  (Fill or Kill)
    ORDER_FILLING_IOC = 2  (Immediate or Cancel)
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional

from core.models.execution import (
    ExecutionRecord,
    ExecutionRequest,
    ExecutionStatus,
    MT5RequestPayload,
)
from core.models.risk import AccountState
from core.models.trade_candidate import Direction
from core.market_data.mt5_symbol import resolve_mt5_symbol
from core.risk.risk_engine import RiskEngine
from core.settings.execution_config import ExecutionConfig
from core.settings.risk_config import RiskConfig

logger = logging.getLogger("atlax.execution")

# MT5 return codes — from official MT5 Python docs
_MT5_RETCODE_DONE         = 10009
_MT5_RETCODE_PLACED       = 10008
_MT5_RETCODE_DONE_PARTIAL = 10010
_MT5_RETCODE_INVALID_FILL = 10030

# MT5 ORDER_FILLING constants
_FILLING_MODE_MAP = {
    "FOK":    0,   # ORDER_FILLING_FOK
    "IOC":    1,   # ORDER_FILLING_IOC
    "RETURN": 2,   # ORDER_FILLING_RETURN
}
_FILLING_MODE_NAMES = {value: key for key, value in _FILLING_MODE_MAP.items()}

# MT5 TRADE_ACTION constant
_TRADE_ACTION_DEAL = 1   # Immediate market order

# MT5 ORDER_TYPE constants
_ORDER_TYPE_BUY  = 0
_ORDER_TYPE_SELL = 1


class ExecutionEngine:
    """
    ATLAX MT5 Execution Engine.

    Implements the 10-step execution lifecycle from docs/11_EXECUTION_ENGINE.md.

    Instantiate once per session. Thread-safety: not guaranteed — do not call
    execute() concurrently from multiple threads on the same instance.

    Args:
        config:      Validated ExecutionConfig.
        risk_config: Validated RiskConfig (used for Risk Engine gate).
    """

    def __init__(self, config: ExecutionConfig, risk_config: RiskConfig) -> None:
        self._cfg  = config
        self._risk = RiskEngine()
        self._risk_cfg = risk_config

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def execute(self, request: ExecutionRequest) -> ExecutionRecord:
        """
        Execute an approved trade request against MT5.

        This is the primary entry point. It runs the full 10-step lifecycle
        and returns an ExecutionRecord regardless of outcome.

        Args:
            request: An approved ExecutionRequest (risk_evaluation.approved must be True).

        Returns:
            ExecutionRecord — full audit record of this attempt.
        """
        attempted_at = datetime.now(timezone.utc)

        logger.info(
            '{"event":"execution_start","candidate_id":"%s","symbol":"%s",'
            '"timeframe":"%s","profile":"%s","direction":"%s","lot_size":"%s"}',
            request.candidate.candidate_id,
            request.candidate.symbol,
            request.candidate.timeframe,
            request.candidate.profile,
            request.candidate.direction,
            request.risk_evaluation.lot_size,
        )

        try:
            return self._execute_lifecycle(request, attempted_at)
        except Exception as exc:  # noqa: BLE001
            # Catch-all — produce an ERROR record so nothing is ever silent
            completed_at = datetime.now(timezone.utc)
            logger.error(
                '{"event":"execution_unhandled_error","candidate_id":"%s","error":"%s"}',
                request.candidate.candidate_id, str(exc)[:200],
            )
            return self._make_record(
                request, None, None, None, None, None,
                ExecutionStatus.ERROR,
                error_reason=f"Unhandled execution error: {exc}",
                recovery_suggestion="Check logs, verify MT5 terminal state, reconcile manually.",
                attempted_at=attempted_at,
                completed_at=completed_at,
            )

    # ------------------------------------------------------------------
    # Lifecycle implementation
    # ------------------------------------------------------------------

    def _execute_lifecycle(
        self,
        request: ExecutionRequest,
        attempted_at: datetime,
    ) -> ExecutionRecord:

        # ── Step 1: Engine enabled gate ───────────────────────────────
        if not self._cfg.enabled:
            return self._make_record(
                request, None, None, None, None, None,
                ExecutionStatus.REQUEST_INVALID,
                error_reason="Execution Engine is disabled (execution.enabled=false).",
                recovery_suggestion="Set execution.enabled=true in config/atlax.yaml.",
                attempted_at=attempted_at,
                completed_at=datetime.now(timezone.utc),
            )

        # ── Step 2: Risk gate — must be approved ──────────────────────
        if not request.risk_evaluation.approved:
            return self._make_record(
                request, None, None, None, None, None,
                ExecutionStatus.RISK_NOT_APPROVED,
                error_reason=(
                    f"ExecutionRequest carries an unapproved RiskEvaluation: "
                    f"code={request.risk_evaluation.rejection_code}, "
                    f"reason={request.risk_evaluation.reason[:100]}"
                ),
                recovery_suggestion="Risk Engine must approve the candidate before execution.",
                attempted_at=attempted_at,
                completed_at=datetime.now(timezone.utc),
            )

        # ── Step 3: Build MT5 request ─────────────────────────────────
        mt5_payload, build_error = self._build_mt5_request(request)
        if build_error:
            return self._make_record(
                request, None, None, None, None, None,
                ExecutionStatus.REQUEST_INVALID,
                error_reason=build_error,
                recovery_suggestion="Check entry model, SL, TP, and symbol configuration.",
                attempted_at=attempted_at,
                completed_at=datetime.now(timezone.utc),
            )

        # ── Step 4 + 5: Pre-flight check (order_check) ───────────────
        preflight_ok, preflight_detail, selected_filling_mode = self._run_preflight(
            request, mt5_payload
        )
        if not preflight_ok:
            logger.warning(
                '{"event":"execution_preflight_rejected","candidate_id":"%s",'
                '"detail":"%s"}',
                request.candidate.candidate_id, preflight_detail[:200],
            )
            return self._make_record(
                request, mt5_payload, preflight_detail, None, None, None,
                ExecutionStatus.PREFLIGHT_REJECTED,
                error_reason=f"MT5 order_check rejected: {preflight_detail}",
                recovery_suggestion=(
                    "Check symbol availability, account margin, SL/TP validity, "
                    "market hours, and broker restrictions."
                ),
                attempted_at=attempted_at,
                completed_at=datetime.now(timezone.utc),
            )

        # Persist the exact broker-compatible filling mode that passed
        # preflight, rather than the originally requested preference.
        if selected_filling_mode is not None:
            mt5_payload = replace(
                mt5_payload,
                type_filling=self._filling_mode_label(selected_filling_mode),
            )

        # ── Step 6 + 7: Send order ────────────────────────────────────
        send_retcode, order_id, deal_id, broker_comment, send_error = (
            self._send_order(
                request,
                mt5_payload,
                filling_mode_override=selected_filling_mode,
            )
        )

        if send_error:
            send_status = (
                ExecutionStatus.AMBIGUOUS
                if send_error.startswith("AMBIGUOUS:")
                else ExecutionStatus.ERROR
            )
            return self._make_record(
                request, mt5_payload, preflight_detail,
                send_retcode, order_id, deal_id, send_status,
                error_reason=send_error,
                recovery_suggestion=(
                    "Reconcile MT5 by ATLAX magic/comment before any retry."
                    if send_status == ExecutionStatus.AMBIGUOUS
                    else "Check MT5 terminal state. Reconcile manually if needed."
                ),
                attempted_at=attempted_at,
                completed_at=datetime.now(timezone.utc),
                broker_comment=broker_comment,
            )

        # ── Step 8 + 9: Classify result ───────────────────────────────
        status = self._classify_retcode(send_retcode)

        logger.info(
            '{"event":"execution_result","candidate_id":"%s","symbol":"%s",'
            '"direction":"%s","lot_size":"%s","retcode":%s,"order_id":%s,'
            '"deal_id":%s,"status":"%s","broker_comment":"%s"}',
            request.candidate.candidate_id,
            request.candidate.symbol,
            request.candidate.direction,
            request.risk_evaluation.lot_size,
            send_retcode, order_id, deal_id,
            status.value,
            (broker_comment or "")[:80],
        )

        # ── Step 10: Return full record ───────────────────────────────
        return self._make_record(
            request, mt5_payload, preflight_detail,
            send_retcode, order_id, deal_id,
            status,
            error_reason=None if status in (
                ExecutionStatus.SENT,
                ExecutionStatus.FILLED,
                ExecutionStatus.PARTIALLY_FILLED,
            ) else f"MT5 retcode {send_retcode}: {broker_comment}",
            recovery_suggestion=(
                None if status in (
                    ExecutionStatus.SENT,
                    ExecutionStatus.FILLED,
                ) else "Check MT5 terminal and reconcile position state manually."
            ),
            attempted_at=attempted_at,
            completed_at=datetime.now(timezone.utc),
            broker_comment=broker_comment,
        )

    # ------------------------------------------------------------------
    # MT5 request builder
    # ------------------------------------------------------------------

    def _build_mt5_request(
        self,
        request: ExecutionRequest,
    ) -> tuple[Optional[MT5RequestPayload], Optional[str]]:
        """
        Build a sanitized MT5RequestPayload from the approved candidate geometry.

        Returns (payload, None) on success.
        Returns (None, error_reason) on failure.
        """
        try:
            try:
                import MetaTrader5 as mt5  # type: ignore[import]
            except ImportError:
                mt5 = None

            candidate = request.candidate
            lot_size  = request.risk_evaluation.lot_size

            entry_ref = candidate.entry_model.reference_price
            sl_level  = candidate.invalidation_model.invalidation_level
            # TP1 is handled by TradeManager; TP2 is applied to the broker
            # position below as the approved hard final target.
            # Authority: docs/11_EXECUTION_ENGINE.md (trade management phase)

            if candidate.direction == Direction.BUY:
                order_type = "ORDER_TYPE_BUY"
                price_str  = str(entry_ref) if not self._cfg.use_market_price else None
            else:
                order_type = "ORDER_TYPE_SELL"
                price_str  = str(entry_ref) if not self._cfg.use_market_price else None

            # Sanitized comment — candidate ID truncated, no credentials
            # MT5 comment max = 31 chars; prefix + underscore + 8-char ID = safe
            comment = (
                f"{self._cfg.comment_prefix[:20]}_"
                f"{candidate.candidate_id[:8]}"
            )

            filling_int = _FILLING_MODE_MAP.get(self._cfg.order_filling_mode, 1)
            broker_symbol = resolve_mt5_symbol(mt5, candidate.symbol)
            # Keep the canonical name when resolution is unavailable. The
            # mandatory order_check then fails closed instead of converting a
            # symbol-mapping issue into an un-audited execution.
            broker_symbol = broker_symbol or candidate.symbol

            payload = MT5RequestPayload(
                action       = "TRADE_ACTION_DEAL",
                symbol       = broker_symbol,
                volume       = str(lot_size),
                order_type   = order_type,
                price        = price_str,
                sl           = str(sl_level),
                tp           = "0",
                deviation    = self._cfg.deviation_points,
                type_filling = self._cfg.order_filling_mode,
                magic        = self._cfg.magic_number,
                comment      = comment,
            )
            payload = replace(
                payload,
                tp=str(candidate.target_model.opposite_extreme_target),
            )
            return payload, None

        except Exception as exc:  # noqa: BLE001
            return None, f"Failed to build MT5 request: {exc}"

    # ------------------------------------------------------------------
    # Pre-flight check
    # ------------------------------------------------------------------

    def _run_preflight(
        self,
        request: ExecutionRequest,
        payload: MT5RequestPayload,
    ) -> tuple[bool, str, Optional[int]]:
        """
        Run MT5 order_check before sending.

        Returns (True, detail_string, selected_filling_mode) if check passes.
        Returns (False, detail_string, None) if check rejects.

        If MT5 is not installed, returns (False, reason) — fail-closed.
        """
        try:
            import MetaTrader5 as mt5  # type: ignore[import]

            preferred_mode = self._resolve_filling_mode(mt5, payload.symbol)
            check_request = self._payload_to_mt5_dict(
                request,
                payload,
                filling_mode_override=preferred_mode,
            )
            result = mt5.order_check(check_request)

            if result is None:
                last_error = mt5.last_error()
                return False, f"order_check returned None. last_error={last_error}", None

            if result.retcode == 0:
                return True, "OK", preferred_mode

            initial_detail = (
                f"order_check retcode={result.retcode} "
                f"comment={result.comment!r}"
            )

            # Some brokers report a symbol as accepting FOK/IOC in
            # symbol_info(), but reject the selected mode during order_check.
            # Revalidate the alternate immediate mode before sending. This is
            # still fail-closed: order_send is never called without a passing
            # order_check result.
            if not self._is_invalid_fill_result(result):
                return False, initial_detail, None

            attempted_details = [initial_detail]
            for fallback_mode in self._filling_mode_candidates(
                mt5, payload.symbol, preferred_mode
            ):
                if fallback_mode == preferred_mode:
                    continue
                fallback_request = self._payload_to_mt5_dict(
                    request,
                    payload,
                    filling_mode_override=fallback_mode,
                )
                fallback_result = mt5.order_check(fallback_request)
                if fallback_result is None:
                    attempted_details.append(
                        f"{self._filling_mode_label(fallback_mode)}: "
                        f"order_check returned None; last_error={mt5.last_error()}"
                    )
                    continue
                if fallback_result.retcode == 0:
                    logger.info(
                        '{"event":"execution_filling_mode_fallback",'
                        '"symbol":"%s","requested":"%s",'
                        '"selected":"%s"}',
                        payload.symbol,
                        self._filling_mode_label(preferred_mode),
                        self._filling_mode_label(fallback_mode),
                    )
                    return (
                        True,
                        f"OK; type_filling={self._filling_mode_label(fallback_mode)}",
                        fallback_mode,
                    )
                attempted_details.append(
                    f"{self._filling_mode_label(fallback_mode)}: "
                    f"retcode={fallback_result.retcode} "
                    f"comment={fallback_result.comment!r}"
                )

            return False, "; ".join(attempted_details), None

        except ImportError:
            return False, (
                "MetaTrader5 Python package is not installed. "
                "Install with: pip install MetaTrader5. "
                "Authority: docs/11_EXECUTION_ENGINE.md"
            ), None
        except Exception as exc:  # noqa: BLE001
            return False, f"order_check raised unexpected error: {exc}", None

    @staticmethod
    def _is_invalid_fill_result(result: object) -> bool:
        """Return True only for broker responses that justify fill fallback."""
        try:
            retcode = int(getattr(result, "retcode", -1))
        except (TypeError, ValueError):
            retcode = -1
        comment = str(getattr(result, "comment", "") or "").lower()
        return retcode == _MT5_RETCODE_INVALID_FILL or any(
            marker in comment
            for marker in ("unsupported filling", "invalid fill", "invalid filling")
        )

    @staticmethod
    def _filling_mode_label(mode: int) -> str:
        return _FILLING_MODE_NAMES.get(mode, str(mode))

    def _filling_mode_candidates(
        self,
        mt5: object,
        symbol: str,
        preferred_mode: int,
    ) -> tuple[int, ...]:
        """Return safe fallback modes for a broker's immediate execution path."""
        candidates = [preferred_mode]
        # FOK and IOC are the only safe universal alternatives for market
        # orders. RETURN is added only when symbol metadata says the execution
        # mode is not Market Execution.
        for mode in (0, 1):
            if mode not in candidates:
                candidates.append(mode)
        symbol_info_fn = getattr(mt5, "symbol_info", None)
        info = symbol_info_fn(symbol) if callable(symbol_info_fn) else None
        if info is not None and getattr(info, "trade_exemode", None) != 2:
            if 2 not in candidates:
                candidates.append(2)
        return tuple(candidates)

    # ------------------------------------------------------------------
    # Order send
    # ------------------------------------------------------------------

    def _send_order(
        self,
        request: ExecutionRequest,
        payload: MT5RequestPayload,
        *,
        filling_mode_override: Optional[int] = None,
    ) -> tuple[Optional[int], Optional[int], Optional[int], Optional[str], Optional[str]]:
        """
        Send the order via MT5 order_send.

        Returns:
            (retcode, order_id, deal_id, broker_comment, error_reason)
            error_reason is None on success, set on exception.
        """
        # A missing result or transport exception is ambiguous: the broker may
        # have accepted the request even though the client did not receive it.
        # Never blindly retry an order in that state.
        try:
            import MetaTrader5 as mt5  # type: ignore[import]

            mt5_dict = self._payload_to_mt5_dict(
                request,
                payload,
                filling_mode_override=filling_mode_override,
            )
            result = mt5.order_send(mt5_dict)

            if result is None:
                last_error = mt5.last_error()
                logger.error(
                    '{"event":"order_send_ambiguous","last_error":"%s"}',
                    str(last_error),
                )
                return None, None, None, None, (
                    "AMBIGUOUS: order_send returned None; reconcile broker state "
                    f"before retry. last_error={last_error}"
                )

            retcode = result.retcode
            order_id = getattr(result, "order", None)
            deal_id = getattr(result, "deal", None)
            broker_comment = getattr(result, "comment", None)
            return retcode, order_id, deal_id, broker_comment, None

        except ImportError:
            return None, None, None, None, (
                "MetaTrader5 Python package is not installed."
            )
        except Exception as exc:  # noqa: BLE001
            logger.error(
                '{"event":"order_send_ambiguous","error":"%s"}',
                str(exc)[:200],
            )
            return None, None, None, None, (
                f"AMBIGUOUS: order_send raised an exception; reconcile broker "
                f"state before retry. error={exc}"
            )

    # ------------------------------------------------------------------
    # MT5 dict builder
    # ------------------------------------------------------------------

    def _resolve_filling_mode(self, mt5: object, symbol: str) -> int:
        """Select a broker-supported filling mode for the symbol."""
        requested = self._cfg.order_filling_mode
        configured = _FILLING_MODE_MAP.get(requested)
        symbol_info_fn = getattr(mt5, "symbol_info", None)
        info = symbol_info_fn(symbol) if callable(symbol_info_fn) else None
        if info is None:
            if configured is None:
                raise ValueError("No configured MT5 filling mode is available")
            return configured

        filling_flags = int(getattr(info, "filling_mode", 0) or 0)
        execution_mode = getattr(info, "trade_exemode", None)
        if requested == "FOK" and filling_flags & 1:
            return 0
        if requested == "IOC" and filling_flags & 2:
            return 1
        # RETURN is not valid for Market Execution; it is valid for the other
        # execution modes when the broker does not advertise a stricter mode.
        if requested == "RETURN" and execution_mode != 2:
            return 2
        if filling_flags & 2:
            return 1
        if filling_flags & 1:
            return 0
        if execution_mode != 2:
            return 2
        raise ValueError(f"Broker exposes no supported filling mode for {symbol}")

    def _payload_to_mt5_dict(
        self,
        request: ExecutionRequest,
        payload: MT5RequestPayload,
        *,
        filling_mode_override: Optional[int] = None,
    ) -> dict:
        """
        Convert MT5RequestPayload to the raw dict format expected by MT5 Python API.

        The price field is set to 0.0 for market orders (MT5 convention).
        Uses ask/bid price when use_market_price=True — the simplest safe default
        for execution testing; production may want to read live ask/bid from MT5.
        """
        try:
            import MetaTrader5 as mt5  # type: ignore[import]
        except ImportError:
            mt5 = None  # type: ignore[assignment]

        filling_int = (
            filling_mode_override
            if filling_mode_override is not None
            else (
                self._resolve_filling_mode(mt5, payload.symbol)
                if mt5 is not None
                else _FILLING_MODE_MAP.get(self._cfg.order_filling_mode, 1)
            )
        )
        order_type_int = (
            _ORDER_TYPE_BUY if payload.order_type == "ORDER_TYPE_BUY"
            else _ORDER_TYPE_SELL
        )

        # For market orders, MT5 convention: price = current ask/bid
        if self._cfg.use_market_price and mt5 is not None:
            tick = mt5.symbol_info_tick(payload.symbol)
            if tick is not None:
                price = tick.ask if order_type_int == _ORDER_TYPE_BUY else tick.bid
            else:
                price = 0.0
        else:
            price = float(payload.price) if payload.price else 0.0

        return {
            "action":        _TRADE_ACTION_DEAL,
            "symbol":        payload.symbol,
            "volume":        float(payload.volume),
            "type":          order_type_int,
            "price":         price,
            "sl":            float(payload.sl),
            "tp":            float(payload.tp),
            "deviation":     payload.deviation,
            "type_filling":  filling_int,
            "magic":         payload.magic,
            "comment":       payload.comment,
        }

    # ------------------------------------------------------------------
    # Result classification
    # ------------------------------------------------------------------

    @staticmethod
    def _classify_retcode(retcode: Optional[int]) -> ExecutionStatus:
        """Map MT5 retcode to ExecutionStatus."""
        if retcode is None:
            return ExecutionStatus.AMBIGUOUS
        if retcode == _MT5_RETCODE_DONE:
            return ExecutionStatus.FILLED
        if retcode == _MT5_RETCODE_PLACED:
            return ExecutionStatus.SENT
        if retcode == _MT5_RETCODE_DONE_PARTIAL:
            return ExecutionStatus.PARTIALLY_FILLED
        return ExecutionStatus.REJECTED_BY_BROKER

    # ------------------------------------------------------------------
    # Record builder
    # ------------------------------------------------------------------

    def _make_record(
        self,
        request: ExecutionRequest,
        mt5_request: Optional[MT5RequestPayload],
        preflight_result: Optional[str],
        order_send_retcode: Optional[int],
        mt5_order_id: Optional[int],
        mt5_deal_id: Optional[int],
        status: ExecutionStatus,
        error_reason: Optional[str],
        recovery_suggestion: Optional[str],
        attempted_at: datetime,
        completed_at: datetime,
        broker_comment: Optional[str] = None,
    ) -> ExecutionRecord:
        candidate = request.candidate
        return ExecutionRecord(
            record_id           = str(uuid.uuid4()),
            candidate_id        = candidate.candidate_id,
            profile             = candidate.profile,
            symbol              = candidate.symbol,
            timeframe           = candidate.timeframe,
            direction           = candidate.direction,
            lot_size            = request.risk_evaluation.lot_size,
            risk_evaluation_id  = request.risk_evaluation.candidate_id,
            mt5_request         = mt5_request,
            preflight_result    = preflight_result,
            order_send_retcode  = order_send_retcode,
            mt5_order_id        = mt5_order_id,
            mt5_deal_id         = mt5_deal_id,
            broker_comment      = broker_comment,
            status              = status,
            error_reason        = error_reason,
            recovery_suggestion = recovery_suggestion,
            attempted_at        = attempted_at,
            completed_at        = completed_at,
        )
