"""
ATLAX Market Scanner.

The main orchestration loop that ties the entire ATLAX pipeline together.
For each configured instrument, on every poll cycle, the scanner:
    1. Fetches live candles from MT5 (Market Data Layer)
    2. Runs the CRT Detector (Detector Layer)
    3. For each active profile, runs the Strategy Engine
    4. Runs the Confidence Engine to score the candidate
    5. Routes to the Alert Engine for delivery

This is the entry point for live operation.

Authority: docs/04_SYSTEM_DESIGN.md (system layer flow)
           docs/03_ARCHITECTURE.md (profile-aware routing)

Architectural Boundaries:
    - Scanner is the ONLY component that crosses layer boundaries.
    - It orchestrates but never owns detection, strategy, or alert logic.
    - Execution Engine (Phase 10) will be invoked AFTER human approval.
    - Scanner never executes trades.
    - Scanner never skips logging for any error or skip decision.
"""

from __future__ import annotations

import logging
import os
import signal
import time
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from html import escape
from types import SimpleNamespace
from typing import Optional

try:
    import MetaTrader5 as mt5
except ModuleNotFoundError:  # The API and menu test paths do not require MT5.
    mt5 = None

from core.alerts.alert_engine import AlertEngine
from core.confidence.atr_quality import evaluate_atr_quality
from core.confidence.confidence_engine import ConfidenceEngine
from core.detectors.crt.detector import CRTDetector
from core.detectors.fvg.detector import LTFFVGDetector
from core.detectors.fvg.watcher import FVGWatcher
from core.execution.execution_engine import ExecutionEngine
from core.execution.trade_manager import TradeManager
from core.market_data.key_level_evaluator import (
    KeyLevelEvaluator,
    simple_atr_for_parent,
)
from core.market_data.key_level_shadow import KeyLevelShadowLedger
from core.market_data.mt5_candle_feed import MT5CandleFeed
from core.market_data.mt5_connection import MT5Connection, MT5ConnectionError
from core.market_data.mt5_account_identity import read_mt5_account_identity
from core.market_data.mt5_symbol import resolve_mt5_symbol
from core.models.approval import ApprovalStatus
from core.models.account_mode import AccountModeStatus, TradingAccountMode
from core.models.execution import ExecutionRequest
from core.models.execution_quality import (
    ExecutionQualityEvent,
    ExecutionQualityEventType,
)
from core.analytics.heatmap_engine import HeatmapEngine
from core.analytics.correlation_engine import CorrelationEngine
from core.models.risk import AccountState
from core.risk.account_mode import (
    bind_current_account,
    load_account_mode_state,
    record_verification,
    select_prop_firm_profile,
    set_account_mode,
)
from core.risk.prop_firm_runtime import PropFirmRuntimeAdapter, PropFirmRuntimeResult
from core.risk.prop_firm_shadow import PropFirmShadowValidator
from core.risk.prop_firm_profile_monitor import PropFirmProfileMonitor
from core.risk.loss_budget import evaluate_loss_budget
from core.risk.circuit_breaker import (
    CircuitBreaker,
    CircuitBreakerDecision,
)
from core.risk.risk_engine import RiskEngine
from core.operations.snapshot import build_operations_snapshot
from core.operations.reconciliation import reconcile_trade_state
from core.operations.trading_account_registry import (
    add_trading_account,
    delete_trading_account,
    load_trading_account_registry,
    select_trading_account,
)
from core.operations.watchdog import ReliabilityWatchdog
from core.settings.alert_config import AlertConfig
from core.settings.approval_config import ApprovalConfig
from core.settings.confidence_config import ConfidenceConfig
from core.settings.crt_config import CRTConfig
from core.settings.execution_config import ExecutionConfig
from core.settings.market_data_config import MarketDataConfig, WatchedInstrument
from core.settings.risk_config import RiskConfig
from core.settings.strategy_config import ProfileConfig, StrategyConfig
from core.strategy.crt_strategy import CRTStrategy
from core.settings.analytics_config import AnalyticsConfig
from core.analytics.ai_advisory import AdvisoryEngine
from core.analytics.realized_pnl import reconcile_mt5_exit_deals
from core.analytics.signal_passport import (
    get_signal_passport_history,
    get_signal_passport_performance,
    render_signal_passport_history,
    render_signal_passport_weekly,
)
from core.payments.payment_service import PaymentService
from core.payments.subscription_lifecycle import SubscriptionLifecycleService
from core.settings.payment_config import PaymentConfig
from core.storage.factory import (
    get_approval_engine,
    get_audit_log,
    get_journal,
    get_runtime_state,
    get_subscriber_db,
)

logger = logging.getLogger("atlax.scanner")


class MarketScanner:
    """
    Continuous market scanning loop.

    Connects to MT5, then polls all configured instruments on the
    configured interval. Runs the full pipeline for every instrument
    and every active profile.

    Graceful shutdown: catches SIGINT and SIGTERM and exits cleanly.

    Args:
        market_cfg:  Market data configuration (instruments, poll interval).
        crt_cfg:     CRT detector configuration.
        strategy_cfg: Strategy engine configuration (profiles).
        conf_cfg:    Confidence engine configuration (weights).
        alert_cfg:   Alert engine configuration (channels, dedup).
    """

    def __init__(
        self,
        market_cfg: MarketDataConfig,
        crt_cfg: CRTConfig,
        strategy_cfg: StrategyConfig,
        conf_cfg: ConfidenceConfig,
        alert_cfg: AlertConfig = None,
        risk_cfg: RiskConfig = None,
        approval_cfg: ApprovalConfig = None,
        execution_cfg: ExecutionConfig = None,
        analytics_cfg: Optional[AnalyticsConfig] = None,
        payment_cfg: Optional[PaymentConfig] = None,
        htf_momentum_cfg=None,
        atr_quality_cfg=None,
        key_level_cfg=None,
        nested_crt_cfg=None,
        fvg_cfg=None,
        volume_profile_cfg=None,
        profit_protection_cfg=None,
        account_mode_cfg=None,
        circuit_breaker_cfg=None,
        prop_firm_profiles=None,
        watchdog_cfg=None,
    ) -> None:
        self._market_cfg   = market_cfg
        self._detector     = CRTDetector(crt_cfg)
        self._strategy     = CRTStrategy()
        self._conf_engine  = ConfidenceEngine(conf_cfg)
        self._subscriber_db = get_subscriber_db()
        self._runtime_store = get_runtime_state()
        self._watchdog = (
            ReliabilityWatchdog(self._runtime_store, watchdog_cfg)
            if watchdog_cfg is not None
            else None
        )
        self._journal = get_journal(analytics_cfg.db_path)
        self._alert_engine = AlertEngine(
            alert_cfg,
            db_path=analytics_cfg.db_path,
            journal=self._journal,
            subscriber_db=self._subscriber_db,
            runtime_store=self._runtime_store,
            atr_quality_config=atr_quality_cfg,
        )
        self._risk_engine  = RiskEngine()
        self._approval_engine = get_approval_engine(approval_cfg)
        self._execution_engine = ExecutionEngine(execution_cfg, risk_cfg)
        self._trade_manager    = TradeManager(
            state_file               = 'data/partial_profit_watches.json',
            partial_profit_enabled   = getattr(execution_cfg, 'partial_profit_enabled', True),
            partial_profit_ratio     = getattr(execution_cfg, 'partial_profit_ratio', 0.5),
            move_sl_to_breakeven     = getattr(execution_cfg, 'move_sl_to_breakeven', True),
            deviation_points         = getattr(execution_cfg, 'deviation_points', 20),
            filling_mode             = getattr(execution_cfg, 'order_filling_mode', 'IOC'),
            state_store              = self._runtime_store,
            event_sink               = self._record_execution_quality_event,
            profit_protection_config = profit_protection_cfg,
        )
        self._strategy_cfg = strategy_cfg
        self._risk_cfg     = risk_cfg
        self._execution_cfg = execution_cfg
        self._crt_cfg      = crt_cfg
        heatmap_path = getattr(analytics_cfg, "heatmap_path", "data/heatmap.json") if analytics_cfg else "data/heatmap.json"
        self._heatmap      = HeatmapEngine(output_file=heatmap_path, state_store=self._runtime_store)
        self._correlation  = CorrelationEngine(output_file='data/correlation.json', state_store=self._runtime_store)
        
        try:
            from core.analytics.simulator import CounterfactualSimulator
            self._simulator = CounterfactualSimulator()
        except ImportError:
            self._simulator = None
            
        try:
            from core.risk.challenge_navigator import ChallengeNavigator
            self._navigator = ChallengeNavigator()
        except ImportError:
            self._navigator = None
            
        self._running      = False
        self._is_paused    = False
        self._cycle_count  = 0
        self._last_cycle_at: Optional[str] = None
        self._last_cycle_completed_at: Optional[str] = None
        self._last_market_data_at: Optional[str] = None
        self._cycle_instruments_processed = 0
        self._cycle_instrument_failures = 0
        self._last_watchdog_probe_monotonic = 0.0
        stored_reconciliation = self._runtime_store.get(
            "operations.reconciliation", {}
        ) or {}
        self._reconciliation_status = str(
            stored_reconciliation.get("status", "UNKNOWN")
        ).upper()
        self._reconciliation_report = stored_reconciliation
        self._last_alerted = self._load_alerted_state()  # persisted across restarts

        # FVG detector + watcher (two-alert flow)
        self._fvg_detector = LTFFVGDetector(
            min_fvg_pips=0.5,
            pip_size=crt_cfg.pip_size,
        )
        self._fvg_watcher  = FVGWatcher(
            max_age_seconds=14400,
            state_store=self._runtime_store,
        )  # 4h expiry

        # AI Advisory Engine (end-of-day report)
        self._analytics_cfg   = analytics_cfg  # stored for menu callbacks and note lookups
        self._advisory_engine = (
            AdvisoryEngine(
                analytics_cfg,
                market_data_reader=self._read_market_closes,
                allowed_symbols={instrument.symbol for instrument in market_cfg.instruments},
                subscriber_db_provider=self._get_subscriber_db,
            )
            if analytics_cfg else None
        )
        self._last_report_date: Optional[str] = None  # tracks which day the report fired

        self._payment_cfg = payment_cfg
        self._payment_service: Optional[PaymentService] = None
        self._subscription_lifecycle: Optional[SubscriptionLifecycleService] = None
        self._admin_audit = get_audit_log()
        self._payment_verifier = None
        if payment_cfg is not None:
            try:
                from core.payments.automatic_verification import AutomaticPaymentVerifier

                self._payment_verifier = AutomaticPaymentVerifier(payment_cfg)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    '{"event":"payment_verifier_init_failed","error":"%s"}',
                    str(exc)[:160],
                )

        # Extended config: added parameters (phase 3+)
        self._htf_momentum_cfg = htf_momentum_cfg
        self._atr_quality_cfg = atr_quality_cfg
        self._key_level_cfg = key_level_cfg
        self._key_level_evaluator = (
            KeyLevelEvaluator(key_level_cfg)
            if key_level_cfg is not None and key_level_cfg.enabled
            else None
        )
        self._key_level_shadow_ledger = (
            KeyLevelShadowLedger(self._runtime_store)
            if self._key_level_evaluator is not None
            else None
        )
        # Evaluation is immutable for one closed parent candle. Keeping it in
        # memory avoids source-history requests on every five-second cycle;
        # after a restart ATLAX re-evaluates deterministically from MT5 data.
        self._key_level_evaluations: dict[str, object] = {}
        self._nested_crt_cfg = nested_crt_cfg
        self._fvg_cfg = fvg_cfg
        self._volume_profile_cfg = volume_profile_cfg
        self._profit_protection_cfg = profit_protection_cfg
        self._account_mode_cfg = account_mode_cfg
        self._circuit_breaker = (
            CircuitBreaker(self._runtime_store, circuit_breaker_cfg)
            if circuit_breaker_cfg is not None
            else None
        )
        self._operations_metrics = None
        try:
            from core.operations.monitoring import OperationalMetricsLedger

            self._operations_metrics = OperationalMetricsLedger(self._runtime_store)
        except Exception as exc:  # noqa: BLE001 - metrics must not stop scanning
            logger.warning(
                '{"event":"operations_metrics_init_failed","error":"%s"}',
                str(exc)[:160],
            )
        self._prop_firm_profiles = prop_firm_profiles if prop_firm_profiles is not None else []
        self._profile_monitor = PropFirmProfileMonitor(
            self._runtime_store,
            self._prop_firm_profiles,
            max_age_hours=float(
                getattr(self._account_mode_cfg, "max_profile_age_hours", 168.0)
            ),
        )
        self._profile_monitor_report = self._refresh_prop_firm_profile_monitor()
        self._prop_firm_runtime = None
        self._prop_firm_shadow = None
        if self._account_mode_cfg is not None and bool(
            getattr(self._account_mode_cfg, "enabled", False)
        ):
            self._prop_firm_runtime = PropFirmRuntimeAdapter(
                self._runtime_store,
                self._account_mode_cfg,
                self._prop_firm_profiles,
            )
            self._prop_firm_shadow = PropFirmShadowValidator(
                self._risk_engine,
                self._risk_cfg,
                self._runtime_store,
                context_provider=self._resolve_prop_firm_context,
            )

    def _read_market_closes(self, symbol: str, timeframe: str, periods: int) -> list[float]:
        """Read configured closed candles for advisory correlation only."""
        instrument = next(
            (
                item for item in self._market_cfg.instruments
                if item.symbol.upper() == str(symbol).upper()
                and item.timeframe.upper() == str(timeframe).upper()
            ),
            None,
        )
        if instrument is None:
            return []
        sequence = MT5CandleFeed(instrument).get_candles(
            count=min(max(int(periods), 20), 200),
            include_live=False,
        )
        return [float(candle.close) for candle in sequence.candles]

    def _get_subscriber_db(self):
        """Return the shared subscriber store used by Telegram and AI quotas."""
        return self._subscriber_db

    # ------------------------------------------------------------------
    # Prop-Firm runtime integration
    # ------------------------------------------------------------------

    @staticmethod
    def _decimal_value(value, *, allow_zero: bool = False) -> Optional[Decimal]:
        """Parse a finite broker value without inventing a fallback."""
        try:
            parsed = Decimal(str(value))
        except (ArithmeticError, TypeError, ValueError):
            return None
        if not parsed.is_finite() or (parsed < 0 if allow_zero else parsed <= 0):
            return None
        return parsed

    def _build_live_risk_account_state(
        self,
        account_info,
        positions,
        candidate_symbol: str,
    ) -> AccountState:
        """Build the live risk snapshot with broker symbol economics.

        The Risk Engine deliberately does not call MT5. A scanner-created
        snapshot is therefore always marked as requiring complete broker
        metadata so a missing ``symbol_info`` response fails closed instead
        of using offline compatibility values.
        """
        symbol_info = None
        try:
            broker_symbol = resolve_mt5_symbol(mt5, candidate_symbol)
            symbol_info = (
                mt5.symbol_info(broker_symbol)
                if mt5 is not None and broker_symbol is not None
                else None
            )
        except Exception as exc:  # noqa: BLE001 - Risk Engine will reject metadata gaps
            logger.warning(
                '{"event":"risk_symbol_metadata_unavailable","symbol":"%s",'
                '"reason":"symbol_info_failed","error":"%s"}',
                candidate_symbol,
                str(exc)[:160],
            )

        if symbol_info is None:
            logger.warning(
                '{"event":"risk_symbol_metadata_unavailable","symbol":"%s",'
                '"reason":"symbol_info_unavailable"}',
                candidate_symbol,
            )

        trades_today, daily_trade_count_available = self._get_atlax_trades_opened_today()
        daily_pnl, daily_pnl_available = self._get_atlax_daily_pnl()
        weekly_pnl, weekly_pnl_available = self._get_atlax_weekly_pnl()

        return AccountState(
            balance=Decimal(str(account_info.balance)),
            equity=Decimal(str(account_info.equity)),
            daily_pnl=daily_pnl,
            weekly_pnl=weekly_pnl,
            open_trade_count=len(positions),
            trades_today=trades_today,
            open_symbols=tuple(str(getattr(position, "symbol", "")) for position in positions),
            currency=str(account_info.currency).upper(),
            snapshot_at=datetime.now(timezone.utc),
            symbol_tick_value=self._decimal_value(
                getattr(symbol_info, "trade_tick_value", None)
                if symbol_info is not None
                else None
            ),
            symbol_tick_size=self._decimal_value(
                getattr(symbol_info, "trade_tick_size", None)
                if symbol_info is not None
                else None
            ),
            symbol_volume_min=self._decimal_value(
                getattr(symbol_info, "volume_min", None)
                if symbol_info is not None
                else None
            ),
            symbol_volume_max=self._decimal_value(
                getattr(symbol_info, "volume_max", None)
                if symbol_info is not None
                else None
            ),
            symbol_volume_step=self._decimal_value(
                getattr(symbol_info, "volume_step", None)
                if symbol_info is not None
                else None
            ),
            broker_metadata_required=True,
            daily_trade_count_available=daily_trade_count_available,
            daily_pnl_available=daily_pnl_available,
            weekly_pnl_available=weekly_pnl_available,
        )

    def _get_atlax_trades_opened_today(self) -> tuple[int, bool]:
        """Return today's distinct ATLAX opening trades from MT5 history.

        The configured magic number scopes the count to positions originated
        by ATLAX. Partial fills are deduplicated by position id; MT5 order and
        deal tickets are conservative fallbacks when a broker omits it. A
        missing or malformed ATLAX deal makes the count unavailable so the
        Risk Engine can fail closed instead of treating it as zero.
        """
        magic_number = getattr(getattr(self, "_execution_cfg", None), "magic_number", None)
        try:
            expected_magic = int(magic_number)
        except (TypeError, ValueError):
            logger.warning(
                '{"event":"daily_trade_count_unavailable","reason":"execution_magic_invalid"}'
            )
            return 0, False

        history_deals_get = getattr(mt5, "history_deals_get", None) if mt5 is not None else None
        if not callable(history_deals_get):
            logger.warning(
                '{"event":"daily_trade_count_unavailable","reason":"history_deals_unavailable"}'
            )
            return 0, False

        now = datetime.now(timezone.utc)
        day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        try:
            deals = history_deals_get(day_start, now)
        except Exception as exc:  # noqa: BLE001 - unavailable count must block execution
            logger.warning(
                '{"event":"daily_trade_count_unavailable","reason":"history_deals_failed",'
                '"error":"%s"}',
                str(exc)[:160],
            )
            return 0, False

        if deals is None:
            logger.warning(
                '{"event":"daily_trade_count_unavailable","reason":"history_deals_returned_none"}'
            )
            return 0, False

        entry_in = int(getattr(mt5, "DEAL_ENTRY_IN", 0))
        opening_trade_ids: set[tuple[str, int]] = set()
        for deal in deals:
            try:
                if int(getattr(deal, "magic")) != expected_magic:
                    continue
            except (TypeError, ValueError):
                # A deal we cannot attribute to ATLAX is excluded, not guessed.
                continue

            try:
                if int(getattr(deal, "entry")) != entry_in:
                    continue
            except (TypeError, ValueError):
                logger.warning(
                    '{"event":"daily_trade_count_unavailable",'
                    '"reason":"atlax_deal_entry_invalid"}'
                )
                return 0, False

            trade_id: Optional[tuple[str, int]] = None
            for field in ("position_id", "order", "ticket"):
                try:
                    value = int(getattr(deal, field, 0) or 0)
                except (TypeError, ValueError):
                    value = 0
                if value > 0:
                    trade_id = (field, value)
                    break

            if trade_id is None:
                logger.warning(
                    '{"event":"daily_trade_count_unavailable",'
                    '"reason":"atlax_open_deal_missing_identifier"}'
                )
                return 0, False
            opening_trade_ids.add(trade_id)

        count = len(opening_trade_ids)
        logger.info(
            '{"event":"daily_trade_count_calculated","trades_today":%s}',
            count,
        )
        return count, True

    @staticmethod
    def _finite_broker_decimal(value) -> Optional[Decimal]:
        """Parse any finite signed broker cash amount without a fallback."""
        try:
            parsed = Decimal(str(value))
        except (ArithmeticError, TypeError, ValueError):
            return None
        return parsed if parsed.is_finite() else None

    def _get_atlax_realized_pnl(
        self,
        start: datetime,
        end: datetime,
        *,
        window: str,
    ) -> tuple[Decimal, bool]:
        """Return net broker PnL for matching ATLAX exit deals in a UTC window."""
        magic_number = getattr(getattr(self, "_execution_cfg", None), "magic_number", None)
        try:
            expected_magic = int(magic_number)
        except (TypeError, ValueError):
            logger.warning(
                '{"event":"realized_pnl_unavailable","window":"%s",'
                '"reason":"execution_magic_invalid"}',
                window,
            )
            return Decimal("0"), False

        history_deals_get = getattr(mt5, "history_deals_get", None) if mt5 is not None else None
        if not callable(history_deals_get):
            logger.warning(
                '{"event":"realized_pnl_unavailable","window":"%s",'
                '"reason":"history_deals_unavailable"}',
                window,
            )
            return Decimal("0"), False

        try:
            deals = history_deals_get(start, end)
        except Exception as exc:  # noqa: BLE001 - unavailable PnL must block execution
            logger.warning(
                '{"event":"realized_pnl_unavailable","window":"%s",'
                '"reason":"history_deals_failed",'
                '"error":"%s"}',
                window,
                str(exc)[:160],
            )
            return Decimal("0"), False

        if deals is None:
            logger.warning(
                '{"event":"realized_pnl_unavailable","window":"%s",'
                '"reason":"history_deals_returned_none"}',
                window,
            )
            return Decimal("0"), False

        entry_out = int(getattr(mt5, "DEAL_ENTRY_OUT", 1))
        net_pnl = Decimal("0")
        matching_exit_count = 0
        for deal in deals:
            try:
                if int(getattr(deal, "magic")) != expected_magic:
                    continue
            except (TypeError, ValueError):
                # A deal that cannot be attributed to ATLAX is excluded.
                continue

            try:
                if int(getattr(deal, "entry")) != entry_out:
                    continue
            except (TypeError, ValueError):
                logger.warning(
                    '{"event":"realized_pnl_unavailable","window":"%s",'
                    '"reason":"atlax_deal_entry_invalid"}',
                    window,
                )
                return Decimal("0"), False

            values = [
                self._finite_broker_decimal(getattr(deal, field, None))
                for field in ("profit", "swap", "commission", "fee")
            ]
            if any(value is None for value in values):
                logger.warning(
                    '{"event":"realized_pnl_unavailable","window":"%s",'
                    '"reason":"malformed_atlax_exit_deal"}',
                    window,
                )
                return Decimal("0"), False

            net_pnl += sum(values, Decimal("0"))
            matching_exit_count += 1

        logger.info(
            '{"event":"realized_pnl_calculated","window":"%s",'
            '"net_pnl":"%s","matching_exit_deals":%s}',
            window,
            net_pnl,
            matching_exit_count,
        )
        return net_pnl, True

    def _get_atlax_daily_pnl(self) -> tuple[Decimal, bool]:
        """Return current UTC-day net realized PnL for ATLAX MT5 exits."""
        now = datetime.now(timezone.utc)
        day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        return self._get_atlax_realized_pnl(day_start, now, window="daily")

    def _get_atlax_weekly_pnl(self) -> tuple[Decimal, bool]:
        """Return current UTC ISO-week net realized PnL for ATLAX MT5 exits."""
        now = datetime.now(timezone.utc)
        week_start = (now - timedelta(days=now.weekday())).replace(
            hour=0,
            minute=0,
            second=0,
            microsecond=0,
        )
        return self._get_atlax_realized_pnl(week_start, now, window="weekly")

    def _calculate_open_atlax_risk(self, positions) -> Optional[Decimal]:
        """Calculate open ATLAX stop risk from broker economics.

        Only positions carrying the configured ATLAX magic number are included.
        If any included position lacks a usable stop or tick valuation, the
        result is UNKNOWN rather than treating the missing risk as zero.
        """
        magic_number = getattr(self._execution_cfg, "magic_number", None)
        if magic_number is None:
            return None

        total = Decimal("0")
        for position in positions or ():
            position_magic = getattr(position, "magic", None)
            if position_magic is None:
                return None
            try:
                if int(position_magic) != int(magic_number):
                    continue
            except (TypeError, ValueError):
                return None

            symbol = str(getattr(position, "symbol", "") or "").strip()
            stop = self._decimal_value(getattr(position, "sl", None), allow_zero=True)
            entry = self._decimal_value(getattr(position, "price_open", None))
            volume = self._decimal_value(getattr(position, "volume", None))
            if not symbol or stop is None or entry is None or volume is None:
                return None

            symbol_info = mt5.symbol_info(symbol) if mt5 is not None else None
            tick_value = self._decimal_value(
                getattr(symbol_info, "trade_tick_value", None)
                if symbol_info is not None
                else None
            )
            tick_size = self._decimal_value(
                getattr(symbol_info, "trade_tick_size", None)
                if symbol_info is not None
                else None
            )
            if tick_value is None or tick_size is None:
                return None

            stop_distance = abs(entry - stop)
            total += (stop_distance / tick_size) * tick_value * volume

        return total

    def _record_prop_firm_verification(
        self,
        state,
        result: PropFirmRuntimeResult,
    ) -> None:
        """Persist a changed verification state without writing every scan."""
        if (
            state.status is result.status
            and state.status_reason == result.reason
        ):
            return
        try:
            record_verification(
                self._runtime_store,
                status=result.status,
                reason=result.reason,
                actor="scanner",
            )
        except Exception as exc:  # noqa: BLE001 - runtime status must not crash scans
            logger.warning(
                '{"event":"prop_firm_verification_persist_failed","error":"%s"}',
                str(exc)[:160],
            )

    def _refresh_prop_firm_profile_monitor(self):
        """Refresh profile freshness without changing any source-defined rule."""
        try:
            report = self._profile_monitor.evaluate()
        except Exception as exc:  # noqa: BLE001 - live profile use must fail closed
            logger.error(
                '{"event":"prop_firm_profile_monitor_failed","error":"%s"}',
                str(exc)[:160],
            )
            return self._profile_monitor._unknown_report(
                datetime.now(timezone.utc).isoformat(),
                "Prop-Firm profile monitor failed",
            )
        for transition in report.transitions:
            logger.info(
                '{"event":"prop_firm_profile_monitor_transition",'
                '"profile_id":"%s","from_status":"%s",'
                '"to_status":"%s","reason":"%s"}',
                transition.get("profile_id", ""),
                transition.get("from_status", ""),
                transition.get("to_status", ""),
                transition.get("reason", "")[:240],
            )
            if getattr(self, "_operations_metrics", None) is not None:
                try:
                    self._operations_metrics.observe_prop_firm_profile(
                        transition.get("to_status", "UNKNOWN")
                    )
                except Exception as exc:  # noqa: BLE001 - metrics are observational
                    logger.warning(
                        '{"event":"prop_firm_profile_metrics_failed","error":"%s"}',
                        str(exc)[:160],
                    )
        return report

    def _prop_firm_profile_gate_reason(self, state) -> str | None:
        """Return a fail-closed reason when the selected profile is not live-approved."""
        if not hasattr(self, "_profile_monitor_report"):
            try:
                self._profile_monitor = PropFirmProfileMonitor(
                    self._runtime_store,
                    getattr(self, "_prop_firm_profiles", ()),
                    max_age_hours=float(
                        getattr(
                            getattr(self, "_account_mode_cfg", None),
                            "max_profile_age_hours",
                            168.0,
                        )
                    ),
                )
                self._profile_monitor_report = self._refresh_prop_firm_profile_monitor()
            except Exception as exc:  # noqa: BLE001 - live use must fail closed
                logger.error(
                    '{"event":"prop_firm_profile_monitor_lazy_init_failed",'
                    '"error":"%s"}',
                    str(exc)[:160],
                )
                return "Prop-Firm profile monitor is not initialized"
        profile_id = str(getattr(state, "selected_profile_id", "") or "").strip()
        profile_version = str(
            getattr(state, "selected_profile_version", "") or ""
        ).strip()
        if not profile_id or not profile_version:
            return "No complete Prop-Firm profile selection is available"
        if self._profile_monitor_report.is_live_approved(profile_id, profile_version):
            return None
        entry = self._profile_monitor_report.entry_for(profile_id, profile_version)
        if entry is None:
            return "Selected Prop-Firm profile is not present in the profile monitor"
        return (
            f"Selected Prop-Firm profile is not live-approved: {entry.status}; "
            f"{entry.reason[:160]}"
        )

    def _resolve_prop_firm_context(
        self,
        candidate,
        account_state: AccountState,
        positions=None,
    ):
        """Resolve a verified context for the current MT5 account.

        This provider is read-only with respect to MT5. Missing identity,
        position risk, profile, or configuration data returns UNKNOWN and is
        never converted into a permissive default.
        """
        state = load_account_mode_state(self._runtime_store)
        # Normal-account mode is an explicit risk-policy selection. An old
        # Prop-Firm binding must never promote it back into Prop-Firm mode.
        if state.mode is TradingAccountMode.NORMAL_ACCOUNT:
            return None

        if self._prop_firm_runtime is None:
            result = PropFirmRuntimeResult(
                AccountModeStatus.UNKNOWN,
                "Prop-Firm runtime adapter is not configured",
            )
            self._record_prop_firm_verification(state, result)
            return None

        profile_gate_reason = self._prop_firm_profile_gate_reason(state)
        if profile_gate_reason is not None:
            result = PropFirmRuntimeResult(
                AccountModeStatus.UNKNOWN,
                profile_gate_reason,
            )
            self._record_prop_firm_verification(state, result)
            logger.warning(
                '{"event":"prop_firm_profile_not_live_approved",'
                '"profile_id":"%s","profile_version":"%s","reason":"%s"}',
                getattr(state, "selected_profile_id", ""),
                getattr(state, "selected_profile_version", ""),
                profile_gate_reason[:200],
            )
            return None

        try:
            identity = read_mt5_account_identity(
                mt5,
                terminal_instance_id=getattr(
                    self._account_mode_cfg,
                    "terminal_instance_id",
                    None,
                ),
            )
            current_positions = (
                positions if positions is not None else mt5.positions_get()
            )
            if current_positions is None:
                raise RuntimeError("MT5 positions_get is unavailable")
            open_risk = self._calculate_open_atlax_risk(current_positions)
            if open_risk is None:
                result = PropFirmRuntimeResult(
                    AccountModeStatus.UNKNOWN,
                    "Open ATLAX risk is unavailable or broker stop valuation is incomplete",
                )
            else:
                result = self._prop_firm_runtime.resolve(
                    state=state,
                    identity=identity,
                    account=account_state,
                    open_risk=open_risk,
                )
        except Exception as exc:  # noqa: BLE001 - fail closed at the caller
            result = PropFirmRuntimeResult(
                AccountModeStatus.UNKNOWN,
                f"Prop-Firm runtime data is unavailable: {str(exc)[:160]}",
            )

        self._record_prop_firm_verification(state, result)
        if result.status is AccountModeStatus.VERIFIED and result.context is not None:
            logger.info(
                '{"event":"prop_firm_context_ready","candidate_id":"%s",'
                '"profile_id":"%s","profile_version":"%s"}',
                getattr(candidate, "candidate_id", ""),
                result.profile.profile_id if result.profile else "",
                result.profile.version if result.profile else "",
            )
            return result.context

        logger.warning(
            '{"event":"prop_firm_context_unknown","candidate_id":"%s",'
            '"status":"%s","reason":"%s"}',
            getattr(candidate, "candidate_id", ""),
            result.status.value,
            result.reason[:200],
        )
        return None

    def _auto_activate_bound_account_mode(self) -> None:
        """Activate automatic enforcement when the bound MT5 account is present."""
        if not bool(getattr(self._account_mode_cfg, "auto_enforce_when_bound", False)):
            return
        state = load_account_mode_state(self._runtime_store)
        if state.mode is not TradingAccountMode.AUTO_ENFORCED:
            return
        if state.binding is None or not state.selected_profile_id:
            logger.warning(
                '{"event":"prop_firm_auto_mode_blocked",'
                '"reason":"account binding or profile is missing"}'
            )
            return
        try:
            identity = read_mt5_account_identity(
                mt5,
                terminal_instance_id=getattr(
                    self._account_mode_cfg,
                    "terminal_instance_id",
                    None,
                ),
            )
            if not state.binding.matches(identity):
                self._record_prop_firm_verification(
                    state,
                    PropFirmRuntimeResult(
                        AccountModeStatus.MISMATCH,
                        "Connected MT5 account does not match the approved binding",
                    ),
                )
                logger.warning(
                    '{"event":"prop_firm_auto_mode_blocked",'
                    '"reason":"connected MT5 account does not match binding",'
                    '"login":"%s","server":"%s"}',
                    identity.login,
                    identity.server,
                )
                return
            profile_gate_reason = self._prop_firm_profile_gate_reason(state)
            if profile_gate_reason is not None:
                self._record_prop_firm_verification(
                    state,
                    PropFirmRuntimeResult(AccountModeStatus.UNKNOWN, profile_gate_reason),
                )
                logger.warning(
                    '{"event":"prop_firm_auto_mode_blocked",'
                    '"reason":"profile_not_live_approved","detail":"%s"}',
                    profile_gate_reason[:200],
                )
                return
            if state.mode is TradingAccountMode.NORMAL_ACCOUNT:
                activated = set_account_mode(
                    self._runtime_store,
                    mode=TradingAccountMode.AUTO_ENFORCED,
                    actor="scanner:auto_account_detection",
                )
            else:
                activated = state
            selected_profile = next(
                (
                    item for item in self._prop_firm_profiles
                    if getattr(item, "profile_id", "") == activated.selected_profile_id
                    and getattr(item, "version", "") == activated.selected_profile_version
                ),
                None,
            )
            if selected_profile is None:
                self._record_prop_firm_verification(
                    activated,
                    PropFirmRuntimeResult(
                        AccountModeStatus.UNKNOWN,
                        "Selected Prop-Firm profile is not loaded as a typed source-backed profile",
                    ),
                )
                return
            self._record_prop_firm_verification(
                activated,
                PropFirmRuntimeResult(
                    AccountModeStatus.VERIFIED,
                    "Connected MT5 account matches the approved Prop-Firm binding and profile",
                ),
            )
            logger.info(
                '{"event":"prop_firm_auto_mode_activated",'
                '"login":"%s","server":"%s",'
                '"profile_id":"%s"}',
                identity.login,
                identity.server,
                activated.selected_profile_id,
            )
        except Exception as exc:  # noqa: BLE001 - fail closed
            logger.warning(
                '{"event":"prop_firm_auto_mode_activation_failed",'
                '"error":"%s"}',
                str(exc)[:160],
            )

    def _observe_prop_firm_shadow(self, candidate, account_state, current_evaluation) -> None:
        """Record Prop-Firm shadow evidence without changing live evaluation."""
        if self._prop_firm_shadow is None:
            return
        state = load_account_mode_state(self._runtime_store)
        if state.mode is not TradingAccountMode.PROP_FIRM_SHADOW:
            return
        event = self._prop_firm_shadow.observe(
            candidate,
            account_state,
            current_evaluation,
        )
        if event:
            logger.info(
                '{"event":"prop_firm_shadow_recorded","candidate_id":"%s",'
                '"category":"%s"}',
                getattr(candidate, "candidate_id", ""),
                event.get("category", event.get("status", "UNKNOWN")),
            )

    def _runtime_store_available(self) -> bool:
        """Check runtime-state readability without inventing a fallback."""
        try:
            self._runtime_store.get("operations.circuit_breaker", None)
            return True
        except Exception as exc:  # noqa: BLE001 - breaker must fail closed
            logger.error(
                '{"event":"runtime_store_healthcheck_failed","error":"%s"}',
                str(exc)[:160],
            )
            return False

    def _persist_prop_firm_recovery_state(
        self,
        context,
        decision,
    ) -> bool:
        """Persist the evaluated recovery transition before a new entry."""

        recovery_config = getattr(context, "recovery_config", None)
        if recovery_config is None or not bool(
            getattr(recovery_config, "enabled", False)
        ):
            return True

        next_state = getattr(decision, "recovery_state", None)
        state_key = str(getattr(context, "recovery_state_key", "") or "").strip()
        if next_state is None or not state_key:
            logger.error(
                '{"event":"prop_firm_recovery_persistence_failed",'
                '"reason":"missing_state_or_account_scoped_key"}'
            )
            return False
        try:
            payload = next_state.to_dict()
            self._runtime_store.set(state_key, payload)
            self._runtime_store.set("prop_firm.recovery_state", payload)
            snapshot = dict(
                self._runtime_store.get("prop_firm.snapshot", {}) or {}
            )
            snapshot.update(
                {
                    "recovery_mode": next_state.mode.value,
                    "risk_multiplier": (
                        str(decision.risk_multiplier)
                        if decision.risk_multiplier is not None
                        else None
                    ),
                    "remaining_overall_buffer": (
                        str(decision.remaining_overall_buffer)
                        if decision.remaining_overall_buffer is not None
                        else None
                    ),
                    "remaining_daily_buffer": (
                        str(decision.remaining_daily_buffer)
                        if decision.remaining_daily_buffer is not None
                        else None
                    ),
                }
            )
            self._runtime_store.set("prop_firm.snapshot", snapshot)
            return True
        except Exception as exc:  # noqa: BLE001 - recovery must fail closed
            logger.error(
                '{"event":"prop_firm_recovery_persistence_failed",'
                '"error":"%s"}',
                str(exc)[:200],
            )
            return False

    def _evaluate_circuit_breaker(
        self,
        *,
        account_info,
        account_state: AccountState,
        mode_state,
        prop_firm_context,
        loss_budget,
        telegram_channel,
    ) -> CircuitBreakerDecision | None:
        """Evaluate and report the new-entry safety gate."""
        if self._circuit_breaker is None:
            return None
        decision = self._circuit_breaker.evaluate(
            mt5_available=account_info is not None,
            trade_allowed=getattr(account_info, "trade_allowed", None),
            trade_expert=getattr(account_info, "trade_expert", None),
            manual_paused=self._is_paused,
            storage_available=self._runtime_store_available(),
            account_mode=mode_state.mode,
            account_status=mode_state.status,
            prop_firm_context_ready=prop_firm_context is not None,
            account=account_state,
            risk_config=self._risk_cfg,
            loss_budget=loss_budget,
            reconciliation_status=self._reconciliation_status,
        )
        if decision.transitioned:
            logger.info(
                '{"event":"circuit_breaker_state_changed","state":"%s",'
                '"allowed":%s,"reason_code":"%s","reason":"%s"}',
                decision.state.value,
                str(decision.allowed).lower(),
                decision.reason_code,
                decision.reason[:200],
            )
            if self._operations_metrics is not None:
                try:
                    self._operations_metrics.observe_circuit_breaker(
                        decision.state.value,
                        decision.reason_code,
                    )
                except Exception as exc:  # noqa: BLE001 - metrics are observational
                    logger.warning(
                        '{"event":"circuit_breaker_metrics_failed","error":"%s"}',
                        str(exc)[:160],
                    )
            if (
                not decision.allowed
                and self._circuit_breaker.config.notify_admin_on_transition
                and telegram_channel is not None
            ):
                admin_id = str(
                    getattr(getattr(telegram_channel, "_config", None), "chat_id", "")
                    or ""
                ).strip()
                if admin_id:
                    try:
                        telegram_channel.send_reply(
                            admin_id,
                            "🛡️ <b>ATLAX New-Entry Circuit Breaker</b>\n\n"
                            f"Status: <b>{decision.state.value}</b>\n"
                            f"Reason: <code>{escape(decision.reason_code)}</code>\n"
                            f"{escape(decision.reason[:240])}\n\n"
                            "Existing-position protection remains governed by the Trade Manager.",
                        )
                    except Exception as exc:  # noqa: BLE001 - notification cannot change safety
                        logger.warning(
                            '{"event":"circuit_breaker_notification_failed",'
                            '"error":"%s"}',
                            str(exc)[:160],
                        )
            self._record_execution_quality_event(
                ExecutionQualityEvent.new(
                    ExecutionQualityEventType.CIRCUIT_BREAKER,
                    action=decision.reason_code,
                    status=decision.state.value,
                    reason=decision.reason[:240],
                    metadata={"allowed": bool(decision.allowed)},
                )
            )
        return decision

    def _watchdog_heartbeat(self, telegram_channel=None) -> None:
        """Publish bounded runtime health without changing trading decisions."""
        watchdog = getattr(self, "_watchdog", None)
        if watchdog is None:
            return
        self._purge_expired_watchdog_messages(telegram_channel)
        if not watchdog.enabled:
            return
        now_mono = time.monotonic()
        interval = float(
            getattr(getattr(watchdog, "_config", None), "heartbeat_interval_seconds", 30)
        )
        last_probe = float(getattr(self, "_last_watchdog_probe_monotonic", 0.0))
        if now_mono - last_probe < interval:
            return
        self._last_watchdog_probe_monotonic = now_mono
        observed_at = datetime.now(timezone.utc).isoformat()

        scanner_status = {
            "status": "HEALTHY" if self._last_cycle_completed_at else "UNKNOWN",
            "reason": (
                "scan_cycle_completed"
                if self._last_cycle_completed_at
                else "no_scan_cycle_completed"
            ),
            "last_success_at": self._last_cycle_completed_at,
        }
        instance_lock = watchdog.check_instance_lock(current_process_owns_lock=True)

        try:
            account_info = mt5.account_info() if mt5 is not None else None
            mt5_status = {
                "status": "HEALTHY" if account_info is not None else "UNKNOWN",
                "reason": (
                    "mt5_account_info_available"
                    if account_info is not None
                    else "mt5_account_info_unavailable"
                ),
                "last_success_at": (
                    observed_at if account_info is not None else None
                ),
            }
        except Exception as exc:  # noqa: BLE001 - health becomes UNKNOWN
            mt5_status = {
                "status": "UNKNOWN",
                "reason": f"mt5_probe_failed:{str(exc)[:160]}",
            }

        market_status = {"status": "UNKNOWN", "reason": "no_market_data_observed"}
        if self._last_market_data_at:
            try:
                data_age = (
                    datetime.now(timezone.utc)
                    - datetime.fromisoformat(self._last_market_data_at).astimezone(
                        timezone.utc
                    )
                ).total_seconds()
                stale_after = float(
                    getattr(
                        getattr(watchdog, "_config", None),
                        "market_data_stale_after_seconds",
                        180,
                    )
                )
                if data_age > stale_after:
                    market_status = {
                        "status": "DEGRADED",
                        "reason": f"market_data_stale:{int(data_age)}s",
                        "last_success_at": self._last_market_data_at,
                    }
                elif self._cycle_instrument_failures:
                    market_status = {
                        "status": "DEGRADED",
                        "reason": (
                            f"instrument_failures:{self._cycle_instrument_failures}"
                        ),
                        "last_success_at": self._last_market_data_at,
                    }
                else:
                    market_status = {
                        "status": "HEALTHY",
                        "reason": (
                            f"fresh_instruments:{self._cycle_instruments_processed}"
                        ),
                        "last_success_at": self._last_market_data_at,
                    }
            except (TypeError, ValueError, AttributeError):
                market_status = {
                    "status": "UNKNOWN",
                    "reason": "market_data_timestamp_invalid",
                    "last_success_at": self._last_market_data_at,
                }

        telegram_poll = {"status": "UNKNOWN", "reason": "telegram_channel_missing"}
        telegram_delivery = {
            "status": "UNKNOWN",
            "reason": "telegram_delivery_not_observed",
        }
        if telegram_channel is not None and telegram_channel.is_enabled:
            last_poll = getattr(telegram_channel, "_last_poll_at", None)
            if getattr(telegram_channel, "_last_poll_failed", False):
                telegram_poll = {
                    "status": "DEGRADED",
                    "reason": "telegram_poll_failed",
                    "last_success_at": getattr(
                        telegram_channel, "_last_poll_success_at", None
                    ),
                }
            elif not last_poll:
                telegram_poll = {
                    "status": "UNKNOWN",
                    "reason": "telegram_poll_not_observed",
                    "last_success_at": None,
                }
            else:
                try:
                    poll_age = (
                        datetime.now(timezone.utc)
                        - datetime.fromisoformat(last_poll).astimezone(timezone.utc)
                    ).total_seconds()
                    stale_after = float(
                        getattr(
                            getattr(watchdog, "_config", None),
                            "telegram_stale_after_seconds",
                            180,
                        )
                    )
                    telegram_poll = (
                        {
                            "status": "DEGRADED",
                            "reason": f"telegram_poll_stale:{int(poll_age)}s",
                            "last_success_at": getattr(
                                telegram_channel, "_last_poll_success_at", None
                            ),
                        }
                        if poll_age > stale_after
                        else {
                            "status": "HEALTHY",
                            "reason": "telegram_poll_recent",
                            "last_success_at": getattr(
                                telegram_channel, "_last_poll_success_at", None
                            ),
                        }
                    )
                except (TypeError, ValueError, AttributeError):
                    telegram_poll = {
                        "status": "UNKNOWN",
                        "reason": "telegram_poll_timestamp_invalid",
                        "last_success_at": getattr(
                            telegram_channel, "_last_poll_success_at", None
                        ),
                    }
            last_delivery = getattr(telegram_channel, "_last_delivery_at", None)
            last_delivery_failed = getattr(
                telegram_channel, "_last_delivery_failed_at", None
            )
            delivery_failed = (
                bool(last_delivery_failed)
                and (
                    not last_delivery
                    or str(last_delivery_failed) > str(last_delivery)
                )
            )
            if delivery_failed:
                telegram_delivery = {
                    "status": "DEGRADED",
                    "reason": "telegram_delivery_failed",
                    "last_success_at": last_delivery,
                }
            elif last_delivery:
                telegram_delivery = {
                    "status": "HEALTHY",
                    "reason": "telegram_delivery_confirmed",
                    "last_success_at": last_delivery,
                }

        runtime_status = {
            "status": "HEALTHY" if self._runtime_store_available() else "UNKNOWN",
            "reason": "runtime_store_available",
            "last_success_at": observed_at,
        }
        if runtime_status["status"] != "HEALTHY":
            runtime_status["reason"] = "runtime_store_unavailable"
            runtime_status["last_success_at"] = None

        try:
            self._journal.list_execution_quality_events(limit=1)
            journal_status = {
                "status": "HEALTHY",
                "reason": "journal_read_available",
                "last_success_at": observed_at,
            }
        except Exception as exc:  # noqa: BLE001 - unknown persistence blocks confidence
            journal_status = {
                "status": "UNKNOWN",
                "reason": f"journal_read_failed:{str(exc)[:160]}",
            }

        try:
            self._admin_audit.recent(limit=1)
            audit_status = {"status": "HEALTHY", "reason": "audit_read_available"}
        except Exception as exc:  # noqa: BLE001 - audit health is observational
            audit_status = {
                "status": "UNKNOWN",
                "reason": f"audit_read_failed:{str(exc)[:160]}",
            }

        subscriber_status = (
            {
                "status": "HEALTHY",
                "reason": "subscriber_store_initialized",
                "last_success_at": observed_at,
            }
            if self._subscriber_db is not None
            else {"status": "UNKNOWN", "reason": "subscriber_store_missing"}
        )
        payment_status = (
            {
                "status": "HEALTHY",
                "reason": "payment_configuration_loaded",
                "last_success_at": observed_at,
            }
            if self._payment_cfg is not None and self._payment_cfg.enabled
            else {"status": "HEALTHY", "reason": "payment_service_disabled"}
        )
        config_status = watchdog.check_configuration(
            str(getattr(getattr(watchdog, "_config", None), "config_path", "config/atlax.yaml"))
        )

        watchdog.heartbeat(
            {
                "scanner": scanner_status,
                "instance_lock": instance_lock,
                "mt5_ipc": mt5_status,
                "market_data": market_status,
                "telegram_polling": telegram_poll,
                "telegram_delivery": telegram_delivery,
                "runtime_state": runtime_status,
                "journal": journal_status,
                "audit": audit_status,
                "subscriber_service": subscriber_status,
                "payment_service": payment_status,
                "configuration": config_status,
            }
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def _telegram_channel(self):
        """Return the configured Telegram channel, if one is available."""
        return next(
            (channel for channel in self._alert_engine._channels if channel.name == "telegram"),
            None,
        )

    def _configure_telegram_admin_surface(self, telegram_channel) -> None:
        """Register Telegram controls before MT5 connects so recovery stays operable."""
        if telegram_channel is None or not telegram_channel.is_enabled:
            return
        telegram_channel.register_bot_commands()
        telegram_channel.register_mini_app_menu_button()
        if self._watchdog is not None:
            admin_id = str(
                getattr(getattr(telegram_channel, "_config", None), "chat_id", "") or ""
            ).strip()
            if admin_id:
                self._watchdog.set_notifier(
                    lambda message: self._send_temporary_watchdog_notification(
                        telegram_channel,
                        admin_id,
                        message,
                    )
                )

    _WATCHDOG_EXPIRING_MESSAGES_KEY = "telegram.watchdog_expiring_messages"

    def _watchdog_expiring_messages(self) -> list[dict]:
        """Load only valid, non-secret records for temporary watchdog notices."""
        raw = self._runtime_store.get(self._WATCHDOG_EXPIRING_MESSAGES_KEY, []) or []
        if not isinstance(raw, list):
            return []
        records = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            chat_id = str(item.get("chat_id", "")).strip()
            message_id = item.get("message_id")
            expires_at = str(item.get("expires_at", "")).strip()
            if (
                not chat_id
                or isinstance(message_id, bool)
                or not isinstance(message_id, int)
                or message_id <= 0
                or not expires_at
            ):
                continue
            try:
                datetime.fromisoformat(expires_at).astimezone(timezone.utc)
            except (TypeError, ValueError, OverflowError):
                continue
            records.append(
                {
                    "chat_id": chat_id,
                    "message_id": message_id,
                    "expires_at": expires_at,
                }
            )
        return records

    def _save_watchdog_expiring_messages(self, records: list[dict]) -> None:
        self._runtime_store.set(self._WATCHDOG_EXPIRING_MESSAGES_KEY, records)

    def _send_temporary_watchdog_notification(
        self,
        telegram_channel,
        admin_id: str,
        message: str,
    ) -> None:
        """Deliver a watchdog notice and persist its configured expiry."""
        message_id = self._send_one_time_telegram_prompt(telegram_channel, admin_id, message)
        if message_id is None:
            return
        watchdog = getattr(self, "_watchdog", None)
        ttl_seconds = int(
            getattr(getattr(watchdog, "_config", None), "notification_ttl_seconds")
        )
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)
        records = self._watchdog_expiring_messages()
        records.append(
            {
                "chat_id": str(admin_id),
                "message_id": message_id,
                "expires_at": expires_at.isoformat(),
            }
        )
        self._save_watchdog_expiring_messages(records)

    def _purge_expired_watchdog_messages(self, telegram_channel) -> None:
        """Delete only tracked watchdog notices after their configured TTL."""
        if telegram_channel is None or not getattr(telegram_channel, "is_enabled", True):
            return
        records = self._watchdog_expiring_messages()
        if not records:
            return
        now = datetime.now(timezone.utc)
        remaining = []
        for record in records:
            try:
                expires_at = datetime.fromisoformat(record["expires_at"]).astimezone(
                    timezone.utc
                )
            except (TypeError, ValueError, OverflowError):
                continue
            if expires_at <= now:
                try:
                    deleted = telegram_channel.delete_message(
                        record["chat_id"], record["message_id"]
                    )
                except Exception:  # noqa: BLE001 - cleanup cannot affect scanning
                    deleted = False
                if not deleted:
                    remaining.append(record)
            else:
                remaining.append(record)
        if remaining != records:
            self._save_watchdog_expiring_messages(remaining)

    def _poll_telegram_commands(self, telegram_channel) -> None:
        """Poll one Telegram batch without requiring an active MT5 connection."""
        if telegram_channel is None or not telegram_channel.is_enabled:
            return
        commands = telegram_channel.poll_commands()
        if commands:
            self._handle_telegram_commands(commands, telegram_channel)

    def _active_trading_account_key(self) -> str:
        """Return a non-secret marker for the selected MT5 target."""
        registry = load_trading_account_registry(self._runtime_store)
        return registry.active_profile_id

    def _active_mt5_config(self):
        """Use a selected Telegram account as the exact terminal-session target.

        A saved account always uses terminal-session mode. This deliberately
        suppresses the process-wide password fallback so a stale environment
        secret cannot attempt to authenticate the newly selected account.
        """
        registry = load_trading_account_registry(self._runtime_store)
        active = registry.active_account
        if active is None:
            return self._market_cfg.mt5
        return replace(
            self._market_cfg.mt5,
            account=active.login,
            server=active.server,
            password="",
        )

    def _selected_account_matches_terminal(self) -> bool:
        """Verify that a selected target remains the live MT5 identity.

        This catches an operator changing accounts directly in MT5 after a
        scanner connection was established. No selected target preserves the
        legacy configuration-driven connection behavior.
        """
        registry = load_trading_account_registry(self._runtime_store)
        active = registry.active_account
        if active is None:
            return True
        if mt5 is None:
            return False
        account_info = mt5.account_info()
        return bool(
            account_info is not None
            and getattr(account_info, "login", None) == active.login
            and str(getattr(account_info, "server", "")).strip() == active.server.strip()
        )

    def _wait_for_mt5_reconnect(self, telegram_channel, selection_key: str) -> None:
        """Keep the admin account menu live while MT5 is unavailable."""
        deadline = time.monotonic() + 5.0
        while self._running and time.monotonic() < deadline:
            self._poll_telegram_commands(telegram_channel)
            self._watchdog_heartbeat(telegram_channel)
            try:
                if self._active_trading_account_key() != selection_key:
                    return
            except ValueError:
                # The next outer-loop pass records the invalid registry and
                # remains fail-closed.
                return
            time.sleep(1.0)

    def run(self) -> None:
        """
        Start the scanner loop.

        Connects to MT5, registers shutdown handlers, then loops
        until stopped. Each cycle processes all instruments.
        """
        if not self._market_cfg.enabled:
            logger.warning('{"event":"scanner_disabled","reason":"market_data.enabled=false"}')
            return

        self._register_signal_handlers()
        self._running = True

        logger.info(
            '{"event":"scanner_start","instruments":%d,"profiles":%d,'
            '"poll_interval_s":%d}',
            len(self._market_cfg.instruments),
            len(self._strategy_cfg.active_profiles),
            self._market_cfg.poll_interval_seconds,
        )

        telegram_channel = self._telegram_channel()
        self._configure_telegram_admin_surface(telegram_channel)
        try:
            while self._running:
                telegram_channel = self._telegram_channel()
                try:
                    selection_key = self._active_trading_account_key()
                    connection = MT5Connection(self._active_mt5_config())
                except ValueError as exc:
                    logger.error(
                        '{"event":"trading_account_registry_invalid","reason":"%s"}',
                        str(exc)[:160],
                    )
                    self._wait_for_mt5_reconnect(telegram_channel, "invalid")
                    continue

                try:
                    connection.initialize()
                except MT5ConnectionError as exc:
                    logger.error(
                        '{"event":"scanner_mt5_connect_failed","reason":"%s"}', str(exc)
                    )
                    self._wait_for_mt5_reconnect(telegram_channel, selection_key)
                    continue

                self._ensure_configured_symbols()
                self._auto_activate_bound_account_mode()
                reconnect_requested = False
                try:
                    while self._running:
                        if self._active_trading_account_key() != selection_key:
                            reconnect_requested = True
                            logger.info(
                                '{"event":"trading_account_reconnect_requested"}'
                            )
                            break
                        if not self._selected_account_matches_terminal():
                            reconnect_requested = True
                            logger.warning(
                                '{"event":"trading_account_terminal_mismatch",'
                                '"reason":"selected MT5 identity is no longer active"}'
                            )
                            break
                        cycle_start = time.monotonic()
                        if not self._is_paused:
                            self._run_cycle()
                        else:
                            self._run_subscription_lifecycle()
                        self._watchdog_heartbeat(telegram_channel)
                        elapsed = time.monotonic() - cycle_start
                        sleep_time = max(0, self._market_cfg.poll_interval_seconds - elapsed)

                        # Micro-sleep loop for responsive command polling.
                        # Uses backoff to avoid log spam when Telegram is unreachable.
                        if self._running:
                            telegram_channel = self._telegram_channel()
                            slept = 0
                            _poll_fail_streak = 0
                            _poll_backoff_until = 0.0
                            while slept < sleep_time and self._running:
                                if self._active_trading_account_key() != selection_key:
                                    reconnect_requested = True
                                    break
                                if telegram_channel and telegram_channel.is_enabled:
                                    now_mono = time.monotonic()
                                    if now_mono >= _poll_backoff_until:
                                        cmds = telegram_channel.poll_commands()
                                        self._watchdog_heartbeat(telegram_channel)
                                        if cmds:
                                            _poll_fail_streak = 0
                                            self._handle_telegram_commands(cmds, telegram_channel)
                                            if self._active_trading_account_key() != selection_key:
                                                reconnect_requested = True
                                                break
                                        elif (
                                            hasattr(telegram_channel, "_last_poll_failed")
                                            and telegram_channel._last_poll_failed
                                        ):
                                            _poll_fail_streak += 1
                                            # Backoff: 5s, 15s, 30s cap.
                                            backoff = min(5 * _poll_fail_streak, 30)
                                            _poll_backoff_until = now_mono + backoff
                                        else:
                                            _poll_fail_streak = 0
                                time.sleep(1.0)
                                slept += 1.0
                finally:
                    connection.shutdown()

                if reconnect_requested:
                    continue
        except Exception as exc:  # noqa: BLE001
            logger.error(
                '{"event":"scanner_unhandled_error","error":"%s"}',
                str(exc)[:200],
            )
            raise
        finally:
            logger.info(
                '{"event":"scanner_stopped","total_cycles":%d}',
                self._cycle_count,
            )

    def stop(self) -> None:
        """Request a graceful shutdown after the current cycle completes."""
        self._running = False
        logger.info('{"event":"scanner_stop_requested"}')

    def _ensure_configured_symbols(self) -> None:
        """Make every configured symbol visible before market data or execution."""
        try:
            import MetaTrader5 as mt5
        except ImportError:
            logger.error('{"event":"symbol_universe_check_failed","reason":"mt5_not_installed"}')
            return

        symbols = sorted({instrument.symbol for instrument in self._market_cfg.instruments})
        unavailable: list[str] = []
        selected = 0

        for symbol in symbols:
            broker_symbol = resolve_mt5_symbol(mt5, symbol)
            info = mt5.symbol_info(broker_symbol) if broker_symbol else None
            if info is None:
                unavailable.append(symbol)
                continue
            if not bool(getattr(info, "visible", True)):
                if mt5.symbol_select(broker_symbol, True):
                    selected += 1
                else:
                    unavailable.append(symbol)
            if broker_symbol != symbol:
                logger.info(
                    '{"event":"mt5_symbol_resolved","canonical":"%s",'
                    '"broker_symbol":"%s"}',
                    symbol,
                    broker_symbol,
                )

        logger.info(
            '{"event":"symbol_universe_ready","configured":%d,"selected":%d,"unavailable":%d}',
            len(symbols), selected, len(unavailable),
        )
        if unavailable:
            logger.warning(
                '{"event":"symbol_universe_unavailable","symbols":"%s"}',
                ",".join(unavailable),
            )

    def _load_alerted_state(self) -> dict:
        """
        Load the persisted deduplication state from disk.
        Returns a dict mapping "SYMBOL_TIMEFRAME" to naive datetime objects.
        On first run or if file is corrupt, returns an empty dict.

        IMPORTANT: MT5 candle close_time is a naive (tz-unaware) datetime.
        We strip tzinfo on load so comparisons always work regardless of
        whether the stored ISO string has a +00:00 suffix.
        """
        data = self._runtime_store.get("scanner.last_alerted", {})
        loaded = {}
        for key, val in (data.items() if isinstance(data, dict) else []):
            try:
                loaded[key] = datetime.fromisoformat(str(val)).replace(tzinfo=None)
            except (ValueError, TypeError):
                pass
        logger.info('{"event":"alerted_state_loaded","entries":%d}', len(loaded))
        return loaded

    def _save_alerted_state(self) -> None:
        """
        Persist the current deduplication state to disk.
        Called after every new detection to survive restarts.
        Saves naive ISO strings (no tz suffix) to match MT5 candle datetimes.
        """
        serializable = {
            key: value.replace(tzinfo=None).isoformat() if hasattr(value, "replace") else str(value)
            for key, value in self._last_alerted.items()
        }
        self._runtime_store.set("scanner.last_alerted", serializable)
        return

    def _reconcile_closed_executions(self) -> None:
        """Settle journal execution records from broker-authoritative exit deals.

        Reconciliation is deliberately read-only with respect to MT5.  An
        execution is marked realized only when a matching exit deal for the
        configured ATLAX magic number is present and the PnL calculation is
        unambiguous.
        """
        journal = getattr(self, "_journal", None)
        if journal is None or not hasattr(journal, "list_unsettled_executions"):
            return

        try:
            import importlib

            # Resolve at call time so tests and long-running deployments can
            # replace/reconnect the broker module without stale references.
            broker = importlib.import_module("MetaTrader5")
            magic_number = int(getattr(self._execution_cfg, "magic_number", 0))
            now = datetime.now(timezone.utc)
            unsettled = journal.list_unsettled_executions() or []
            for record in unsettled:
                attempted_at = record.get("attempted_at")
                try:
                    start = datetime.fromisoformat(str(attempted_at).replace("Z", "+00:00"))
                except (TypeError, ValueError):
                    start = now
                if start.tzinfo is None:
                    start = start.replace(tzinfo=timezone.utc)

                deals = broker.history_deals_get(start, now) or []
                reconciliation = reconcile_mt5_exit_deals(
                    deals,
                    magic_number=magic_number,
                    position_id=record.get("mt5_position_id"),
                    order_id=None,
                )
                if reconciliation.status != "OK":
                    continue
                journal.record_execution_realization(
                    record["execution_record_id"],
                    reconciliation,
                    realized_at=now,
                )
                logger.info(
                    '{"event":"execution_reconciled","execution_record_id":"%s",'
                    '"status":"%s","net_pnl":"%s","deals":%d}',
                    str(record.get("execution_record_id", ""))[:80],
                    reconciliation.status,
                    str(reconciliation.net_pnl),
                    reconciliation.deals_count,
                )
        except (ImportError, OSError, RuntimeError, TypeError, ValueError) as exc:
            logger.warning(
                '{"event":"execution_reconciliation_failed","error":"%s"}',
                str(exc)[:160],
            )

    def _reconcile_trade_state(self) -> None:
        """Reconcile ATLAX-owned broker state before scanning for new entries."""
        now = datetime.now(timezone.utc)
        unknown = {
            "status": "UNKNOWN",
            "observed_at": now.isoformat(),
            "blocks_new_entries": True,
            "counts": {},
            "items": [],
        }
        try:
            import importlib

            broker = importlib.import_module("MetaTrader5")
            positions_get = getattr(broker, "positions_get", None)
            orders_get = getattr(broker, "orders_get", None)
            if not callable(positions_get) or not callable(orders_get):
                raise RuntimeError("MT5 position or pending-order API is unavailable")
            positions = positions_get()
            pending_orders = orders_get()
            if positions is None or pending_orders is None:
                raise RuntimeError("MT5 position or pending-order snapshot is unavailable")

            # Settle broker-confirmed exits before comparing active state.
            self._reconcile_closed_executions()
            records = self._journal.list_unsettled_executions()
            report = reconcile_trade_state(
                records,
                positions,
                pending_orders,
                magic_number=int(self._execution_cfg.magic_number),
                observed_at=now,
            )
            payload = report.to_dict()
            self._reconciliation_status = report.status
            self._reconciliation_report = payload
            self._runtime_store.set("operations.reconciliation", payload)
            if self._operations_metrics is not None:
                self._operations_metrics.observe_reconciliation(report.status)
            logger.info(
                '{"event":"trade_state_reconciled","status":"%s",'
                '"matched":%d,"missing_from_atlax":%d,"missing_from_mt5":%d,'
                '"conflicts":%d,"unknown":%d}',
                report.status,
                report.matched,
                report.missing_from_atlax,
                report.missing_from_mt5,
                report.conflicts,
                report.unknown,
            )
            return
        except Exception as exc:  # noqa: BLE001 - uncertain broker state fails closed
            unknown["items"] = [
                {
                    "status": "UNKNOWN",
                    "subject": "runtime",
                    "execution_record_id": "",
                    "broker_id": "",
                    "reason": str(exc)[:240],
                }
            ]
            logger.warning(
                '{"event":"trade_state_reconciliation_unknown","error":"%s"}',
                str(exc)[:160],
            )
        self._reconciliation_status = "UNKNOWN"
        self._reconciliation_report = unknown
        try:
            self._runtime_store.set("operations.reconciliation", unknown)
            if self._operations_metrics is not None:
                self._operations_metrics.observe_reconciliation("UNKNOWN")
        except Exception as exc:  # noqa: BLE001 - breaker also checks storage
            logger.error(
                '{"event":"reconciliation_state_persist_failed","error":"%s"}',
                str(exc)[:160],
            )

    def _verify_crypto_for_admin_approval(self, intent: dict):
        """Allow admin approval only after an exact chain verification result."""
        verifier = getattr(self, "_payment_verifier", None)
        if verifier is None:
            from types import SimpleNamespace

            result = SimpleNamespace(
                status="unknown",
                network=str(intent.get("method", "")),
                reason="automatic payment verifier is not configured",
            )
        elif not bool(getattr(verifier, "enabled", False)):
            result = verifier.verify(intent)
        else:
            result = verifier.verify(intent)

        status = str(getattr(result, "status", "unknown")).lower()
        allowed = status == "verified"
        self._record_operational_audit(
            str(intent.get("chat_id", "")),
            "crypto_payment_verification",
            result=status,
            metadata={
                "reference": str(intent.get("reference", ""))[:80],
                "network": str(getattr(result, "network", ""))[:40],
                "reason": str(getattr(result, "reason", ""))[:160],
            },
        )
        return allowed, result
    def run_once(self) -> None:
        """
        Run a single scan cycle without connecting to MT5.
        Used for testing with a mock feed.
        """
        self._run_cycle()

    # ------------------------------------------------------------------
    # Interactive Inline Keyboard Menu
    # ------------------------------------------------------------------

    def _is_admin(self, chat_id: str) -> bool:
        """Return True only if chat_id matches the configured admin chat.
        Authority: docs/16_SECURITY.md (least-privilege, privacy isolation)
        """
        telegram_channel = next(
            (c for c in self._alert_engine._channels if c.name == "telegram"), None
        )
        alert_event = None
        alert_delivery_status = "unavailable"
        if telegram_channel is None:
            return False
        return str(chat_id) == str(telegram_channel._config.chat_id)

    @staticmethod
    def _add_mini_app_button(keyboard: list[list[dict]], telegram_channel) -> None:
        """Add the Mini App launch action when Telegram has a public URL."""
        builder = getattr(telegram_channel, "mini_app_button", None)
        if not callable(builder):
            return
        button = builder()
        if button:
            keyboard.insert(0, [button])

    def _show_legacy_main_menu(self, chat_id: str, telegram_channel) -> None:
        """Send the ATLAX main interactive menu with inline buttons."""
        if self._is_admin(chat_id):
            db = getattr(self, "_subscriber_db", None) or self._get_subscriber_db()
            self._show_admin_home(chat_id, telegram_channel, db)
            return

        text = "🤖 <b>ATLAX Signal Terminal</b>\n\nYour subscriber menu:"
        keyboard = [
            [
                {"text": "📰 Market & News",     "callback_data": "menu:market"},
                {"text": "⚙️ Preferences",        "callback_data": "menu:settings"},
            ],
            [
                {"text": "💳 Subscribe / Renew",  "callback_data": "menu:subscribe"},
            ]
        ]
        self._add_mini_app_button(keyboard, telegram_channel)
        telegram_channel.send_inline_menu(chat_id, text, keyboard)

    def _display_name(self, chat_id: str, username: str, db) -> str:
        sub = db.get_subscriber(chat_id)
        name = (username or "").strip()
        if not name and sub:
            name = str(sub.get("username", "")).strip()
        return escape(name or "Trader")

    def _show_welcome(self, chat_id: str, telegram_channel, db, username: str = "") -> None:
        """Show the first-run experience for users without active access."""
        name = self._display_name(chat_id, username, db)
        language = "en"
        try:
            language = str(db.get_language(chat_id) or "en").lower()
        except (AttributeError, TypeError):
            try:
                language = str((db.get_settings(chat_id) or {}).get("language", "en")).lower()
            except (AttributeError, TypeError):
                pass
        if language == "sw":
            lines = [
                f"👋 <b>Karibu ATLAX, {name}</b>",
                "",
                "📡 ATLAX ni mfumo wa deterministic wa biashara wa vipindi vingi unaotambua, hupima, na kueleza fursa za soko.",
                "",
                "📚 Unapokea Signal Passport na historia ya ishara zako kwa uwazi.",
                "ATLAX haidhibiti akaunti yako ya broker, wallet, salio, au faida na hasara.",
            ]
        else:
            lines = [
                f"👋 <b>Welcome to ATLAX, {name}</b>",
                "",
                "📡 ATLAX is a deterministic, multi-timeframe trading operating system that detects, scores, and explains market opportunities using strict, rule-based engines.",
                "",
                "📚 Signal Passport and history keep your alerts clear, reviewable, and easy to follow.",
                "ATLAX does not control a subscriber's broker account, wallet, balance, or profit and loss.",
            ]
        if self._payment_cfg is not None and self._payment_cfg.enabled:
            lines.extend([
                f"💳 <b>Plan:</b> ${self._payment_cfg.subscription_price_usd} USD for {self._payment_cfg.subscription_duration_days} days",
                "Choose your preferred payment method from the subscription menu and receive your signals after payment verification.",
            ])
        else:
            lines.append("🔐 Access is available by approved invitation or authorized activation.")
        lines.extend(["", "Choose an action below to explore ATLAX."])
        keyboard = [
            [{"text": "💳 Subscribe", "callback_data": "menu:subscribe"}],
            [{"text": "🧭 How ATLAX Works", "callback_data": "nav:about"}],
            [{"text": "📚 Read FAQ", "callback_data": "nav:faq"}],
            [{"text": "🆘 Help & Support", "callback_data": "nav:help"}],
            [{"text": "🌐 Language", "callback_data": "nav:language"}],
        ]
        self._add_mini_app_button(keyboard, telegram_channel)
        telegram_channel.send_inline_menu(chat_id, "\n".join(lines), keyboard)

    def _show_main_menu(
        self,
        chat_id: str,
        telegram_channel,
        db=None,
        username: str = "",
    ) -> None:
        """Show a state-aware ATLAX terminal home screen."""
        if db is None:
            db = getattr(self, "_subscriber_db", None) or self._get_subscriber_db()

        if not self._is_admin(chat_id) and not db.is_registered(chat_id):
            self._show_welcome(chat_id, telegram_channel, db, username)
            return

        # Removed admin redirect here so admins can see the subscriber menu via "menu:main"

        from core.alerts.subscriber_db import STYLE_LABELS

        name = self._display_name(chat_id, username, db)
        sub = db.get_subscriber(chat_id) or {}
        settings = db.get_settings(chat_id)
        style = STYLE_LABELS.get(str(sub.get("style", "")), str(sub.get("style", "")))
        timeframes = ", ".join(sub.get("timeframes", [])) or "n/a"
        symbols = ", ".join(settings.get("symbols", [])) or "All symbols"
        expiry = sub.get("subscription_expires_at") or "No expiry set"
        text = (
            f"👋 <b>Welcome back, {name}</b>\n\n"
            "🤖 <b>ATLAX Signal Terminal</b>\n"
            f"Access: <b>{style}</b>\n"
            f"Timeframes: <b>{timeframes}</b>\n"
            f"Symbols: <b>{symbols}</b>\n"
            f"Expires: <code>{expiry}</code>\n\n"
            "Select an option below:"
        )
        keyboard = [
            [
                {"text": "⚡ Signals", "callback_data": "nav:signals"},
                {"text": "📊 Market Context", "callback_data": "menu:market"},
            ],
            [
                {"text": "📖 History", "callback_data": "nav:history"},
                {"text": "🧠 Ask AI", "callback_data": "menu:subscriber_ai"},
            ],
            [{"text": "👤 Profile", "callback_data": "nav:profile"}],
            [
                {"text": "💳 Subscription", "callback_data": "menu:subscribe"},
                {"text": "⚙️ Settings", "callback_data": "menu:settings"},
            ],
            [
                {"text": "❓ Help & FAQ", "callback_data": "nav:help"},
            ],
        ]
        if self._is_admin(chat_id):
            keyboard.insert(0, [{"text": "👑 Admin Control Panel", "callback_data": "admin:home"}])

        self._add_mini_app_button(keyboard, telegram_channel)
        telegram_channel.send_inline_menu(chat_id, text, keyboard)

    def _show_user_profile(self, chat_id: str, telegram_channel, db, message_id: Optional[int] = None) -> None:
        from core.alerts.subscriber_db import STYLE_LABELS

        sub = db.get_subscriber(chat_id)
        if not sub or not db.is_registered(chat_id):
            text = "👤 <b>ATLAX Profile</b>\n\nNo active access is attached to this account yet."
            keyboard = [
                [{"text": "🚀 Start Subscription", "callback_data": "menu:subscribe"}],
                [{"text": "🏠 Home", "callback_data": "menu:main"}],
            ]
        else:
            settings = db.get_settings(chat_id)
            username = sub.get("username") or "UNKNOWN"
            style = STYLE_LABELS.get(str(sub.get("style", "")), str(sub.get("style", "")))
            timeframes = ", ".join(sub.get("timeframes", [])) or "n/a"
            symbols = ", ".join(settings.get("symbols", [])) or "All symbols"
            confidence = settings.get("min_confidence")
            confidence_label = f"{confidence}%" if confidence is not None else "Profile default"
            notifications = {
                "all": "All updates",
                "signals_only": "Signals only",
                "minimal": "Minimal",
            }.get(settings.get("notifications", "all"), str(settings.get("notifications", "all")))
            source = sub.get("payment_method") or sub.get("subscription_source") or "invite code"
            expiry = sub.get("subscription_expires_at") or "No expiry set"
            updated_at = sub.get("updated_at") or "UNKNOWN"
            text = (
                "👤 <b>My ATLAX Profile</b>\n\n"
                f"Display name: <b>{escape(str(username))}</b>\n"
                f"Telegram ID: <code>{escape(str(chat_id))}</code>\n"
                f"Style: <b>{escape(style)}</b>\n"
                f"Timeframes: <b>{escape(timeframes)}</b>\n"
                f"Symbols: <b>{escape(symbols)}</b>\n"
                f"Min confidence: <b>{escape(str(confidence_label))}</b>\n"
                f"Notifications: <b>{escape(notifications)}</b>\n"
                f"Access source: <b>{escape(str(source))}</b>\n"
                f"Expires: <code>{escape(str(expiry))}</code>\n"
                f"Last updated: <code>{escape(str(updated_at))}</code>"
            )
            keyboard = [
                [{"text": "⚙️ Edit Preferences", "callback_data": "menu:settings"}],
                [{"text": "💳 Subscription", "callback_data": "menu:subscribe"}],
                [{"text": "📡 Signal Terminal", "callback_data": "nav:signals"}],
                [{"text": "🏠 Home", "callback_data": "menu:main"}],
            ]

        if message_id is None:
            telegram_channel.send_inline_menu(chat_id, text, keyboard)
        else:
            telegram_channel.edit_inline_menu(chat_id, message_id, text, keyboard)

    def _show_subscriber_ai_menu(self, chat_id: str, telegram_channel, db, message_id: Optional[int] = None) -> None:
        """Show subscriber-safe AI questions and explain the advisory boundary."""
        if not db.is_registered(chat_id):
            self._show_welcome(chat_id, telegram_channel, db)
            return
        text = (
            "🤖 <b>ATLAX AI for Subscribers</b>\n\n"
            "Ask about ATLAX signals, market context, news, heatmap, pair correlation, "
            "or trading concepts.\n\n"
            "Use <code>/ask your question</code> for anything else.\n\n"
            "AI is advisory only. It cannot access or manage your trading account."
        )
        keyboard = [
            [
                {"text": "📰 News", "callback_data": "subai:news"},
                {"text": "🔥 Heatmap", "callback_data": "subai:heatmap"},
            ],
            [
                {"text": "🔗 Correlation", "callback_data": "subai:correlation"},
                {"text": "📊 ATLAX Results", "callback_data": "subai:performance"},
            ],
            [{"text": "📘 Explain ATLAX", "callback_data": "subai:explain"}],
            [
                {"text": "🏠 Home", "callback_data": "menu:main"},
                {"text": "❓ Help", "callback_data": "nav:help"},
            ],
        ]
        if message_id is None:
            telegram_channel.send_inline_menu(chat_id, text, keyboard)
        else:
            telegram_channel.edit_inline_menu(chat_id, message_id, text, keyboard)

    def _show_signal_passport_history(
        self,
        chat_id: str,
        telegram_channel,
        db,
        message_id: Optional[int] = None,
    ) -> None:
        """Show recent public Passport records to an active subscriber or admin."""
        if not self._is_admin(chat_id) and (db is None or not db.is_registered(chat_id)):
            self._show_welcome(chat_id, telegram_channel, db)
            return
        db_path = getattr(self._analytics_cfg, "db_path", "data/journal.sqlite")
        records = get_signal_passport_history(db_path, days=30, limit=10)
        text = render_signal_passport_history(records)
        keyboard = [
            [{"text": "\U0001F4CA Weekly Report", "callback_data": "nav:weekly"}],
            [
                {"text": "\U0001F504 Refresh", "callback_data": "nav:history"},
                {"text": "\U0001F3E0 Home", "callback_data": "menu:main"},
            ],
        ]
        if message_id is None:
            telegram_channel.send_inline_menu(chat_id, text, keyboard)
        else:
            telegram_channel.edit_inline_menu(chat_id, message_id, text, keyboard)
        logger.info(
            '{"event":"telegram_signal_passport_history_viewed","chat_id":"%s"}',
            str(chat_id)[:80],
        )

    def _show_signal_passport_weekly(
        self,
        chat_id: str,
        telegram_channel,
        db,
        message_id: Optional[int] = None,
    ) -> None:
        """Show the deterministic seven-day public Passport aggregate."""
        if not self._is_admin(chat_id) and (db is None or not db.is_registered(chat_id)):
            self._show_welcome(chat_id, telegram_channel, db)
            return
        db_path = getattr(self._analytics_cfg, "db_path", "data/journal.sqlite")
        performance = get_signal_passport_performance(db_path, days=7)
        text = render_signal_passport_weekly(performance)
        keyboard = [
            [{"text": "\U0001F4D8 Recent History", "callback_data": "nav:history"}],
            [{"text": "\U0001F3E0 Home", "callback_data": "menu:main"}],
        ]
        if message_id is None:
            telegram_channel.send_inline_menu(chat_id, text, keyboard)
        else:
            telegram_channel.edit_inline_menu(chat_id, message_id, text, keyboard)
        logger.info(
            '{"event":"telegram_signal_passport_weekly_viewed","chat_id":"%s"}',
            str(chat_id)[:80],
        )

    def _handle_subscriber_ai_callback(self, data: str, chat_id: str, message_id: int, telegram_channel, db) -> None:
        """Answer a fixed subscriber AI prompt through the guarded advisory engine."""
        if not db.is_registered(chat_id):
            self._show_welcome(chat_id, telegram_channel, db)
            return
        settings = db.get_settings(chat_id) or {}
        symbols = [str(item).strip().upper() for item in settings.get("symbols", []) if str(item).strip()]
        if not symbols:
            symbols = sorted({instrument.symbol.upper() for instrument in self._market_cfg.instruments})
        question_map = {
            "subai:news": "What high-impact economic news is scheduled in the next 24 hours, and which currencies does it affect?",
            "subai:heatmap": "What does the current ATLAX market heatmap show? Explain the strongest and weakest configured pair context without making a trade recommendation.",
            "subai:performance": "How did ATLAX signals perform today and over the last 30 days? Report counts, wins, losses, open signals, and sample sizes only.",
            "subai:explain": "Explain how ATLAX signal styles, timeframes, signal routing, and advisory AI work for a subscriber.",
        }
        if data == "subai:correlation" and len(symbols) >= 2:
            question_map[data] = f"What is the current H1 price-return correlation between {symbols[0]} and {symbols[1]}? Explain what the result means without recommending a trade."
        elif data == "subai:correlation":
            question_map[data] = "Explain pair correlation and tell me what data ATLAX needs before it can calculate it."
        question = question_map.get(data)
        if question is None:
            self._show_subscriber_ai_menu(chat_id, telegram_channel, db, message_id)
            return

        back_btn = [[
            {"text": "↩️ Back to AI", "callback_data": "menu:subscriber_ai"},
            {"text": "🏠 Home", "callback_data": "menu:main"},
        ]]
        thinking_message_id = self._send_one_time_telegram_prompt(telegram_channel, chat_id, "Thinking...")
        if self._advisory_engine is None:
            text = "⚠️ Subscriber AI is not configured."
        else:
            try:
                text = self._advisory_engine.answer_subscriber_question(question, chat_id)
            except Exception as exc:
                logger.error('{"event":"subscriber_ai_callback_error","error":"%s"}', str(exc)[:160])
                text = "⚠️ ATLAX AI could not answer right now. Please try again later."
        self._delete_one_time_telegram_message(telegram_channel, chat_id, thinking_message_id)
        telegram_channel.edit_inline_menu(chat_id, message_id, text, back_btn)

    def _show_legacy_signal_terminal(self, chat_id: str, telegram_channel, db, message_id: Optional[int] = None) -> None:
        if not db.is_registered(chat_id):
            self._show_welcome(chat_id, telegram_channel, db)
            return

        predictions = getattr(getattr(self._alert_engine, "tracker", None), "predictions", {}) or {}
        visible = []
        for alert_id, record in predictions.items():
            deliveries = record.get("subscriber_deliveries", []) or []
            delivered_to_user = any(str(item.get("chat_id", "")) == str(chat_id) for item in deliveries)
            if str(record.get("chat_id", "")) == str(chat_id) or delivered_to_user:
                visible.append((alert_id, record))
        visible.sort(key=lambda item: float(item[1].get("timestamp", 0.0)), reverse=True)

        lines = [
            "📡 <b>Signal Terminal</b>",
            "",
            f"Active monitored signals: <b>{len(visible)}</b>",
            "",
        ]
        for alert_id, record in visible[:5]:
            timestamp = record.get("timestamp")
            try:
                when = datetime.fromtimestamp(float(timestamp), timezone.utc).strftime("%H:%M UTC")
            except (TypeError, ValueError, OSError):
                when = "unknown time"
            lines.extend([
                f"<b>{escape(str(record.get('symbol', 'UNKNOWN')))}</b> | <b>{escape(str(record.get('direction', 'UNKNOWN')))}</b> | {escape(when)}",
                f"Target: <code>{escape(str(record.get('target', 'n/a')))}</code>  Midpoint: <code>{escape(str(record.get('midpoint', 'n/a')))}</code>",
                f"Invalidation: <code>{escape(str(record.get('sl', 'n/a')))}</code>",
                f"ID: <code>{escape(str(alert_id)[:12])}</code>",
                "",
            ])
        if not visible:
            lines.append("No active monitored signals are attached to your account right now.")
            lines.append("New eligible alerts will arrive here automatically.")
        lines.extend(["", "Signal information is advisory and comes from stored ATLAX alert records."])
        keyboard = [
            [{"text": "🔄 Refresh", "callback_data": "nav:signals"}],
            [{"text": "👤 My Profile", "callback_data": "nav:profile"}],
            [{"text": "🏠 Home", "callback_data": "menu:main"}],
        ]
        text = "\n".join(lines)
        if message_id is None:
            telegram_channel.send_inline_menu(chat_id, text, keyboard)
        else:
            telegram_channel.edit_inline_menu(chat_id, message_id, text, keyboard)

    def _visible_signal_records(self, chat_id: str) -> list[tuple[str, dict]]:
        """Return only active signal records delivered to this Telegram user."""
        predictions = getattr(getattr(self._alert_engine, "tracker", None), "predictions", {}) or {}
        visible = []
        for alert_id, record in predictions.items():
            if not isinstance(record, dict):
                continue
            deliveries = record.get("subscriber_deliveries", []) or []
            delivered_to_user = any(
                str(item.get("chat_id", "")) == str(chat_id)
                for item in deliveries
                if isinstance(item, dict)
            )
            if str(record.get("chat_id", "")) == str(chat_id) or delivered_to_user:
                visible.append((str(alert_id), record))

        def _timestamp(item: tuple[str, dict]) -> float:
            try:
                return float(item[1].get("timestamp", 0.0))
            except (TypeError, ValueError):
                return 0.0

        return sorted(visible, key=_timestamp, reverse=True)

    def _signal_journal_entries(self) -> dict[str, object]:
        """Read signal metadata already written by AlertEngine, without creating data."""
        journal = getattr(self._alert_engine, "_journal", None)
        if journal is None:
            return {}
        try:
            entries = journal.get_entries()
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                '{"event":"telegram_signal_journal_read_failed","error":"%s"}',
                str(exc)[:160],
            )
            return {}
        return {
            str(getattr(entry, "record_id", "")): entry
            for entry in entries
            if getattr(entry, "record_id", None)
        }

    def _signal_filter_state(self, chat_id: str) -> dict[str, str]:
        states = getattr(self, "_telegram_signal_filters", {})
        state = states.get(str(chat_id), {})
        return {
            "symbol": str(state.get("symbol", "all")).upper(),
            "timeframe": str(state.get("timeframe", "all")).upper(),
            "confidence": str(state.get("confidence", "any")).lower(),
        }

    def _set_signal_filter_state(self, chat_id: str, kind: str, value: str) -> None:
        states = getattr(self, "_telegram_signal_filters", {})
        state = dict(states.get(str(chat_id), {}))
        state[kind] = value
        states[str(chat_id)] = state
        self._telegram_signal_filters = states

    def _clear_signal_filter_state(self, chat_id: str) -> None:
        states = getattr(self, "_telegram_signal_filters", {})
        states.pop(str(chat_id), None)
        self._telegram_signal_filters = states

    @staticmethod
    def _button_rows(buttons: list[dict], width: int) -> list[list[dict]]:
        return [buttons[index:index + width] for index in range(0, len(buttons), width)]

    def _available_symbol_options(self, chat_id: str = "", db=None) -> list[str]:
        """Return safe pair choices from market configuration and known records."""
        symbols: list[str] = []

        def add(raw_value) -> None:
            value = str(raw_value or "").strip().upper()
            if (
                not value
                or len(value) > 20
                or any(char not in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-" for char in value)
            ):
                return
            if value not in symbols:
                symbols.append(value)

        for instrument in getattr(getattr(self, "_market_cfg", None), "instruments", []) or []:
            add(getattr(instrument, "symbol", ""))
        if db is not None and chat_id:
            try:
                for value in (db.get_settings(chat_id) or {}).get("symbols", []) or []:
                    add(value)
            except Exception:
                pass
        if chat_id:
            for _, record in self._visible_signal_records(chat_id):
                add(record.get("symbol"))
        return symbols

    def _style_choice_keyboard(self, current_style: str = "", back_callback: str = "menu:main") -> list[list[dict]]:
        choices = [
            ("scalper", "\U0001F3C3 Scalper", "M5", "set:style_scalper"),
            ("day_trader", "\U0001F4C5 Day Trader", "M15/H1", "set:style_day"),
            ("swing_trader", "\U0001F30A Swing Trader", "H4/D1", "set:style_swing"),
            ("all_styles", "\U0001F5C2 All Styles", "M5/M15/H1/H4/D1", "set:style_all"),
        ]
        keyboard = []
        for style, label, timeframes, callback in choices:
            tick = " \U00002705" if current_style == style else ""
            keyboard.append([{"text": f"{label} ({timeframes}){tick}", "callback_data": callback}])
        keyboard.append([{"text": "\U0001F3E0 Home", "callback_data": back_callback}])
        return keyboard

    def _show_style_choice_menu(
        self,
        chat_id: str,
        telegram_channel,
        db,
        message_id: Optional[int] = None,
        back_callback: str = "menu:main",
    ) -> None:
        sub = db.get_subscriber(chat_id) or {}
        text = (
            "\U0001F3AF <b>Choose your trading style</b>\n\n"
            "This controls the timeframes routed to your Telegram account.\n"
            "You can change it anytime from Preferences."
        )
        keyboard = self._style_choice_keyboard(str(sub.get("style", "")), back_callback)
        if message_id is None:
            telegram_channel.send_inline_menu(chat_id, text, keyboard)
        else:
            telegram_channel.edit_inline_menu(chat_id, message_id, text, keyboard)

    def _show_pair_routing_menu(
        self,
        chat_id: str,
        telegram_channel,
        db,
        *,
        message_id: Optional[int] = None,
        page: int = 0,
        callback_prefix: str = "signals:pairs",
        done_callback: str = "signals:refresh",
        back_callback: str = "menu:settings",
        title: str = "Pair Routing",
    ) -> None:
        settings = db.get_settings(chat_id) or {}
        selected = []
        for raw_value in settings.get("symbols", []) or []:
            value = str(raw_value or "").strip().upper()
            if value and value not in selected:
                selected.append(value)
        options = self._available_symbol_options(chat_id, db)
        for value in selected:
            if value not in options:
                options.append(value)

        page_size = 18
        total_pages = max(1, (len(options) + page_size - 1) // page_size)
        page = max(0, min(int(page), total_pages - 1))
        page_options = options[page * page_size:(page + 1) * page_size]
        current = ", ".join(selected) if selected else "All configured pairs"
        text = (
            f"\U0001F4B1 <b>{escape(title)}</b>\n\n"
            f"Receiving: <b>{escape(current)}</b>\n\n"
            "Choose one or more pairs. An empty selection means all configured pairs."
        )
        keyboard = [[{
            "text": f"\U0001F310 All Pairs{' \U00002705' if not selected else ''}",
            "callback_data": f"{callback_prefix}:all",
        }]]
        pair_buttons = []
        for symbol in page_options:
            tick = "\U00002705 " if symbol in selected else ""
            pair_buttons.append({
                "text": f"{tick}{symbol}",
                "callback_data": f"{callback_prefix}:toggle:{symbol}",
            })
        keyboard.extend(self._button_rows(pair_buttons, 3))
        if total_pages > 1:
            navigation = []
            if page > 0:
                navigation.append({"text": "\U00002B05 Previous", "callback_data": f"{callback_prefix}:page:{page - 1}"})
            navigation.append({"text": f"Page {page + 1}/{total_pages}", "callback_data": f"{callback_prefix}:page:{page}"})
            if page < total_pages - 1:
                navigation.append({"text": "Next \U000027A1", "callback_data": f"{callback_prefix}:page:{page + 1}"})
            keyboard.append(navigation)
        keyboard.append([
            {"text": "\U00002705 Done", "callback_data": done_callback},
            {"text": "\U00002B05 Back", "callback_data": back_callback},
        ])
        if message_id is None:
            telegram_channel.send_inline_menu(chat_id, text, keyboard)
        else:
            telegram_channel.edit_inline_menu(chat_id, message_id, text, keyboard)

    def _signal_filter_options(
        self,
        chat_id: str,
        db,
        visible: list[tuple[str, dict]],
        journal_entries: dict[str, object],
    ) -> dict[str, list[str]]:
        """Build filter choices only from stored subscriber/configuration values."""
        sub = db.get_subscriber(chat_id) or {}
        settings = db.get_settings(chat_id) or {}

        def _tokens(values) -> list[str]:
            result = []
            for raw_value in values:
                value = str(raw_value or "").strip().upper()
                if not value or len(value) > 20 or not value.isalnum() or value in result:
                    continue
                result.append(value)
            return result

        symbols = self._available_symbol_options(chat_id, db)
        symbols.extend(
            value for value in _tokens(record.get("symbol") for _, record in visible)
            if value not in symbols
        )

        timeframes = _tokens(sub.get("timeframes", []))
        timeframes.extend(
            value
            for value in _tokens(
                getattr(journal_entries.get(alert_id), "timeframe", "")
                for alert_id, _ in visible
            )
            if value not in timeframes
        )

        confidence_values = set()
        configured_min = settings.get("min_confidence")
        try:
            if configured_min is not None and 0 <= int(configured_min) <= 100:
                confidence_values.add(str(int(configured_min)))
        except (TypeError, ValueError):
            pass
        for profile in getattr(getattr(self, "_strategy_cfg", None), "active_profiles", []) or []:
            try:
                threshold = int(getattr(profile, "min_confidence_threshold"))
            except (TypeError, ValueError, AttributeError):
                continue
            if 0 <= threshold <= 100:
                confidence_values.add(str(threshold))

        return {
            "symbols": symbols,
            "timeframes": timeframes[:6],
            "confidence": sorted(confidence_values, key=int),
        }

    def _show_signal_terminal(self, chat_id: str, telegram_channel, db, message_id: Optional[int] = None) -> None:
        if not db.is_registered(chat_id):
            self._show_welcome(chat_id, telegram_channel, db)
            return

        visible = self._visible_signal_records(chat_id)
        journal_entries = self._signal_journal_entries()
        options = self._signal_filter_options(chat_id, db, visible, journal_entries)
        filters = self._signal_filter_state(chat_id)
        if filters["symbol"] != "all" and filters["symbol"] not in options["symbols"]:
            filters["symbol"] = "all"
        if filters["timeframe"] != "all" and filters["timeframe"] not in options["timeframes"]:
            filters["timeframe"] = "all"
        if filters["confidence"] != "any" and filters["confidence"] not in options["confidence"]:
            filters["confidence"] = "any"

        filtered = []
        for alert_id, record in visible:
            entry = journal_entries.get(alert_id)
            symbol = str(record.get("symbol", "UNKNOWN")).upper()
            timeframe = str(getattr(entry, "timeframe", "UNKNOWN")).upper()
            try:
                score = float(getattr(entry, "confidence_score")) if entry is not None else None
            except (TypeError, ValueError, AttributeError):
                score = None
            if filters["symbol"] != "all" and symbol != filters["symbol"]:
                continue
            if filters["timeframe"] != "all" and timeframe != filters["timeframe"]:
                continue
            if filters["confidence"] != "any" and (score is None or score < int(filters["confidence"])):
                continue
            filtered.append((alert_id, record, entry, score))

        lines = [
            "<b>Signal Terminal</b>",
            "",
            f"Recent active signals: <b>{len(filtered)}</b> of {len(visible)}",
            f"Pair: <b>{escape(filters['symbol'])}</b> | Timeframe: <b>{escape(filters['timeframe'])}</b>",
            f"Confidence: <b>{escape(filters['confidence'])}</b>",
            "",
        ]
        for alert_id, record, entry, score in filtered[:5]:
            timestamp = record.get("timestamp")
            try:
                when = datetime.fromtimestamp(float(timestamp), timezone.utc).strftime("%H:%M UTC")
                recent_label = "Recent" if time.time() - float(timestamp) <= 86400 else "Stored"
            except (TypeError, ValueError, OSError):
                when = "UNKNOWN"
                recent_label = "Stored"
            timeframe = getattr(entry, "timeframe", "UNKNOWN") if entry is not None else "UNKNOWN"
            confidence_label = f"{score:.1f}" if score is not None else "UNKNOWN"
            lines.extend([
                f"[{recent_label}] <b>{escape(str(record.get('symbol', 'UNKNOWN')))}</b> | <b>{escape(str(record.get('direction', 'UNKNOWN')))}</b> | {escape(when)}",
                f"Timeframe: <b>{escape(str(timeframe))}</b> | Confidence: <b>{escape(confidence_label)}</b>",
                f"Target: <code>{escape(str(record.get('target', 'UNKNOWN')))}</code> | Midpoint: <code>{escape(str(record.get('midpoint', 'UNKNOWN')))}</code>",
                f"Invalidation: <code>{escape(str(record.get('sl', 'UNKNOWN')))}</code>",
                "",
            ])
        if not filtered:
            if visible:
                lines.append("No stored signals match the current filters.")
            else:
                lines.append("No active monitored signals are attached to your account right now.")
            lines.append("New eligible alerts will arrive here automatically.")
        lines.extend(["", "Signal information is advisory and comes from stored ATLAX alert records."])

        timeframe_label = "All TF" if filters["timeframe"] == "all" else filters["timeframe"]
        confidence_label = "Any score" if filters["confidence"] == "any" else f"{filters['confidence']}%+"
        routing_symbols = (db.get_settings(chat_id) or {}).get("symbols", []) or []
        routing_label = ", ".join(str(value).upper() for value in routing_symbols) if routing_symbols else "All"

        keyboard = [
            [
                {"text": f"\U0001F4B1 Pairs: {routing_label}", "callback_data": "signals:pairs"},
                {"text": f"\U000023F1 {timeframe_label}", "callback_data": "signals:filter:timeframe:all"},
            ],
            [
                {"text": f"\U0001F3AF {confidence_label}", "callback_data": "signals:filter:confidence:any"},
                {"text": "\U0001F9F9 Clear", "callback_data": "signals:clear"},
            ],
        ]

        timeframe_buttons = [
            {"text": f"\U000023F1 {timeframe}", "callback_data": f"signals:filter:timeframe:{timeframe}"}
            for timeframe in options["timeframes"]
        ]
        for index in range(0, len(timeframe_buttons), 3):
            keyboard.append(timeframe_buttons[index:index + 3])

        confidence_buttons = [
            {"text": f"\U0001F3AF {confidence}%+", "callback_data": f"signals:filter:confidence:{confidence}"}
            for confidence in options["confidence"]
        ]
        for index in range(0, len(confidence_buttons), 3):
            keyboard.append(confidence_buttons[index:index + 3])

        detail_buttons = []
        for alert_id, record, _, _ in filtered[:5]:
            if len(alert_id) <= 45 and all(char.isalnum() or char in "-_" for char in alert_id):
                symbol = str(record.get("symbol", "UNKNOWN")).upper()
                direction = str(record.get("direction", "UNKNOWN")).upper()
                direction_emoji = "\U0001F7E2" if direction == "BUY" else "\U0001F534" if direction == "SELL" else "\U0001F4CC"
                detail_buttons.append({
                    "text": f"{direction_emoji} {symbol} {direction}",
                    "callback_data": f"signals:detail:{alert_id}",
                })
        for index in range(0, len(detail_buttons), 2):
            keyboard.append(detail_buttons[index:index + 2])

        keyboard.append([
            {"text": "\U0001F504 Refresh", "callback_data": "signals:refresh"},
            {"text": "\U0001F464 Profile", "callback_data": "nav:profile"},
            {"text": "\U0001F3E0 Home", "callback_data": "menu:main"},
        ])
        text = "\n".join(lines)
        if message_id is None:
            telegram_channel.send_inline_menu(chat_id, text, keyboard)
        else:
            telegram_channel.edit_inline_menu(chat_id, message_id, text, keyboard)

    def _show_signal_detail(self, chat_id: str, telegram_channel, db, alert_id: str, message_id: Optional[int] = None) -> None:
        """Render a stored signal only after re-checking delivery ownership."""
        if not db.is_registered(chat_id):
            self._show_welcome(chat_id, telegram_channel, db)
            return

        visible = dict(self._visible_signal_records(chat_id))
        record = visible.get(str(alert_id))
        keyboard = [
            [{"text": "↩️ Back to Signal Terminal", "callback_data": "signals:refresh"}],
            [{"text": "🏠 Home", "callback_data": "menu:main"}],
        ]
        if record is None:
            logger.info(
                '{"event":"telegram_signal_detail_denied","chat_id":"%s","alert_id":"%s"}',
                str(chat_id)[:80], str(alert_id)[:80],
            )
            text = "<b>Signal Detail</b>\n\nThat signal is not available for this account."
        else:
            entry = self._signal_journal_entries().get(str(alert_id))
            timestamp = record.get("timestamp")
            try:
                when = datetime.fromtimestamp(float(timestamp), timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
            except (TypeError, ValueError, OSError):
                when = "UNKNOWN"
            if record.get("target_hit"):
                status = "Target reached"
            elif record.get("sl_hit"):
                status = "Invalidation reached"
            elif record.get("midpoint_hit"):
                status = "Midpoint reached; monitoring target"
            else:
                status = "Active monitoring"

            timeframe = getattr(entry, "timeframe", "UNKNOWN") if entry is not None else "UNKNOWN"
            profile = getattr(entry, "profile", "UNKNOWN") if entry is not None else "UNKNOWN"
            confidence = getattr(entry, "confidence_score", "UNKNOWN") if entry is not None else "UNKNOWN"
            entry_price = getattr(entry, "entry_price", "UNKNOWN") if entry is not None else "UNKNOWN"
            target = getattr(entry, "tp_price", record.get("target", "UNKNOWN")) if entry is not None else record.get("target", "UNKNOWN")
            invalidation = getattr(entry, "sl_price", record.get("sl", "UNKNOWN")) if entry is not None else record.get("sl", "UNKNOWN")
            text = (
                "<b>Signal Detail</b>\n\n"
                f"Symbol: <b>{escape(str(record.get('symbol', 'UNKNOWN')))}</b>\n"
                f"Direction: <b>{escape(str(record.get('direction', 'UNKNOWN')))}</b>\n"
                f"Timeframe: <b>{escape(str(timeframe))}</b>\n"
                f"Profile: <b>{escape(str(profile))}</b>\n"
                f"Confidence: <b>{escape(str(confidence))}</b>\n"
                f"Status: <b>{escape(status)}</b>\n"
                f"Created: <code>{escape(when)}</code>\n\n"
                f"Entry zone reference: <code>{escape(str(entry_price))}</code>\n"
                f"Midpoint: <code>{escape(str(record.get('midpoint', 'UNKNOWN')))}</code>\n"
                f"Target: <code>{escape(str(target))}</code>\n"
                f"Invalidation: <code>{escape(str(invalidation))}</code>\n\n"
                f"Alert ID: <code>{escape(str(alert_id))}</code>\n"
                "\nThis is stored alert information for your account. It is advisory only."
            )
            logger.info(
                '{"event":"telegram_signal_detail_viewed","chat_id":"%s","alert_id":"%s"}',
                str(chat_id)[:80], str(alert_id)[:80],
            )

        if message_id is None:
            telegram_channel.send_inline_menu(chat_id, text, keyboard)
        else:
            telegram_channel.edit_inline_menu(chat_id, message_id, text, keyboard)

    def _public_support_contact(self) -> str:
        configured = str(getattr(self._payment_cfg, "support_contact", "") or "").strip()
        return configured or "@ocrooxx"

    def _show_faq_menu(
        self,
        chat_id: str,
        telegram_channel,
        message_id: Optional[int] = None,
        page: int = 0,
    ) -> None:
        from core.analytics.faq_knowledge import load_public_faq, render_faq_page

        faq_path = getattr(self._analytics_cfg, "faq_path", "docs/20_PUBLIC_FAQ.md")
        faq_text, has_previous, has_next = render_faq_page(
            load_public_faq(faq_path), page=page, page_size=10
        )
        text = f"📚 <b>ATLAX Frequently Asked Questions</b>\n\n{faq_text}"
        keyboard = [
            [{"text": "📚 Public FAQ", "callback_data": "nav:faq"}],
        ]
        if has_previous or has_next:
            navigation = []
            if has_previous:
                navigation.append({"text": "⬅️ Previous", "callback_data": f"nav:faq:page:{max(0, page - 1)}"})
            if has_next:
                navigation.append({"text": "Next ➡️", "callback_data": f"nav:faq:page:{page + 1}"})
            keyboard.append(navigation)
        keyboard.extend([
            [{"text": "💳 Subscription", "callback_data": "menu:subscribe"}],
            [{"text": "🤖 Ask ATLAX AI", "callback_data": "menu:subscriber_ai"}],
            [{"text": "🆘 Support", "url": "https://t.me/ocrooxx"}],
            [{"text": "🏠 Home", "callback_data": "menu:main"}],
        ])
        if message_id is None:
            telegram_channel.send_inline_menu(chat_id, text, keyboard)
        else:
            telegram_channel.edit_inline_menu(chat_id, message_id, text, keyboard)

    def _show_help_menu(self, chat_id: str, telegram_channel, message_id: Optional[int] = None) -> None:
        support = escape(self._public_support_contact())
        text = (
            "❓ <b>ATLAX Help & Support</b>\n\n"
            "Use the buttons below to move around ATLAX, or use one of these commands:\n\n"
            "<code>/history</code> - Review recent Signal Passport records\n"
            "<code>/weekly</code> - View the seven-day signal report\n"
            "📡 <code>/signals</code> — Open your signal terminal\n"
            "👤 <code>/profile</code> — Review your subscriber profile\n"
            "💳 <code>/subscribe</code> — Start or renew access\n"
            "🎯 <code>/style</code> — Choose Scalper, Day Trader, Swing Trader, or All Styles\n"
            "🤖 <code>/ai</code> — Open subscriber AI\n"
            "📚 <code>/faq</code> — Read common questions and answers\n"
            "⚙️ <code>/settings</code> — Change pairs and notifications\n"
            "🔐 <code>/status</code> — Check access and payment status\n\n"
            f"🆘 <b>Support:</b> <code>{support}</code>\n"
            "For payment or access questions, include your ATLAX reference. Never send a PIN, password, seed phrase, or private key."
        )
        keyboard = [
            [{"text": "📡 Signal Terminal", "callback_data": "nav:signals"}],
            [
                {"text": "📚 FAQ", "callback_data": "nav:faq"},
                {"text": "💳 Subscription", "callback_data": "menu:subscribe"},
            ],
            [{"text": "🆘 Support", "url": "https://t.me/ocrooxx"}],
            [{"text": "🏠 Home", "callback_data": "menu:main"}],
        ]
        if message_id is None:
            telegram_channel.send_inline_menu(chat_id, text, keyboard)
        else:
            telegram_channel.edit_inline_menu(chat_id, message_id, text, keyboard)

    def _show_about_menu(self, chat_id: str, telegram_channel, message_id: Optional[int] = None) -> None:
        text = (
            "🧭 <b>How ATLAX Works</b>\n\n"
            "ATLAX watches configured markets, evaluates structured market evidence, and sends clear, profile-aware alerts to eligible Telegram subscribers. Each alert is designed to be understandable, reviewable, and connected to the style and pairs you selected.\n\n"
            "🎯 <b>Your style controls delivery:</b> Scalper, Day Trader, Swing Trader, or All Styles.\n"
            "🧩 <b>Your preferences control focus:</b> selected pairs, notifications, and the level of detail you receive.\n"
            "🤖 <b>Subscriber AI adds context:</b> it can explain public ATLAX information, news, heatmap context, correlation, and trading concepts.\n\n"
            "ATLAX is advisory information, not a promise of profit. Review every alert with your own plan and risk limits."
        )
        keyboard = [
            [{"text": "🚀 Start Subscription", "callback_data": "menu:subscribe"}],
            [
                {"text": "📚 Read FAQ", "callback_data": "nav:faq"},
                {"text": "🆘 Help & Support", "callback_data": "nav:help"},
            ],
            [{"text": "🏠 Home", "callback_data": "menu:main"}],
        ]
        if message_id is None:
            telegram_channel.send_inline_menu(chat_id, text, keyboard)
        else:
            telegram_channel.edit_inline_menu(chat_id, message_id, text, keyboard)

    def _ensure_payment_service(self, db):
        if self._payment_cfg is None or not self._payment_cfg.enabled:
            return None
        if self._payment_service is None:
            self._payment_service = PaymentService(self._payment_cfg, subscriber_db=db)
        else:
            self._payment_service.attach_subscriber_db(db)
        if self._subscription_lifecycle is None:
            self._subscription_lifecycle = SubscriptionLifecycleService(
                self._payment_service,
                db,
                self._admin_audit,
            )
        else:
            self._subscription_lifecycle.attach_subscriber_db(db)
        return self._payment_service

    def _run_subscription_lifecycle(self) -> None:
        """Maintain access and payment state independently of market signals."""
        if self._payment_cfg is None or not self._payment_cfg.enabled:
            return
        db = self._get_subscriber_db()
        payment_service = self._ensure_payment_service(db)
        if payment_service is None or self._subscription_lifecycle is None:
            return
        telegram_channel = next(
            (channel for channel in self._alert_engine._channels if channel.name == "telegram"),
            None,
        )
        try:
            result = self._subscription_lifecycle.run(telegram_channel)
            if any(result.values()):
                logger.info(
                    '{"event":"subscription_lifecycle_run","expired_payment_intents":%d,'
                    '"suspended_subscriptions":%d,"expiry_notices_sent":%d,'
                    '"expiry_reminders_sent":%d,"delivery_failures":%d}',
                    result["expired_payment_intents"],
                    result["suspended_subscriptions"],
                    result["expiry_notices_sent"],
                    result["expiry_reminders_sent"],
                    result["delivery_failures"],
                )
        except Exception as exc:  # noqa: BLE001
            logger.error(
                '{"event":"subscription_lifecycle_failed","error":"%s"}',
                str(exc)[:200],
            )

    def _record_operational_audit(
        self,
        actor_chat_id: str,
        action: str,
        *,
        target_chat_id: str = "",
        result: str = "success",
        metadata: Optional[dict] = None,
    ) -> None:
        audit = getattr(self, "_admin_audit", None)
        if audit is None:
            audit = self._admin_audit
        audit.record(
            actor_chat_id,
            action,
            target_chat_id=target_chat_id,
            result=result,
            metadata=metadata,
        )

    def _admin_target_id(self, raw_value: str) -> Optional[str]:
        value = str(raw_value or "").strip()
        if not value or len(value) > 32 or not value.lstrip("-").isdigit():
            return None
        return value

    def _show_admin_home(self, chat_id: str, telegram_channel, db, message_id: Optional[int] = None) -> None:
        if not self._is_admin(chat_id):
            self._record_operational_audit(chat_id, "admin_console_view", result="denied")
            telegram_channel.send_reply(chat_id, "That console is available to the admin only.")
            return
        payment_service = self._ensure_payment_service(db)
        pending = payment_service.pending_count() if payment_service else 0
        active_subscribers = db.total_active()
        active_predictions = len(getattr(getattr(self._alert_engine, "tracker", None), "predictions", {}) or {})
        state = "paused" if self._is_paused else "running"
        pending_tag = f" ({pending})" if pending > 0 else ""
        text = (
            "🤖 <b>ATLAX Admin Menu</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"⚡ Scanner: <b>{escape(state)}</b>  •  🔄 Cycle <b>#{self._cycle_count}</b>\n\n"
            f"👥 Subscribers: <b>{active_subscribers} active</b>\n"
            f"💳 Payments: <b>{pending} pending{pending_tag}</b>\n"
            f"🎯 Predictions: <b>{active_predictions} live</b>\n\n"
            "Select a section below:"
        )
        keyboard = [
            [
                {"text": "👥 Subscribers", "callback_data": "admin:subscribers"},
                {"text": "💳 Payments" + pending_tag, "callback_data": "admin:payments"},
            ],
            [
                {"text": "📈 Revenue", "callback_data": "admin:revenue"},
                {"text": "🏦 Prop Firm Mode", "callback_data": "prop_firm:menu"},
            ],
            [
                {"text": "🔐 Trading Accounts", "callback_data": "admin:accounts"},
            ],
            [
                {"text": "🛡️ Risk Management", "callback_data": "menu:risk"},
                {"text": "📊 Performance Stats", "callback_data": "menu:stats"},
            ],
            [
                {"text": "🤖 AI Advisory", "callback_data": "menu:ai"},
                {"text": "⚙️ System & Alerts", "callback_data": "menu:system"},
            ],
            [
                {"text": "🧠 Learning Report", "callback_data": "stats:learning"},
                {"text": "🧪 Shadow Report", "callback_data": "admin:key_level_shadow"},
            ],
            [
                {"text": "⚙️ System Health", "callback_data": "admin:health"},
                {"text": "🛡️ Audit Log", "callback_data": "admin:audit"},
            ],
            [
                {"text": "📋 Operations Snapshot", "callback_data": "admin:operations_snapshot"},
                {"text": "🧪 Execution Quality", "callback_data": "admin:execution_quality"},
            ],
            [
                {"text": "🏠 Back to Main Menu", "callback_data": "menu:main"},
            ],
        ]
        self._record_operational_audit(chat_id, "admin_console_view")
        if message_id is None:
            telegram_channel.send_inline_menu(chat_id, text, keyboard)
        else:
            telegram_channel.edit_inline_menu(chat_id, message_id, text, keyboard)

    _TRADING_ACCOUNT_DRAFTS_KEY = "trading_accounts.telegram_drafts"

    def _trading_account_drafts(self) -> dict[str, dict]:
        raw = self._runtime_store.get(self._TRADING_ACCOUNT_DRAFTS_KEY, {}) or {}
        if not isinstance(raw, dict):
            return {}
        return {
            str(chat_id): dict(draft)
            for chat_id, draft in raw.items()
            if isinstance(draft, dict)
        }

    def _save_trading_account_drafts(self, drafts: dict[str, dict]) -> None:
        self._runtime_store.set(self._TRADING_ACCOUNT_DRAFTS_KEY, drafts)

    def _clear_trading_account_draft(self, chat_id: str) -> None:
        drafts = self._trading_account_drafts()
        drafts.pop(str(chat_id), None)
        self._save_trading_account_drafts(drafts)

    @staticmethod
    def _delete_one_time_telegram_message(telegram_channel, chat_id: str, message_id) -> None:
        """Best-effort cleanup for bot UI; a deletion failure must not block setup."""
        deleter = getattr(telegram_channel, "delete_message", None)
        if callable(deleter):
            try:
                deleter(chat_id, message_id)
            except Exception:  # noqa: BLE001 - Telegram cleanup is non-critical
                pass

    @staticmethod
    def _send_one_time_telegram_prompt(
        telegram_channel,
        chat_id: str,
        text: str,
        *,
        reply_to_message_id: int | None = None,
    ) -> int | None:
        """Send a trackable prompt, with a compatibility fallback for tests."""
        sender = getattr(telegram_channel, "send_one_time_prompt", None)
        if callable(sender):
            message_id = sender(
                chat_id,
                text,
                reply_to_message_id=reply_to_message_id,
            )
            return message_id if isinstance(message_id, int) and not isinstance(message_id, bool) else None
        if reply_to_message_id is None:
            telegram_channel.send_reply(chat_id, text)
        else:
            telegram_channel.send_reply(chat_id, text, message_id=reply_to_message_id)
        return None

    def _replace_trading_account_prompt(
        self,
        drafts: dict[str, dict],
        chat_id: str,
        draft: dict,
        telegram_channel,
        text: str,
        *,
        reply_to_message_id: int | None = None,
    ) -> None:
        """Delete the consumed wizard prompt and save the next prompt ID."""
        self._delete_one_time_telegram_message(
            telegram_channel,
            chat_id,
            draft.get("prompt_message_id"),
        )
        prompt_id = self._send_one_time_telegram_prompt(
            telegram_channel,
            chat_id,
            text,
            reply_to_message_id=reply_to_message_id,
        )
        if prompt_id is None:
            draft.pop("prompt_message_id", None)
        else:
            draft["prompt_message_id"] = prompt_id
        drafts[str(chat_id)] = draft
        self._save_trading_account_drafts(drafts)

    def _show_trading_accounts_menu(
        self,
        chat_id: str,
        telegram_channel,
        message_id: Optional[int] = None,
    ) -> None:
        """Render the admin-only, secret-free MT5 account selector."""
        try:
            registry = load_trading_account_registry(self._runtime_store)
        except ValueError:
            telegram_channel.send_reply(
                chat_id,
                "Saved trading-account state is invalid. Trading remains blocked until it is repaired.",
            )
            return

        active = registry.active_account
        lines = [
            "🤖 <b>ATLAX Admin  ·  🔐 Trading Accounts</b>",
            "━━━━━━━━━━━━━━━━━━━━━━━━",
            "",
        ]
        if active is None:
            lines.append("Selected target: <b>none</b>")
        else:
            lines.extend(
                (
                    f"Selected target: <b>{escape(active.label)}</b>",
                    f"MT5: <code>{active.login}</code> · {escape(active.server)}",
                )
            )
        lines.extend(
            (
                "",
                "Select an account, then sign in to that account in the local MT5 terminal.",
                "ATLAX reconnects only after the live login and server match the selected target.",
                "",
                "<i>Passwords are never requested, displayed, or stored here.</i>",
                "<i>Account selection does not change the Prop-Firm risk policy.</i>",
                "",
            )
        )

        keyboard = []
        for account in registry.accounts:
            marker = "✅ " if account.profile_id == registry.active_profile_id else ""
            keyboard.append(
                [
                    {
                        "text": f"{marker}{account.label}",
                        "callback_data": f"admin:accounts:select:{account.profile_id}",
                    }
                ]
            )
        if not registry.accounts:
            lines.append("No saved accounts yet.")
        keyboard.extend(
            (
                [{"text": "➕ Add account", "callback_data": "admin:accounts:add"}],
                [{"text": "🗑️ Delete account", "callback_data": "admin:accounts:delete"}],
                [{"text": "🔄 Verify MT5 now", "callback_data": "admin:accounts:verify"}],
                [{"text": "◀️ Admin Menu", "callback_data": "admin:home"}],
            )
        )
        self._record_operational_audit(chat_id, "trading_accounts_view")
        text = "\n".join(lines)
        if message_id is None:
            telegram_channel.send_inline_menu(chat_id, text, keyboard)
        else:
            telegram_channel.edit_inline_menu(chat_id, message_id, text, keyboard)

    def _show_trading_accounts_delete_menu(
        self,
        chat_id: str,
        telegram_channel,
        message_id: Optional[int] = None,
    ) -> None:
        """List saved accounts that can be permanently removed."""
        try:
            registry = load_trading_account_registry(self._runtime_store)
        except ValueError:
            telegram_channel.send_reply(
                chat_id,
                "Saved trading-account state is invalid. Trading remains blocked until it is repaired.",
            )
            return

        lines = [
            "⚠️ <b>Delete Trading Account</b>",
            "━━━━━━━━━━━━━━━━━━━━━━━━",
            "",
            "Choose an account to remove permanently.",
            "This deletes only ATLAX's saved identity record; it never deletes an MT5 account.",
            "",
        ]
        keyboard = []
        deletable = 0
        for account in registry.accounts:
            if account.profile_id == registry.active_profile_id:
                lines.append(
                    f"🔒 <b>{escape(account.label)}</b> · selected (select another account first)"
                )
                continue
            deletable += 1
            keyboard.append(
                [
                    {
                        "text": f"🗑️ {account.label}",
                        "callback_data": f"admin:accounts:delete:{account.profile_id}",
                    }
                ]
            )

        if not registry.accounts:
            lines.append("No saved accounts are available.")
        elif deletable == 0:
            lines.append("No account is currently eligible for deletion.")
        keyboard.append([{"text": "◀️ Accounts", "callback_data": "admin:accounts"}])
        self._record_operational_audit(chat_id, "trading_accounts_delete_view")
        text = "\n".join(lines)
        if message_id is None:
            telegram_channel.send_inline_menu(chat_id, text, keyboard)
        else:
            telegram_channel.edit_inline_menu(chat_id, message_id, text, keyboard)

    def _begin_trading_account_add(
        self,
        chat_id: str,
        telegram_channel,
        message_id: int,
    ) -> None:
        drafts = self._trading_account_drafts()
        drafts[str(chat_id)] = {"step": "label", "prompt_message_id": message_id}
        self._save_trading_account_drafts(drafts)
        telegram_channel.edit_inline_menu(
            chat_id,
            message_id,
            "🔐 <b>Add Trading Account</b>\n\nSend a short account label, for example <code>Maven 2K</code>.\n\nDo not send a password.",
            [
                [{"text": "Cancel", "callback_data": "admin:accounts:cancel_draft"}],
                [{"text": "◀️ Accounts", "callback_data": "admin:accounts"}],
            ],
        )

    def _consume_trading_account_draft_input(
        self,
        raw_value: str,
        chat_id: str,
        is_admin: bool,
        telegram_channel,
        message_id: int | None = None,
    ) -> bool:
        """Consume one non-secret wizard response without logging its raw value."""
        drafts = self._trading_account_drafts()
        draft = drafts.get(str(chat_id))
        if draft is None:
            return False
        if not is_admin:
            self._clear_trading_account_draft(chat_id)
            telegram_channel.send_reply(chat_id, "That operation is available to the admin only.")
            return True

        value = str(raw_value or "").strip()
        if value.lower() == "/cancel":
            self._delete_one_time_telegram_message(
                telegram_channel,
                chat_id,
                draft.get("prompt_message_id"),
            )
            self._clear_trading_account_draft(chat_id)
            telegram_channel.send_reply(chat_id, "Trading-account setup cancelled.")
            return True

        step = str(draft.get("step") or "")
        if step == "label":
            if not value or len(value) > 48:
                telegram_channel.send_reply(chat_id, "Use an account label between 1 and 48 characters.")
                return True
            draft.update({"step": "login", "label": value})
            self._replace_trading_account_prompt(
                drafts,
                chat_id,
                draft,
                telegram_channel,
                "Now send the MT5 login ID (numbers only). Do not send a password.",
                reply_to_message_id=message_id,
            )
            return True

        if step == "login":
            if not value.isdigit() or int(value) <= 0:
                telegram_channel.send_reply(chat_id, "Send a valid positive MT5 login ID using numbers only.")
                return True
            draft.update({"step": "server", "login": int(value)})
            self._replace_trading_account_prompt(
                drafts,
                chat_id,
                draft,
                telegram_channel,
                "Finally, send the exact MT5 broker server name shown in your terminal.",
                reply_to_message_id=message_id,
            )
            return True

        if step == "server":
            if not value or len(value) > 128 or any(ord(char) < 32 for char in value):
                telegram_channel.send_reply(chat_id, "Send a valid broker server name (1-128 printable characters).")
                return True
            try:
                registry = add_trading_account(
                    self._runtime_store,
                    label=str(draft.get("label") or ""),
                    login=int(draft.get("login") or 0),
                    server=value,
                    actor=f"telegram:{chat_id}",
                )
            except ValueError as exc:
                telegram_channel.send_reply(chat_id, f"Account was not saved: {escape(str(exc))}.")
                return True

            self._delete_one_time_telegram_message(
                telegram_channel,
                chat_id,
                draft.get("prompt_message_id"),
            )
            self._clear_trading_account_draft(chat_id)
            saved = next(
                (account for account in registry.accounts if account.identity_key == (int(draft["login"]), value)),
                None,
            )
            self._record_operational_audit(
                chat_id,
                "trading_account_added",
                metadata={"profile_id": saved.profile_id if saved else ""},
            )
            telegram_channel.send_reply(
                chat_id,
                "Account saved without a password. Select it when you are ready to trade from that MT5 login.",
            )
            return True

        self._clear_trading_account_draft(chat_id)
        telegram_channel.send_reply(chat_id, "Trading-account setup expired. Start again from Trading Accounts.")
        return True

    def _handle_trading_account_callback(
        self,
        data: str,
        chat_id: str,
        message_id: int,
        telegram_channel,
    ) -> None:
        if data == "admin:accounts":
            self._show_trading_accounts_menu(chat_id, telegram_channel, message_id)
            return
        if data == "admin:accounts:add":
            self._begin_trading_account_add(chat_id, telegram_channel, message_id)
            return
        if data == "admin:accounts:delete":
            self._show_trading_accounts_delete_menu(chat_id, telegram_channel, message_id)
            return
        if data.startswith("admin:accounts:delete_confirm:"):
            profile_id = data.rsplit(":", 1)[-1]
            try:
                registry = load_trading_account_registry(self._runtime_store)
            except ValueError:
                telegram_channel.send_reply(
                    chat_id,
                    "Saved trading-account state is invalid. Trading remains blocked until it is repaired.",
                )
                return
            account = next(
                (saved for saved in registry.accounts if saved.profile_id == profile_id),
                None,
            )
            if account is None:
                telegram_channel.send_reply(chat_id, "That saved account is no longer available.")
                self._show_trading_accounts_menu(chat_id, telegram_channel, message_id)
                return
            try:
                updated = delete_trading_account(
                    self._runtime_store,
                    profile_id=profile_id,
                    actor=f"telegram:{chat_id}",
                )
            except ValueError as exc:
                telegram_channel.send_reply(chat_id, f"Account was not deleted: {escape(str(exc))}.")
                self._show_trading_accounts_delete_menu(chat_id, telegram_channel, message_id)
                return
            self._record_operational_audit(
                chat_id,
                "trading_account_deleted",
                metadata={
                    "profile_id": account.profile_id,
                    "label": account.label,
                    "remaining_accounts": len(updated.accounts),
                },
            )
            self._show_trading_accounts_menu(chat_id, telegram_channel, message_id)
            return
        if data.startswith("admin:accounts:delete:"):
            profile_id = data.rsplit(":", 1)[-1]
            try:
                registry = load_trading_account_registry(self._runtime_store)
            except ValueError:
                telegram_channel.send_reply(
                    chat_id,
                    "Saved trading-account state is invalid. Trading remains blocked until it is repaired.",
                )
                return
            account = next(
                (saved for saved in registry.accounts if saved.profile_id == profile_id),
                None,
            )
            if account is None:
                telegram_channel.send_reply(chat_id, "That saved account is no longer available.")
                self._show_trading_accounts_delete_menu(chat_id, telegram_channel, message_id)
                return
            if account.profile_id == registry.active_profile_id:
                telegram_channel.send_reply(
                    chat_id,
                    "The selected account is protected. Select another saved account first, then delete this one.",
                )
                self._show_trading_accounts_delete_menu(chat_id, telegram_channel, message_id)
                return
            telegram_channel.edit_inline_menu(
                chat_id,
                message_id,
                "⚠️ <b>Permanent deletion</b>\n\n"
                f"Remove <b>{escape(account.label)}</b> (<code>{account.login}</code> · "
                f"{escape(account.server)}) from ATLAX's saved account list?\n\n"
                "This cannot be undone. It does not close trades or delete the broker account.",
                [
                    [{
                        "text": "⚠️ Permanently delete",
                        "callback_data": f"admin:accounts:delete_confirm:{account.profile_id}",
                    }],
                    [{"text": "Cancel", "callback_data": "admin:accounts:delete"}],
                ],
            )
            return
        if data == "admin:accounts:cancel_draft":
            drafts = self._trading_account_drafts()
            draft = drafts.get(str(chat_id), {})
            self._delete_one_time_telegram_message(
                telegram_channel,
                chat_id,
                draft.get("prompt_message_id") or message_id,
            )
            self._clear_trading_account_draft(chat_id)
            return
        if data == "admin:accounts:verify":
            registry = load_trading_account_registry(self._runtime_store)
            active = registry.active_account
            if active is None:
                telegram_channel.send_reply(chat_id, "Select a saved account before verifying MT5.")
                return
            if mt5 is None:
                telegram_channel.send_reply(chat_id, "MT5 is not available on this host, so the account cannot be verified.")
                return
            try:
                identity = read_mt5_account_identity(
                    mt5,
                    terminal_instance_id=getattr(
                        getattr(self, "_account_mode_cfg", None), "terminal_instance_id", None
                    ),
                )
            except Exception:  # noqa: BLE001 - verification must fail closed
                telegram_channel.send_reply(
                    chat_id,
                    "MT5 is not connected to the selected account yet. Sign in through MT5, then try again.",
                )
                return
            if identity.login != active.login or identity.server.strip() != active.server.strip():
                telegram_channel.send_reply(
                    chat_id,
                    "The live MT5 account does not match the selected target. Trading remains blocked until it matches.",
                )
                return
            self._record_operational_audit(
                chat_id,
                "trading_account_verified",
                metadata={"profile_id": active.profile_id},
            )
            self._delete_one_time_telegram_message(telegram_channel, chat_id, message_id)
            telegram_channel.send_reply(chat_id, "Selected MT5 account verified. ATLAX can reconnect safely.")
            return
        if data.startswith("admin:accounts:select:"):
            profile_id = data.rsplit(":", 1)[-1]
            try:
                registry = select_trading_account(
                    self._runtime_store,
                    profile_id=profile_id,
                    actor=f"telegram:{chat_id}",
                )
            except ValueError:
                telegram_channel.send_reply(chat_id, "That saved account is not available.")
                return
            active = registry.active_account
            self._record_operational_audit(
                chat_id,
                "trading_account_selected",
                metadata={"profile_id": active.profile_id if active else ""},
            )
            self._delete_one_time_telegram_message(telegram_channel, chat_id, message_id)
            telegram_channel.send_reply(
                chat_id,
                "Account selected. ATLAX is stopping its current MT5 session; sign in to the selected account in MT5. Trading remains blocked until the identity matches.",
            )
            return
        telegram_channel.send_reply(chat_id, "Unknown trading-account operation.")

    def _show_admin_payment_queue(self, chat_id: str, telegram_channel, db, message_id: Optional[int] = None) -> None:
        payment_service = self._ensure_payment_service(db)
        pending = payment_service._store.list_pending() if payment_service else []
        lines = ["🤖 <b>ATLAX Admin  ·  💳 Payment Queue</b>", "━━━━━━━━━━━━━━━━━━━━━━━━", "", f"<b>{len(pending)} pending payment(s):</b>", ""]
        keyboard = []
        for intent in pending[:20]:
            reference = str(intent.get("reference", ""))
            lines.append(
                f"<code>{escape(reference)}</code> | @{escape(str(intent.get('username', 'unknown')))} | "
                f"{escape(str(intent.get('method', 'unknown')))} | <b>{escape(str(intent.get('status', 'unknown')))}</b>"
            )
            keyboard.append([
                {"text": f"✅ Approve {reference}", "callback_data": f"payment:approve:{reference}"},
                {"text": f"❌ Reject {reference}", "callback_data": f"payment:reject:{reference}"},
            ])
        if not pending:
            lines.append("No pending payments.")
        keyboard.extend([
            [
                {"text": "📈 Revenue", "callback_data": "admin:revenue"},
                {"text": "📜 History", "callback_data": "payment:history"},
            ],
            [{"text": "◀️ Admin Menu", "callback_data": "admin:home"}],
        ])
        self._record_operational_audit(chat_id, "admin_payment_queue_view")
        text = "\n".join(lines)
        if message_id is None:
            telegram_channel.send_inline_menu(chat_id, text, keyboard)
        else:
            telegram_channel.edit_inline_menu(chat_id, message_id, text, keyboard)

    def _show_admin_subscribers(self, chat_id: str, telegram_channel, db, message_id: Optional[int] = None) -> None:
        subscribers = db.list_subscribers()
        lines = ["🤖 <b>ATLAX Admin  ·  👥 Subscribers</b>", "━━━━━━━━━━━━━━━━━━━━━━━━", "", f"<b>{len(subscribers)} registered subscriber(s):</b>", ""]
        keyboard = []
        for subscriber in subscribers[:20]:
            target = self._admin_target_id(subscriber.get("chat_id", ""))
            if target is None:
                continue
            status = "active" if subscriber.get("active") else "inactive"
            username = escape(str(subscriber.get("username") or "unknown"))
            lines.append(f"@{username} | <b>{status}</b> | {escape(str(subscriber.get('style', 'unknown')))}")
            keyboard.append([{
                "text": f"👤 @{str(subscriber.get('username') or 'unknown')} ({'✅' if subscriber.get('active') else '⛔'} {subscriber.get('style', '?')})",
                "callback_data": f"admin:sub:view:{target}",
            }])
        if not subscribers:
            lines.append("No subscribers are registered.")
        keyboard.extend([
            [{"text": "◀️ Admin Menu", "callback_data": "admin:home"}],
        ])
        self._record_operational_audit(chat_id, "admin_subscribers_view")
        text = "\n".join(lines)
        if message_id is None:
            telegram_channel.send_inline_menu(chat_id, text, keyboard)
        else:
            telegram_channel.edit_inline_menu(chat_id, message_id, text, keyboard)

    def _show_admin_subscriber_detail(self, chat_id: str, telegram_channel, db, target_chat_id: str, message_id: Optional[int] = None) -> None:
        target = self._admin_target_id(target_chat_id)
        subscriber = db.get_subscriber(target) if target is not None else None
        if subscriber is None:
            text = "<b>Subscriber</b>\n\nSubscriber not found."
            keyboard = [[{"text": "◀️ Subscribers", "callback_data": "admin:subscribers"}, {"text": "🏠 Admin Menu", "callback_data": "admin:home"}]]
        else:
            settings = db.get_settings(target)
            ai_usage = db.get_ai_usage(target) if hasattr(db, "get_ai_usage") else {}
            ai_status = "blocked" if ai_usage.get("blocked") else "available"
            text = (
                "🤖 <b>ATLAX Admin  ·  👤 Subscriber Detail</b>\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                f"Username: <b>@{escape(str(subscriber.get('username') or 'unknown'))}</b>\n"
                f"Chat ID: <code>{escape(target)}</code>\n"
                f"Status: <b>{'active' if subscriber.get('active') else 'inactive'}</b>\n"
                f"Style: <b>{escape(str(subscriber.get('style', 'unknown')))}</b>\n"
                f"Timeframes: <b>{escape(', '.join(str(item) for item in subscriber.get('timeframes', [])) or 'n/a')}</b>\n"
                f"Symbols: <b>{escape(', '.join(str(item) for item in settings.get('symbols', [])) or 'All symbols')}</b>\n"
                f"Expires: <code>{escape(str(subscriber.get('subscription_expires_at') or 'No expiry set'))}</code>\n"
                f"Source: <b>{escape(str(subscriber.get('subscription_source') or 'unknown'))}</b>\n"
                f"Subscriber AI: <b>{ai_status}</b>\n"
                f"AI questions today: <b>{ai_usage.get('daily_count', 0)}</b>\n"
                f"AI abuse strikes: <b>{ai_usage.get('abuse_strikes', 0)}</b>"
            )
            keyboard = []
            keyboard.append([{"text": "🔄 Reset AI Quota", "callback_data": f"admin:sub:ai_reset:{target}"}])
            if ai_usage.get("blocked"):
                keyboard.append([{"text": "🔓 Unblock Subscriber AI", "callback_data": f"admin:sub:ai_unblock:{target}"}])
            else:
                keyboard.append([{"text": "🔒 Block Subscriber AI", "callback_data": f"admin:sub:ai_block:{target}"}])
            if target != str(chat_id) and subscriber.get("active"):
                keyboard.append([{"text": "⏸️ Deactivate Subscriber", "callback_data": f"admin:sub:confirm_deactivate:{target}"}])
            keyboard.extend([
                [
                    {"text": "◀️ Subscribers", "callback_data": "admin:subscribers"},
                    {"text": "🏠 Admin Menu", "callback_data": "admin:home"},
                ],
            ])
        self._record_operational_audit(chat_id, "admin_subscriber_view", target_chat_id=target or "", result="success" if subscriber else "not_found")
        if message_id is None:
            telegram_channel.send_inline_menu(chat_id, text, keyboard)
        else:
            telegram_channel.edit_inline_menu(chat_id, message_id, text, keyboard)

    def _show_admin_deactivation_confirmation(self, chat_id: str, telegram_channel, db, target_chat_id: str, message_id: Optional[int] = None) -> None:
        target = self._admin_target_id(target_chat_id)
        subscriber = db.get_subscriber(target) if target is not None else None
        if subscriber is None or target == str(chat_id):
            text = "<b>Deactivation rejected</b>\n\nThe target is invalid or protected."
            keyboard = [[{"text": "◀️ Subscribers", "callback_data": "admin:subscribers"}]]
        else:
            text = (
                "🤖 <b>ATLAX Admin  ·  ⏸️ Confirm Deactivation</b>\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                f"Deactivate @{escape(str(subscriber.get('username') or 'unknown'))}?\n"
                "This removes active alert access but preserves the record for audit."
            )
            keyboard = [
                [{"text": "✅ Confirm Deactivate", "callback_data": f"admin:sub:deactivate:{target}"}],
                [{"text": "❌ Cancel", "callback_data": f"admin:sub:view:{target}"}],
            ]
        if message_id is None:
            telegram_channel.send_inline_menu(chat_id, text, keyboard)
        else:
            telegram_channel.edit_inline_menu(chat_id, message_id, text, keyboard)

    def _admin_reset_subscriber_ai(self, chat_id: str, telegram_channel, db, target_chat_id: str, message_id: Optional[int] = None) -> None:
        target = self._admin_target_id(target_chat_id)
        reset = bool(target and hasattr(db, "reset_ai_usage") and db.reset_ai_usage(target))
        self._record_operational_audit(
            chat_id,
            "subscriber_ai_quota_reset",
            target_chat_id=target or "",
            result="success" if reset else "failed",
        )
        self._show_admin_subscriber_detail(chat_id, telegram_channel, db, target or "", message_id)

    def _admin_set_subscriber_ai_block(self, chat_id: str, telegram_channel, db, target_chat_id: str, blocked: bool, message_id: Optional[int] = None) -> None:
        target = self._admin_target_id(target_chat_id)
        changed = bool(target and hasattr(db, "set_ai_blocked") and db.set_ai_blocked(target, blocked))
        action = "subscriber_ai_block" if blocked else "subscriber_ai_unblock"
        self._record_operational_audit(
            chat_id,
            action,
            target_chat_id=target or "",
            result="success" if changed else "failed",
        )
        self._show_admin_subscriber_detail(chat_id, telegram_channel, db, target or "", message_id)

    def _deactivate_admin_subscriber(self, chat_id: str, telegram_channel, db, target_chat_id: str, message_id: Optional[int] = None) -> None:
        target = self._admin_target_id(target_chat_id)
        if target is None or target == str(chat_id) or db.get_subscriber(target) is None:
            self._record_operational_audit(chat_id, "subscriber_deactivate", target_chat_id=target or "", result="denied")
            telegram_channel.send_reply(chat_id, "Subscriber deactivation was rejected.")
            return
        db.deactivate(target)
        self._record_operational_audit(chat_id, "subscriber_deactivate", target_chat_id=target)
        self._show_admin_subscriber_detail(chat_id, telegram_channel, db, target, message_id)

    def _show_admin_health(self, chat_id: str, telegram_channel, db, message_id: Optional[int] = None) -> None:
        telegram = next((channel for channel in self._alert_engine._channels if channel.name == "telegram"), None)
        payment_service = self._ensure_payment_service(db)
        _scanner_state = "🟢 Running" if not self._is_paused else "🟡 Paused"
        _tg_state = "🟢 Enabled" if telegram and telegram.is_enabled else "🔴 Disabled"
        _pay_state = "🟢 Enabled" if payment_service else "🔴 Disabled"
        _pending_pay = payment_service.pending_count() if payment_service else 0
        _predictions = len(getattr(getattr(self._alert_engine, 'tracker', None), 'predictions', {}) or {})
        text = (
            "🤖 <b>ATLAX Admin  ·  ⚙️ System Health</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"⚡ Scanner: <b>{_scanner_state}</b>  •  🔄 Cycle <b>#{self._cycle_count}</b>\n\n"
            f"📡 Telegram: <b>{_tg_state}</b>\n"
            f"💳 Payments: <b>{_pay_state}</b>\n\n"
            f"👥 Active subscribers: <b>{db.total_active()}</b>\n"
            f"💳 Pending payments: <b>{_pending_pay}</b>\n"
            f"🎯 Active predictions: <b>{_predictions}</b>"
        )
        keyboard = [
            [
                {"text": "◀️ Admin Menu", "callback_data": "admin:home"},
                {"text": "🛠️ System Menu", "callback_data": "menu:system"},
            ],
        ]
        self._record_operational_audit(chat_id, "admin_health_view")
        if message_id is None:
            telegram_channel.send_inline_menu(chat_id, text, keyboard)
        else:
            telegram_channel.edit_inline_menu(chat_id, message_id, text, keyboard)

    def _build_operations_snapshot(self) -> dict:
        """Build the read-only Phase 1 operations snapshot."""
        observed_at = datetime.now(timezone.utc)
        mode_state = load_account_mode_state(self._runtime_store)
        account_payload = None
        budget_payload = None
        profile_payload = None
        mt5_status = "UNAVAILABLE"

        for profile in self._prop_firm_profiles or ():
            if (
                getattr(profile, "profile_id", "") == mode_state.selected_profile_id
                and getattr(profile, "version", "") == mode_state.selected_profile_version
            ):
                profile_payload = {
                    "profile_id": profile.profile_id,
                    "version": profile.version,
                    "firm_name": profile.firm_name,
                    "program_name": profile.program_name,
                    "phase": profile.phase,
                    "account_type": profile.account_type,
                    "currency": profile.currency,
                    "nominal_balance": (
                        str(profile.nominal_balance)
                        if profile.nominal_balance is not None
                        else None
                    ),
                }
                break

        if mt5 is not None:
            try:
                account_info = mt5.account_info()
                positions = mt5.positions_get()
                if account_info is not None and positions is not None:
                    mt5_status = "CONNECTED"
                    account_payload = {
                        "login": int(account_info.login),
                        "server": str(account_info.server),
                        "currency": str(account_info.currency).upper(),
                        "balance": str(account_info.balance),
                        "equity": str(account_info.equity),
                        "trade_allowed": bool(getattr(account_info, "trade_allowed", False)),
                        "trade_expert": bool(getattr(account_info, "trade_expert", False)),
                        "open_position_count": len(positions),
                    }
                    if self._prop_firm_runtime is not None:
                        try:
                            daily_pnl, daily_pnl_available = self._get_atlax_daily_pnl()
                            weekly_pnl, weekly_pnl_available = self._get_atlax_weekly_pnl()
                            account_state = AccountState(
                                balance=Decimal(str(account_info.balance)),
                                equity=Decimal(str(account_info.equity)),
                                daily_pnl=daily_pnl,
                                weekly_pnl=weekly_pnl,
                                open_trade_count=len(positions),
                                trades_today=0,
                                open_symbols=tuple(
                                    str(getattr(position, "symbol", ""))
                                    for position in positions
                                ),
                                currency=str(account_info.currency).upper(),
                                snapshot_at=observed_at,
                                daily_pnl_available=daily_pnl_available,
                                weekly_pnl_available=weekly_pnl_available,
                            )
                            context = self._resolve_prop_firm_context(
                                SimpleNamespace(candidate_id="operations_snapshot"),
                                account_state,
                                positions=positions,
                            )
                            if context is not None:
                                budget_payload = evaluate_loss_budget(
                                    context,
                                    as_of=observed_at,
                                ).to_dict()
                        except Exception as exc:  # noqa: BLE001 - status must remain read-only
                            logger.warning(
                                '{"event":"operations_snapshot_budget_failed",'
                                '"error":"%s"}',
                                str(exc)[:160],
                            )
            except Exception as exc:  # noqa: BLE001 - snapshot reports UNKNOWN
                mt5_status = "UNKNOWN"
                logger.warning(
                    '{"event":"operations_snapshot_mt5_failed","error":"%s"}',
                    str(exc)[:160],
                )

        health = {
            "mt5": mt5_status,
            "telegram": "ENABLED" if any(
                channel.name == "telegram" and channel.is_enabled
                for channel in self._alert_engine._channels
            ) else "DISABLED",
            "runtime_store": "AVAILABLE",
            "market_data": "ACTIVE" if self._cycle_count > 0 else "NOT_STARTED",
            "watchdog": str(
                (self._runtime_store.get("operations.watchdog", {}) or {}).get(
                    "status", "UNKNOWN"
                )
            ),
        }
        return build_operations_snapshot(
            observed_at=observed_at.isoformat(),
            scanner={
                "running": bool(self._running),
                "paused": bool(self._is_paused),
                "cycle": int(self._cycle_count),
                "last_cycle_at": self._last_cycle_at,
            },
            account_mode=mode_state.to_dict(),
            account=account_payload,
            profile=profile_payload,
            loss_budget=budget_payload,
            health=health,
            metrics=self._runtime_store.get("operations.metrics", {}),
            circuit_breaker=self._runtime_store.get("operations.circuit_breaker", {}),
            reconciliation=self._reconciliation_report,
            prop_firm_profiles=self._profile_monitor_report.to_dict(),
            watchdog=self._runtime_store.get("operations.watchdog", {}),
        )

    def _show_operations_snapshot(self, chat_id: str, telegram_channel, message_id: Optional[int] = None) -> None:
        snapshot = self._build_operations_snapshot()
        try:
            self._runtime_store.set("operations.snapshot", snapshot)
        except Exception as exc:  # noqa: BLE001 - UI remains available if persistence is unavailable
            logger.warning(
                '{"event":"operations_snapshot_persist_failed","error":"%s"}',
                str(exc)[:160],
            )
        mode = snapshot["account_mode"]
        account = snapshot.get("account") or {}
        budget = snapshot.get("loss_budget") or {}
        profile = snapshot.get("profile") or {}
        account_label = (
            f"<code>{escape(str(account.get('login', 'UNKNOWN')))}</code> · "
            f"{escape(str(account.get('server', 'UNKNOWN')))}"
            if account
            else "UNKNOWN"
        )
        status = escape(str(mode.get("status", "UNKNOWN")))
        mode_label = escape(str(mode.get("mode", "UNKNOWN")))
        profile_label = escape(str(profile.get("profile_id") or mode.get("selected_profile_id") or "UNKNOWN"))
        version = escape(str(profile.get("version") or mode.get("selected_profile_version") or ""))
        breaker = snapshot.get("circuit_breaker") or {}
        breaker_state = escape(str(breaker.get("state", "UNKNOWN")))
        breaker_reason = escape(str(breaker.get("reason_code", "UNKNOWN")))
        reconciliation = snapshot.get("reconciliation") or {}
        reconciliation_status = escape(str(reconciliation.get("status", "UNKNOWN")))
        profile_monitor = snapshot.get("prop_firm_profiles") or {}
        selected_profile_monitor = next(
            (
                item
                for item in profile_monitor.get("entries", [])
                if str(item.get("profile_id", ""))
                == str(mode.get("selected_profile_id", ""))
            ),
            None,
        )
        profile_monitor_status = escape(
            str(
                (selected_profile_monitor or {}).get(
                    "status", profile_monitor.get("overall_status", "UNKNOWN")
                )
            )
        )
        overall = escape(str(budget.get("remaining_overall_buffer", "UNKNOWN")))
        daily = escape(str(budget.get("remaining_daily_buffer", "UNKNOWN")))
        allowed = escape(str(budget.get("allowed_new_trade_risk", "UNKNOWN")))
        text = (
            "🤖 <b>ATLAX Operations Snapshot</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"🏦 <b>Active MT5:</b> {account_label}\n"
            f"💱 <b>Currency:</b> {escape(str(account.get('currency', 'UNKNOWN')))}\n"
            f"⚙️ <b>Mode:</b> {mode_label}\n"
            f"🔐 <b>Verification:</b> {status}\n"
            f"📚 <b>Profile:</b> {profile_label} {version}\n\n"
            f"🧭 <b>Profile monitor:</b> {profile_monitor_status}\n"
            f"🛡️ <b>New-entry guard:</b> {breaker_state}\n"
            f"🧾 <b>Guard reason:</b> <code>{breaker_reason}</code>\n\n"
            f"🔄 <b>Trade reconciliation:</b> {reconciliation_status}\n\n"
            f"💰 <b>Balance:</b> {escape(str(account.get('balance', 'UNKNOWN')))}\n"
            f"📈 <b>Equity:</b> {escape(str(account.get('equity', 'UNKNOWN')))}\n"
            f"🛡️ <b>Overall buffer:</b> {overall}\n"
            f"📅 <b>Daily buffer:</b> {daily}\n"
            f"🎯 <b>Allowed new risk:</b> {allowed}\n\n"
            f"⚡ <b>Scanner:</b> {'🟢 Running' if snapshot['scanner']['running'] else '🔴 Stopped'} · "
            f"Cycle #{snapshot['scanner']['cycle']}\n"
            f"🔌 <b>MT5:</b> {escape(str(snapshot['health']['mt5']))}\n"
            f"📡 <b>Telegram:</b> {escape(str(snapshot['health']['telegram']))}\n"
            f"🩺 <b>Watchdog:</b> {escape(str(snapshot['health'].get('watchdog', 'UNKNOWN')))}"
        )
        keyboard = [
            [{"text": "🔄 Refresh", "callback_data": "admin:operations_snapshot"}],
            [{"text": "🧪 Execution Quality", "callback_data": "admin:execution_quality"}],
            [{"text": "🏦 Prop Firm Mode", "callback_data": "prop_firm:menu"}],
            [{"text": "◀️ Admin Menu", "callback_data": "admin:home"}],
        ]
        self._record_operational_audit(chat_id, "admin_operations_snapshot_view")
        if message_id is None:
            telegram_channel.send_inline_menu(chat_id, text, keyboard)
        else:
            telegram_channel.edit_inline_menu(chat_id, message_id, text, keyboard)

    def _show_admin_execution_quality(
        self,
        chat_id: str,
        telegram_channel,
        message_id: Optional[int] = None,
    ) -> None:
        """Display recent broker-quality facts without exposing raw payloads."""

        journal = getattr(self, "_journal", None)
        if journal is None:
            journal = getattr(self._alert_engine, "_journal", None)
        try:
            events = journal.list_execution_quality_events(limit=12) if journal else []
        except Exception as exc:  # noqa: BLE001 - admin view reports UNKNOWN
            logger.warning(
                '{"event":"execution_quality_view_failed","error":"%s"}',
                str(exc)[:160],
            )
            events = []

        lines = [
            "🤖 <b>ATLAX Admin  ·  🧪 Execution Quality</b>",
            "━━━━━━━━━━━━━━━━━━━━━━━━",
            "",
            "Recent broker and position-lifecycle facts:",
            "",
        ]
        for event in events:
            occurred_at = str(event.get("occurred_at", "UNKNOWN"))
            if len(occurred_at) > 19:
                occurred_at = occurred_at[:19].replace("T", " ")
            symbol = str(event.get("symbol") or "UNKNOWN")
            timeframe = str(event.get("timeframe") or "")
            direction = str(event.get("direction") or "")
            status = str(event.get("status") or "UNKNOWN")
            action = str(event.get("action") or event.get("event_type") or "UNKNOWN")
            reason = str(event.get("reason") or "")[:100]
            pnl = event.get("net_pnl")
            pnl_text = f" · net PnL {pnl}" if pnl not in (None, "") else ""
            lines.append(
                f"<code>{escape(occurred_at)}</code> · "
                f"<b>{escape(symbol)} {escape(timeframe)}</b> "
                f"{escape(direction)} · <b>{escape(status)}</b>\n"
                f"↳ {escape(action)}{escape(pnl_text)}"
                + (f" · {escape(reason)}" if reason else "")
            )
            lines.append("")
        if not events:
            lines.append("No execution-quality events are recorded yet.")
        keyboard = [
            [{"text": "🔄 Refresh", "callback_data": "admin:execution_quality"}],
            [{"text": "📋 Operations Snapshot", "callback_data": "admin:operations_snapshot"}],
            [{"text": "◀️ Admin Menu", "callback_data": "admin:home"}],
        ]
        self._record_operational_audit(chat_id, "admin_execution_quality_view")
        text = "\n".join(lines)
        if message_id is None:
            telegram_channel.send_inline_menu(chat_id, text, keyboard)
        else:
            telegram_channel.edit_inline_menu(chat_id, message_id, text, keyboard)

    def _show_admin_audit(self, chat_id: str, telegram_channel, message_id: Optional[int] = None) -> None:
        events = self._admin_audit.recent(20)
        lines = ["🤖 <b>ATLAX Admin  ·  🛡️ Audit Log</b>", "━━━━━━━━━━━━━━━━━━━━━━━━", "", f"<b>{len(events)} recent event(s):</b>", ""]
        for event in events:
            lines.append(
                f"<code>{escape(str(event.get('timestamp', 'unknown')))}</code> | "
                f"<b>{escape(str(event.get('action', 'unknown')))}</b> | "
                f"{escape(str(event.get('result', 'unknown')))}"
            )
        if not events:
            lines.append("No operational audit events recorded yet.")
        keyboard = [
            [{"text": "◀️ Admin Menu", "callback_data": "admin:home"}],
        ]
        if message_id is None:
            telegram_channel.send_inline_menu(chat_id, "\n".join(lines), keyboard)
        else:
            telegram_channel.edit_inline_menu(chat_id, message_id, "\n".join(lines), keyboard)

    def _show_payment_history(self, chat_id: str, telegram_channel, db, message_id: Optional[int] = None) -> None:
        payment_service = self._ensure_payment_service(db)
        if payment_service is None:
            text = "<b>Payment History</b>\n\nPayment configuration is not available."
        elif self._is_admin(chat_id):
            text = payment_service.build_admin_review_history_message()
        else:
            text = payment_service.build_payment_history_message(chat_id)
        keyboard = [
            [{"text": "↩️ Back to Subscription", "callback_data": "menu:subscribe"}],
            [{"text": "🏠 Home", "callback_data": "menu:main"}],
        ]
        if message_id is None:
            telegram_channel.send_inline_menu(chat_id, text, keyboard)
        else:
            telegram_channel.edit_inline_menu(chat_id, message_id, text, keyboard)

    def _show_subscription_menu(
        self,
        chat_id: str,
        telegram_channel,
        db,
        message_id: int = None,
        selected_plan: Optional[str] = None,
    ) -> None:
        payment_service = self._ensure_payment_service(db)
        if payment_service is None:
            text = (
                "💳 <b>ATLAX Subscription Checkout</b>\n\n"
                "The subscription checkout is not ready yet because its payment destination has not been configured. Please contact support for assistance."
            )
            keyboard = [[{"text": "↩️ Back", "callback_data": "menu:main"}]]
            if message_id is None:
                telegram_channel.send_inline_menu(chat_id, text, keyboard)
            else:
                telegram_channel.edit_inline_menu(chat_id, message_id, text, keyboard)
            return

        methods = payment_service.enabled_methods()
        if selected_plan not in {"standard", "premium"}:
            text = payment_service.build_subscription_pitch(chat_id)
            keyboard = [
                [{"text": "🌱 Standard Plan", "callback_data": "subscribe:plan:standard"}],
                [{"text": "⭐ Premium Plan", "callback_data": "subscribe:plan:premium"}],
            ]
        else:
            selected_plan = str(selected_plan).lower()
            text = payment_service.build_selected_plan_checkout(chat_id, selected_plan)
            keyboard = []
            if "crypto" in methods or "solana" in methods:
                callback = f"pay:crypto:{selected_plan}"
                keyboard.append([{"text": "🪙 Pay with Solana", "callback_data": callback}])
            if "mpesa" in methods:
                keyboard.append([{"text": "📱 Pay with M-Pesa", "callback_data": f"pay:mpesa:{selected_plan}"}])

        keyboard.extend([
            [{"text": "🔐 My Access", "callback_data": "payment:status"}],
            [{"text": "🧾 Payment History", "callback_data": "payment:history"}],
            [{"text": "🏠 Home", "callback_data": "menu:main"}],
        ])
        if message_id is None:
            telegram_channel.send_inline_menu(chat_id, text, keyboard)
        else:
            telegram_channel.edit_inline_menu(chat_id, message_id, text, keyboard)

    def _show_access_status(
        self,
        chat_id: str,
        telegram_channel,
        db,
        message_id: Optional[int] = None,
    ) -> None:
        payment_service = self._ensure_payment_service(db)
        if payment_service is not None:
            text = payment_service.build_payment_status_message(chat_id)
        else:
            from core.alerts.subscriber_db import STYLE_LABELS

            sub = db.get_subscriber(chat_id)
            if sub and db.is_registered(chat_id):
                label = STYLE_LABELS.get(sub.get("style", ""), sub.get("style", ""))
                tfs = ", ".join(sub.get("timeframes", [])) or "n/a"
                text = (
                    "<b>Your ATLAX access is active</b>\n\n"
                    f"Style: <b>{label}</b>\n"
                    f"Timeframes: <b>{tfs}</b>"
                )
            else:
                text = "<b>No active ATLAX access found</b>\n\nUse /subscribe to get started."

        keyboard = [
            [{"text": "📜 Payment History", "callback_data": "payment:history"}],
            [{"text": "↩️ Back", "callback_data": "menu:main"}],
        ]
        if message_id is None:
            telegram_channel.send_reply(chat_id, text)
        else:
            telegram_channel.edit_inline_menu(chat_id, message_id, text, keyboard)

    def _start_payment_flow(
        self,
        chat_id: str,
        username: str,
        method: str,
        telegram_channel,
        db,
        subscription_plan: str = "standard",
    ) -> None:
        payment_service = self._ensure_payment_service(db)
        if payment_service is None:
            telegram_channel.send_reply(
                chat_id,
                "Payments are not configured yet. Please contact the admin.",
            )
            return

        intent, message = payment_service.create_payment_intent(
            chat_id=chat_id,
            username=username,
            method=method,
            subscription_plan=subscription_plan,
        )
        if intent is None:
            telegram_channel.send_reply(chat_id, message)
            return

        keyboard = [[
            {"text": "📤 Submit Payment Proof", "callback_data": f"payment:proof:{method}:{intent.get('reference', '')}"},
            {"text": "🔐 My Access", "callback_data": "payment:status"},
        ], [
            {"text": "↩️ Subscription", "callback_data": "menu:subscribe"},
            {"text": "🏠 Home", "callback_data": "menu:main"},
        ]]
        telegram_channel.send_inline_menu(chat_id, message, keyboard)

    def _show_payment_proof_help(
        self,
        chat_id: str,
        telegram_channel,
        message_id: Optional[int] = None,
        reference: str = "",
    ) -> None:
        crypto_reference = escape(reference) if reference else "ATLAX-CR-XXXX"
        mpesa_reference = escape(reference) if reference else "ATLAX-MP-XXXX"
        reference_line = f"\n\n🔖 Reference: <code>{crypto_reference}</code>" if reference else ""
        text = (
            "📨 <b>Submit Payment Proof</b>\n\n"
            "After your payment is complete, send the payment reference created by ATLAX together with the transaction identifier from your wallet or M-Pesa confirmation SMS.\n\n"
            "<b>Crypto example</b>\n"
            f"<code>/paid {crypto_reference} SOLANA_TRANSACTION_SIGNATURE</code>\n\n"
            "<b>M-Pesa example</b>\n"
            f"<code>/paid {mpesa_reference} MPESA_TRANSACTION_ID</code>\n\n"
            "🛡️ Do not send a wallet seed phrase, private key, M-Pesa PIN, or password. ATLAX activates access only after an administrator verifies the payment."
            + reference_line
        )
        keyboard = [
            [{"text": "💳 Subscription", "callback_data": "menu:subscribe"}],
            [{"text": "🏠 Home", "callback_data": "menu:main"}],
        ]
        if message_id is None:
            telegram_channel.send_inline_menu(chat_id, text, keyboard)
        else:
            telegram_channel.edit_inline_menu(chat_id, message_id, text, keyboard)

    def _show_admin_revenue(self, chat_id: str, telegram_channel, db, message_id: Optional[int] = None) -> None:
        payment_service = self._ensure_payment_service(db)
        if payment_service is None:
            text = "🤖 <b>ATLAX Admin  ·  📈 Revenue Summary</b>\n━━━━━━━━━━━━━━━━━━━━━━━━\n\nPayment configuration is not available."
        else:
            text = payment_service.build_admin_revenue_summary_message()
        keyboard = [
            [
                {"text": "💳 Payment Queue", "callback_data": "admin:payments"},
                {"text": "📜 History", "callback_data": "payment:history"},
            ],
            [{"text": "◀️ Admin Menu", "callback_data": "admin:home"}],
        ]
        self._record_operational_audit(chat_id, "admin_revenue_view")
        if message_id is None:
            telegram_channel.send_inline_menu(chat_id, text, keyboard)
        else:
            telegram_channel.edit_inline_menu(chat_id, message_id, text, keyboard)

    def _show_prop_firm_menu(self, chat_id: str, telegram_channel, message_id: Optional[int] = None) -> None:
        lines = [
            "🤖 <b>ATLAX Admin  ·  🏦 Prop Firm Mode</b>",
            "━━━━━━━━━━━━━━━━━━━━━━━━",
            ""
        ]

        acct_cfg = getattr(self, "_account_mode_cfg", None)
        profiles = getattr(self, "_prop_firm_profiles", [])
        control = self._runtime_store.get("prop_firm.control", {}) or {}
        mode_state = load_account_mode_state(self._runtime_store)
        canonical_mode = mode_state.mode
        use_canonical_state = acct_cfg is not None
        is_enabled = bool(control.get("enabled", getattr(acct_cfg, "enabled", False)))
        is_paused = bool(control.get("paused", False))
        if use_canonical_state:
            status_icon = {
                TradingAccountMode.NORMAL_ACCOUNT: "🟢 Normal account",
                TradingAccountMode.PROP_FIRM_SHADOW: "🧪 Prop-Firm Shadow",
                TradingAccountMode.PROP_FIRM_ENFORCED: "🔐 Prop-Firm Enforced",
                TradingAccountMode.AUTO_ENFORCED: "⚙️ Auto-Enforced",
            }.get(canonical_mode, "❓ Unknown")
        else:
            status_icon = "🟢 Enabled" if is_enabled else "🔴 Disabled (Shadow)"
            if is_paused:
                status_icon += " · ⏸️ Paused"

        lines.append(f"Mode: <b>{status_icon}</b>")
        if use_canonical_state and mode_state.binding is not None:
            lines.append(
                f"MT5 account: <code>{mode_state.binding.login}</code> · "
                f"{escape(mode_state.binding.server)}"
            )
        if use_canonical_state and mode_state.selected_profile_id:
            lines.append(
                f"Selected profile: <code>{escape(mode_state.selected_profile_id)}</code> "
                f"v{escape(mode_state.selected_profile_version)}"
            )
        if use_canonical_state:
            lines.append(
                f"Verification: <b>{escape(mode_state.status.value)}</b> · "
                f"{escape(mode_state.status_reason[:120])}"
            )
        if use_canonical_state and not bool(getattr(acct_cfg, "enabled", False)):
            lines.append("Runtime configuration: <b>disabled</b>")
        lines.append("")

        if profiles:
            lines.append(f"<b>Available Source Profiles ({len(profiles)}):</b>")
            for p in profiles[:15]:
                if isinstance(p, dict):
                    firm = p.get("firm_name", "?")
                    prog = p.get("program_name", "?")
                    pid = p.get("profile_id", "?")
                else:
                    firm = getattr(p, "firm_name", "?")
                    prog = getattr(p, "program_name", "?")
                    pid = getattr(p, "profile_id", "?")
                lines.append(f"• {firm} - {prog} (<code>{pid}</code>)")
            if len(profiles) > 15:
                lines.append(f"<i>...and {len(profiles) - 15} more.</i>")
        else:
            lines.append("<i>No profiles loaded from config/prop_firm_profiles.yaml</i>")

        lines.append("")
        lines.append("<i>Prop Firm integration ensures strict risk limits and loss budgets are respected on funded accounts.</i>")

        keyboard = [
            [{"text": "📚 Profiles", "callback_data": "propfirm:profiles"}],
            [{"text": "🔗 Bind Current MT5", "callback_data": "propfirm:bind"}],
            [{"text": "⚙️ Auto Enforce", "callback_data": "propfirm:auto"}],
            [{"text": "🧪 Shadow Mode", "callback_data": "propfirm:enable"}, {"text": "🟢 Normal", "callback_data": "propfirm:normal"}],
            [{"text": "⏸️ Pause", "callback_data": "propfirm:pause"}],
            [{"text": "◀️ Admin Menu", "callback_data": "admin:home"}],
        ]

        self._record_operational_audit(chat_id, "admin_prop_firm_view")
        text = "\n".join(lines)
        if message_id is None:
            telegram_channel.send_inline_menu(chat_id, text, keyboard)
        else:
            telegram_channel.edit_inline_menu(chat_id, message_id, text, keyboard)

    def _handle_prop_firm_callback(
        self,
        data: str,
        chat_id: str,
        message_id: int,
        telegram_channel,
    ) -> None:
        """Persist admin-selected Prop-Firm control state fail-closed."""
        profiles = list(getattr(self, "_prop_firm_profiles", []) or [])
        state = dict(self._runtime_store.get("prop_firm.control", {}) or {})
        canonical_state = load_account_mode_state(self._runtime_store)
        canonical_profile_id = str(canonical_state.selected_profile_id or "").strip()
        canonical_profile_version = str(canonical_state.selected_profile_version or "").strip()
        if canonical_profile_id and not state.get("selected_profile_id"):
            state["selected_profile_id"] = canonical_profile_id
        if canonical_profile_version and not state.get("selected_profile_version"):
            state["selected_profile_version"] = canonical_profile_version
        if data == "propfirm:profile" or data.startswith("propfirm:profile:"):
            raw_index = data.rsplit(":", 1)[-1]
            if not raw_index.isdigit() or int(raw_index) >= len(profiles):
                telegram_channel.send_reply(chat_id, "That Prop-Firm profile is not available.")
                return
            profile = profiles[int(raw_index)]
            profile_id = profile.get("profile_id") if isinstance(profile, dict) else getattr(profile, "profile_id", "")
            profile_version = profile.get("version") if isinstance(profile, dict) else getattr(profile, "version", "")
            state.update({"selected_profile_id": str(profile_id), "enabled": False, "paused": False})
            self._runtime_store.set("prop_firm.control", state)
            if getattr(self, "_account_mode_cfg", None) is not None:
                try:
                    select_prop_firm_profile(
                        self._runtime_store,
                        profile_id=str(profile_id),
                        profile_version=str(profile_version),
                        actor=f"telegram:{chat_id}",
                    )
                except Exception as exc:  # noqa: BLE001 - callback must remain safe
                    logger.warning(
                        '{"event":"prop_firm_profile_selection_failed","error":"%s"}',
                        str(exc)[:160],
                    )
            self._show_prop_firm_menu(chat_id, telegram_channel, message_id)
            return

        if data == "propfirm:bind":
            if mt5 is None:
                telegram_channel.send_reply(chat_id, "MT5 is not available; the current account cannot be bound.")
                return
            try:
                identity = read_mt5_account_identity(
                    mt5,
                    terminal_instance_id=getattr(
                        getattr(self, "_account_mode_cfg", None),
                        "terminal_instance_id",
                        None,
                    ),
                )
                bind_current_account(
                    self._runtime_store,
                    identity,
                    actor=f"telegram:{chat_id}",
                )
                self._record_operational_audit(
                    chat_id,
                    "prop_firm_account_bound",
                    metadata={
                        "login": identity.login,
                        "server": identity.server,
                        "currency": identity.currency,
                    },
                )
                self._show_prop_firm_menu(chat_id, telegram_channel, message_id)
            except Exception as exc:  # noqa: BLE001 - fail closed
                logger.warning(
                    '{"event":"prop_firm_bind_failed","error":"%s"}',
                    str(exc)[:160],
                )
                telegram_channel.send_reply(
                    chat_id,
                    "The current MT5 account could not be verified, so it was not bound.",
                )
            return

        if data == "propfirm:auto":
            selected = str(
                state.get("selected_profile_id") or canonical_state.selected_profile_id or ""
            ).strip()
            if not profiles or not selected:
                state.update({"enabled": False, "paused": False})
                self._runtime_store.set("prop_firm.control", state)
                telegram_channel.send_reply(
                    chat_id,
                    "Auto-Enforce requires a selected source-backed profile and a bound MT5 account.",
                )
                return
            if getattr(self, "_account_mode_cfg", None) is not None:
                if not bool(getattr(self._account_mode_cfg, "enabled", False)):
                    telegram_channel.send_reply(
                        chat_id,
                        "Prop-Firm runtime is disabled in configuration; Auto-Enforce was not enabled.",
                    )
                    return
                selected_profile = next(
                    (
                        item for item in profiles
                        if str(getattr(item, "profile_id", "")) == selected
                    ),
                    None,
                )
                if selected_profile is None:
                    telegram_channel.send_reply(
                        chat_id,
                        "The selected profile is not a typed source-backed profile.",
                    )
                    return
                try:
                    set_account_mode(
                        self._runtime_store,
                        mode=TradingAccountMode.AUTO_ENFORCED,
                        actor=f"telegram:{chat_id}",
                        selected_profile_id=selected,
                        selected_profile_version=str(getattr(selected_profile, "version", "")),
                    )
                except Exception as exc:  # noqa: BLE001 - callback must remain safe
                    logger.warning(
                        '{"event":"prop_firm_auto_enable_failed","error":"%s"}',
                        str(exc)[:160],
                    )
                    telegram_channel.send_reply(chat_id, "Auto-Enforce was not enabled.")
                    return
            state.update({"enabled": True, "paused": False})
            self._runtime_store.set("prop_firm.control", state)
            self._show_prop_firm_menu(chat_id, telegram_channel, message_id)
            return

        if data == "propfirm:enable":
            selected = str(
                state.get("selected_profile_id") or canonical_state.selected_profile_id or ""
            ).strip()
            if not profiles or not selected:
                state.update({"enabled": False, "paused": False})
                self._runtime_store.set("prop_firm.control", state)
                telegram_channel.send_reply(chat_id, "Cannot enable Prop-Firm mode without a selected verified profile.")
                return
            if getattr(self, "_account_mode_cfg", None) is not None:
                if not bool(getattr(self._account_mode_cfg, "enabled", False)):
                    telegram_channel.send_reply(
                        chat_id,
                        "Prop-Firm runtime is disabled in configuration; no live mode was enabled.",
                    )
                    return
                selected_profile = next(
                    (
                        item for item in profiles
                        if str(getattr(item, "profile_id", "")) == selected
                    ),
                    None,
                )
                if selected_profile is None:
                    telegram_channel.send_reply(
                        chat_id,
                        "The selected profile is not a typed source-backed profile.",
                    )
                    return
                try:
                    set_account_mode(
                        self._runtime_store,
                        mode=TradingAccountMode.PROP_FIRM_SHADOW,
                        actor=f"telegram:{chat_id}",
                        selected_profile_id=selected,
                        selected_profile_version=str(getattr(selected_profile, "version", "")),
                    )
                except Exception as exc:  # noqa: BLE001 - fail closed
                    logger.warning(
                        '{"event":"prop_firm_shadow_enable_failed","error":"%s"}',
                        str(exc)[:160],
                    )
                    telegram_channel.send_reply(chat_id, "Prop-Firm Shadow mode was not enabled.")
                    return
            state.update({"enabled": True, "paused": False})
            self._runtime_store.set("prop_firm.control", state)
            self._show_prop_firm_menu(chat_id, telegram_channel, message_id)
            return

        if data == "propfirm:normal":
            if getattr(self, "_account_mode_cfg", None) is not None:
                set_account_mode(
                    self._runtime_store,
                    mode=TradingAccountMode.NORMAL_ACCOUNT,
                    actor=f"telegram:{chat_id}",
                )
            state.update({"enabled": False, "paused": False})
            self._runtime_store.set("prop_firm.control", state)
            self._show_prop_firm_menu(chat_id, telegram_channel, message_id)
            return

        if data == "propfirm:pause":
            state["paused"] = True
            self._runtime_store.set("prop_firm.control", state)
            self._show_prop_firm_menu(chat_id, telegram_channel, message_id)
            return

        telegram_channel.send_reply(chat_id, "Unknown Prop-Firm operation.")

    def _show_key_level_shadow_report(self, chat_id: str, telegram_channel, message_id: Optional[int] = None) -> None:
        lines = [
            "🤖 <b>ATLAX Admin  ·  🧪 Shadow Report</b>",
            "━━━━━━━━━━━━━━━━━━━━━━━━",
            "",
            "<b>Key Level Evaluator (Shadow)</b>",
            "Status: 🔴 Offline (Not Configured)",
            "",
            "<i>This module is awaiting Phase 3 integration completion.</i>"
        ]
        keyboard = [
            [{"text": "◀️ Admin Menu", "callback_data": "admin:home"}],
        ]

        self._record_operational_audit(chat_id, "admin_shadow_report_view")
        text = "\n".join(lines)
        if message_id is None:
            telegram_channel.send_inline_menu(chat_id, text, keyboard)
        else:
            telegram_channel.edit_inline_menu(chat_id, message_id, text, keyboard)

    def _notify_admin_payment_review(self, intent: dict, telegram_channel, db) -> None:
        payment_service = self._ensure_payment_service(db)
        if payment_service is None:
            return

        admin_chat_id = str(telegram_channel._config.chat_id)
        reference = str(intent.get("reference", "")).strip().upper()
        if not reference or not admin_chat_id:
            logger.error('{"event":"payment_review_notification_skipped","reason":"missing_reference_or_admin"}')
            return

        keyboard = [[
            {"text": "✅ Approve", "callback_data": f"payment:approve:{reference}"},
            {"text": "❌ Reject", "callback_data": f"payment:reject:{reference}"},
        ]]
        telegram_channel.send_inline_menu(
            admin_chat_id,
            payment_service.build_review_message(intent),
            keyboard,
        )

    def _handle_menu_callback(
        self,
        data: str,
        chat_id: str,
        message_id: int,
        telegram_channel,
        username: str,
        db,
    ) -> None:
        """Route Telegram inline buttons without crossing layer boundaries."""
        data = (data or "").strip()

        if data == "admin:home":
            self._show_admin_home(chat_id, telegram_channel, db, message_id)
            return

        if data.startswith("admin:"):
            if not self._is_admin(chat_id):
                self._record_operational_audit(chat_id, "admin_callback", result="denied", metadata={"callback": data[:80]})
                telegram_channel.send_reply(chat_id, "That operation is available to the admin only.")
                return
            if data == "admin:accounts" or data.startswith("admin:accounts:"):
                self._handle_trading_account_callback(
                    data,
                    chat_id,
                    message_id,
                    telegram_channel,
                )
                return
            if data == "admin:payments":
                self._show_admin_payment_queue(chat_id, telegram_channel, db, message_id)
                return
            if data == "admin:revenue":
                self._show_admin_revenue(chat_id, telegram_channel, db, message_id)
                return
            if data == "admin:subscribers":
                self._show_admin_subscribers(chat_id, telegram_channel, db, message_id)
                return
            if data == "admin:health":
                self._show_admin_health(chat_id, telegram_channel, db, message_id)
                return
            if data == "admin:operations_snapshot":
                self._show_operations_snapshot(chat_id, telegram_channel, message_id)
                return
            if data == "admin:execution_quality":
                self._show_admin_execution_quality(chat_id, telegram_channel, message_id)
                return
            if data == "admin:audit":
                self._show_admin_audit(chat_id, telegram_channel, message_id)
                return
            parts = data.split(":", 3)
            if len(parts) == 4 and parts[1] == "sub":
                target = parts[3]
                if parts[2] == "view":
                    self._show_admin_subscriber_detail(chat_id, telegram_channel, db, target, message_id)
                    return
                if parts[2] == "confirm_deactivate":
                    self._show_admin_deactivation_confirmation(chat_id, telegram_channel, db, target, message_id)
                    return
                if parts[2] == "deactivate":
                    self._deactivate_admin_subscriber(chat_id, telegram_channel, db, target, message_id)
                    return
                if parts[2] == "ai_reset":
                    self._admin_reset_subscriber_ai(chat_id, telegram_channel, db, target, message_id)
                    return
                if parts[2] == "ai_block":
                    self._admin_set_subscriber_ai_block(chat_id, telegram_channel, db, target, True, message_id)
                    return
                if parts[2] == "ai_unblock":
                    self._admin_set_subscriber_ai_block(chat_id, telegram_channel, db, target, False, message_id)
                    return
            logger.warning('{"event":"admin_callback_rejected","reason":"unknown_callback"}')
            telegram_channel.send_reply(chat_id, "Unknown admin operation.")
            return

        if data == "nav:profile":
            self._show_user_profile(chat_id, telegram_channel, db, message_id)
            return

        if data == "nav:language":
            telegram_channel.edit_inline_menu(
                chat_id,
                message_id,
                "🌐 <b>Choose the language</b>",
                [
                    [{"text": "🇬🇧 English", "callback_data": "set:language:en:main"}],
                    [{"text": "🇹🇿 Kiswahili", "callback_data": "set:language:sw:main"}],
                    [{"text": "↩️ Back", "callback_data": "menu:main"}],
                ],
            )
            return

        if data.startswith("set:language:"):
            parts = data.split(":")
            language = parts[2].lower() if len(parts) > 2 else "en"
            if language not in {"en", "sw"}:
                telegram_channel.send_reply(chat_id, "Language selection was rejected.")
                return
            if hasattr(db, "set_language"):
                db.set_language(chat_id, language)
            else:
                db.update_settings(chat_id, "language", language)
            self._show_main_menu(chat_id, telegram_channel, db, username)
            return

        if data == "nav:signals":
            self._show_signal_terminal(chat_id, telegram_channel, db, message_id)
            return

        if data == "nav:history":
            self._show_signal_passport_history(chat_id, telegram_channel, db, message_id)
            return

        if data == "nav:weekly":
            self._show_signal_passport_weekly(chat_id, telegram_channel, db, message_id)
            return

        if data.startswith("signals:pairs"):
            parts = data.split(":")
            action = parts[2] if len(parts) > 2 else ""
            page = 0
            if action == "page" and len(parts) > 3 and parts[3].isdigit():
                page = int(parts[3])
            elif action == "toggle" and len(parts) > 3:
                symbol = parts[3].strip().upper()
                if symbol in self._available_symbol_options(chat_id, db):
                    current = [
                        str(value).strip().upper()
                        for value in (db.get_settings(chat_id) or {}).get("symbols", []) or []
                        if str(value).strip()
                    ]
                    if symbol in current:
                        current.remove(symbol)
                    elif len(current) < 12:
                        current.append(symbol)
                    else:
                        telegram_channel.send_reply(chat_id, "You can select up to 12 pairs.")
                        return
                    db.update_settings(chat_id, "symbols", current)
            elif action == "all":
                db.update_settings(chat_id, "symbols", [])
            elif action == "done":
                self._show_signal_terminal(chat_id, telegram_channel, db, message_id)
                return
            self._show_pair_routing_menu(
                chat_id,
                telegram_channel,
                db,
                message_id=message_id,
                page=page,
                callback_prefix="signals:pairs",
                done_callback="signals:refresh",
                back_callback="signals:refresh",
                title="Signal Pair Routing",
            )
            return

        if data in {"signals:refresh", "signals:clear"}:
            if data == "signals:clear":
                self._clear_signal_filter_state(chat_id)
            self._show_signal_terminal(chat_id, telegram_channel, db, message_id)
            return

        if data.startswith("signals:detail:"):
            alert_id = data.split(":", 2)[2].strip()
            self._show_signal_detail(chat_id, telegram_channel, db, alert_id, message_id)
            return

        if data.startswith("signals:filter:"):
            parts = data.split(":", 3)
            if len(parts) != 4:
                logger.warning(
                    '{"event":"telegram_signal_filter_rejected","reason":"malformed_callback"}'
                )
                self._show_signal_terminal(chat_id, telegram_channel, db, message_id)
                return
            kind, value = parts[2].strip().lower(), parts[3].strip().upper()
            if kind not in {"symbol", "timeframe", "confidence"}:
                logger.warning(
                    '{"event":"telegram_signal_filter_rejected","reason":"unknown_filter"}'
                )
                self._show_signal_terminal(chat_id, telegram_channel, db, message_id)
                return
            if kind == "confidence":
                value = value.lower()
            elif value == "ALL":
                value = "all"

            visible = self._visible_signal_records(chat_id)
            journal_entries = self._signal_journal_entries()
            options = self._signal_filter_options(chat_id, db, visible, journal_entries)
            allowed = {"all"} if kind in {"symbol", "timeframe"} else {"any"}
            allowed.update(options[kind + "s"] if kind != "confidence" else options["confidence"])
            if value not in allowed:
                logger.warning(
                    '{"event":"telegram_signal_filter_rejected","reason":"value_not_allowed","kind":"%s"}',
                    kind,
                )
                self._show_signal_terminal(chat_id, telegram_channel, db, message_id)
                return
            self._set_signal_filter_state(chat_id, kind, value)
            logger.info(
                '{"event":"telegram_signal_filter_applied","chat_id":"%s","kind":"%s","value":"%s"}',
                str(chat_id)[:80], kind, value,
            )
            self._show_signal_terminal(chat_id, telegram_channel, db, message_id)
            return

        if data == "nav:help":
            self._show_help_menu(chat_id, telegram_channel, message_id)
            return

        if data == "nav:faq":
            self._show_faq_menu(chat_id, telegram_channel, message_id)
            return

        if data.startswith("nav:faq:page:"):
            raw_page = data.rsplit(":", 1)[-1]
            page = int(raw_page) if raw_page.isdigit() else 0
            self._show_faq_menu(chat_id, telegram_channel, message_id, page=page)
            return

        if data == "nav:about":
            self._show_about_menu(chat_id, telegram_channel, message_id)
            return

        if data == "payment:status":
            self._show_access_status(chat_id, telegram_channel, db, message_id)
            return

        if data == "payment:proof":
            self._show_payment_proof_help(chat_id, telegram_channel, message_id)
            return

        if data == "payment:history":
            if not self._is_admin(chat_id) and not db.is_registered(chat_id):
                self._show_welcome(chat_id, telegram_channel, db)
            else:
                self._show_payment_history(chat_id, telegram_channel, db, message_id)
            return

        if data.startswith("subscribe:plan:"):
            plan = data.rsplit(":", 1)[-1].lower()
            self._show_subscription_menu(
                chat_id, telegram_channel, db, message_id, selected_plan=plan
            )
            return

        if data.startswith("pay:crypto") or data.startswith("pay:mpesa"):
            parts = data.split(":")
            method = parts[1]
            plan = parts[2] if len(parts) > 2 else "standard"
            self._start_payment_flow(
                chat_id,
                username,
                method,
                telegram_channel,
                db,
                subscription_plan=plan,
            )
            return

        if data.startswith("payment:proof:"):
            parts = data.split(":", 3)
            reference = parts[3] if len(parts) == 4 else ""
            self._show_payment_proof_help(
                chat_id, telegram_channel, message_id, reference=reference
            )
            return

        if data.startswith("payment:approve:") or data.startswith("payment:reject:"):
            if not self._is_admin(chat_id):
                telegram_channel.send_reply(chat_id, "You are not authorized to review payments.")
                return

            action, reference = data.split(":", 2)[1:]
            payment_service = self._ensure_payment_service(db)
            if payment_service is None:
                result_message = "Payments are not configured."
                intent = None
            elif action == "approve":
                intent, result_message = payment_service.confirm_payment(
                    reference,
                    confirmed_by=str(chat_id),
                )
                if intent is not None:
                    target_chat_id = str(intent.get("chat_id", ""))
                    sub = db.get_subscriber(target_chat_id) or {}
                    telegram_channel.send_inline_menu(
                        target_chat_id,
                        payment_service.build_activation_message(intent),
                        self._style_choice_keyboard(
                            str(sub.get("style", intent.get("style", ""))),
                            "menu:main",
                        ),
                    )
            else:
                intent, result_message = payment_service.reject_payment(
                    reference,
                    rejected_by=str(chat_id),
                    reason="Rejected by admin",
                )
                if intent is not None:
                    telegram_channel.send_reply(
                        str(intent.get("chat_id", "")),
                        payment_service.build_rejection_message(intent, "Rejected by admin"),
                    )

            self._record_operational_audit(
                chat_id,
                f"payment_{action}",
                target_chat_id=str(intent.get("chat_id", "")) if intent else "",
                result="success" if intent is not None else "failed",
                metadata={
                    "reference": reference,
                    "status": intent.get("status", "unknown") if intent else "unknown",
                },
            )

            text = f"<b>Payment review</b>\n\nReference: <code>{reference}</code>\n\n{result_message}"
            keyboard = [[{"text": "↩️ Back to Menu", "callback_data": "menu:main"}]]
            if message_id is None:
                telegram_channel.send_reply(chat_id, text)
            else:
                telegram_channel.edit_inline_menu(chat_id, message_id, text, keyboard)
            return

        if data == "menu:main":
            self._show_main_menu(chat_id, telegram_channel, db, username)
            return

        if data == "menu:subscriber_ai":
            self._show_subscriber_ai_menu(chat_id, telegram_channel, db, message_id)
            return

        if data.startswith("subai:"):
            self._handle_subscriber_ai_callback(data, chat_id, message_id, telegram_channel, db)
            return

        admin_only = {"menu:stats", "menu:ai", "menu:risk", "menu:system"}
        if data in admin_only and not self._is_admin(chat_id):
            telegram_channel.send_reply(chat_id, "That menu is available to the admin only.")
            return

        if data == "menu:subscribe":
            self._show_subscription_menu(chat_id, telegram_channel, db, message_id)
        elif data == "menu:market":
            telegram_channel.edit_inline_menu(
                chat_id,
                message_id,
                "<b>Market and News</b>",
                [
                    [{"text": "🔥 Heatmap",        "callback_data": "mkt:heatmap"}],
                    [{"text": "🔗 Correlations",   "callback_data": "mkt:correlation"}],
                    [{"text": "📅 Economic News",  "callback_data": "mkt:news"}],
                    [{"text": "🏠 Main Menu",       "callback_data": "menu:main"}],
                ],
            )
        elif data == "menu:risk":
            self._handle_risk_callback("risk:view", chat_id, message_id, telegram_channel)
        elif data == "menu:stats":
            telegram_channel.edit_inline_menu(
                chat_id,
                message_id,
                "<b>Performance Stats</b>",
                [
                    [{"text": "📅 Today", "callback_data": "stats:today"}],
                    [{"text": "📆 Last 7 Days", "callback_data": "stats:7d"}],
                    [{"text": "🗓️ Last 30 Days", "callback_data": "stats:30d"}],
                    [{"text": "📚 All Time", "callback_data": "stats:all"}],
                    [{"text": "🏠 Main Menu", "callback_data": "menu:main"}],
                ],
            )
        elif data == "menu:ai":
            telegram_channel.edit_inline_menu(
                chat_id,
                message_id,
                "<b>ATLAX AI Advisory</b>",
                [
                    [{"text": "📈 Win Rate", "callback_data": "ai:winrate"}],
                    [{"text": "🏆 Best Pair", "callback_data": "ai:best_pair"}],
                    [{"text": "📉 Worst Pair", "callback_data": "ai:worst_pair"}],
                    [{"text": "💡 Improvement Tip", "callback_data": "ai:tips"}],
                    [{"text": "📝 Trading Plan Notes", "callback_data": "ai:notes"}],
                    [{"text": "🏦 Prop-Firm Rules", "callback_data": "ai:prop_firm"}],
                    [{"text": "🏠 Main Menu", "callback_data": "menu:main"}],
                ],
            )
        elif data == "menu:system":
            telegram_channel.edit_inline_menu(
                chat_id,
                message_id,
                "<b>Alerts and System</b>",
                [
                    [{"text": "🩺 System Status", "callback_data": "sys:status"}],
                    [{"text": "🛠️ Admin Operations", "callback_data": "admin:home"}],
                    [{"text": "📊 Daily Report", "callback_data": "sys:report"}],
                    [{"text": "🕘 Recent Activity", "callback_data": "sys:alerts"}],
                    [{"text": "🏠 Main Menu", "callback_data": "menu:main"}],
                ],
            )
        elif data == "menu:settings":
            self._show_settings_home(chat_id, message_id, telegram_channel)
        elif data in {"propfirm:menu", "propfirm:profiles", "prop_firm:menu"}:
            if not self._is_admin(chat_id):
                telegram_channel.send_reply(chat_id, "That menu is available to the admin only.")
            else:
                self._show_prop_firm_menu(chat_id, telegram_channel, message_id)
        elif data.startswith("propfirm:"):
            if not self._is_admin(chat_id):
                telegram_channel.send_reply(chat_id, "That menu is available to the admin only.")
            else:
                self._handle_prop_firm_callback(data, chat_id, message_id, telegram_channel)
        elif data.startswith("mkt:"):
            self._handle_market_callback(data, chat_id, message_id, telegram_channel)
        elif data.startswith("risk:"):
            self._handle_risk_callback(data, chat_id, message_id, telegram_channel)
        elif data.startswith("stats:"):
            self._handle_stats_callback(data, chat_id, message_id, telegram_channel)
        elif data.startswith("ai:"):
            self._handle_ai_callback(data, chat_id, message_id, telegram_channel)
        elif data.startswith("sys:"):
            self._handle_system_callback(data, chat_id, message_id, telegram_channel)
        elif data.startswith("set:"):
            self._handle_settings_callback(data, chat_id, message_id, telegram_channel)

    def _handle_market_callback(self, data: str, chat_id: str, message_id: int, telegram_channel) -> None:
        """Handle market context and news requests."""
        back_btn = [[{"text": "↩️ Back to Market", "callback_data": "menu:market"},
                     {"text": "🏠 Main Menu",      "callback_data": "menu:main"}]]

        if data == "mkt:heatmap":
            try:
                state = self._runtime_store.get("heatmap.state", {})

                if not state:
                    text = "🔥 <b>Active CRT Zones</b>\n\nNo heatmap data available yet."
                else:
                    lines = []
                    for key_str, info in state.items():
                        symbol = info.get("symbol", key_str.split('_')[0])
                        tf     = info.get("timeframe", "")
                        bias   = info.get("bias", "")
                        dist   = info.get("distance_pips", 0)
                        status = info.get("status", "")

                        if status == "Sweeping Now":
                            icon = "🔥"
                        elif "Bullish" in bias:
                            icon = "🟢"
                        elif "Bearish" in bias:
                            icon = "🔴"
                        else:
                            icon = "⚪"

                        tf_suffix = f" ({tf})" if tf else ""
                        lines.append(
                            f"{icon} <b>{symbol}{tf_suffix}</b>: {bias} "
                            f"— <i>{dist} pips to sweep</i>"
                        )

                    if lines:
                        text = "🔥 <b>Active CRT Heatmap Zones</b>\n\n" + "\n".join(lines)
                    else:
                        text = "🔥 <b>Active CRT Heatmap Zones</b>\n\nNo valid CRT zones forming right now."

                telegram_channel.edit_inline_menu(chat_id, message_id, text, back_btn)

            except Exception as exc:
                telegram_channel.edit_inline_menu(chat_id, message_id, f"⚠️ Error loading heatmap: {exc}", back_btn)

        elif data == "mkt:correlation":
            try:
                corr_data = self._runtime_store.get("correlation.state", {})

                if not corr_data:
                    text = (
                        "🔗 <b>Pair Correlations</b>\n\n"
                        "⏳ Correlation data is being built from D1 price history.\n"
                        "It will be ready after the scanner processes a few D1 candles.\n"
                        "Check back in a few minutes."
                    )
                    telegram_channel.edit_inline_menu(chat_id, message_id, text, back_btn)
                    return

                pairs = corr_data.get("pairs", [])
                note  = corr_data.get("note", "")

                strong_pos  = [p for p in pairs if p["coefficient"] >= 0.70]
                mod_pos     = [p for p in pairs if 0.30 <= p["coefficient"] < 0.70]
                mod_neg     = [p for p in pairs if -0.70 < p["coefficient"] <= -0.30]
                strong_neg  = [p for p in pairs if p["coefficient"] <= -0.70]

                def fmt(p: dict) -> str:
                    coef = p["coefficient"]
                    em   = p.get("emoji", "")
                    a, b = p["pair_a"], p["pair_b"]
                    # Positive: both up or both down; Negative: one up, other down
                    if coef >= 0:
                        return f"{em} <b>{a}</b> ↔ <b>{b}</b>  <code>{coef:+.2f}</code>"
                    else:
                        return f"📈📉 <b>{a}</b> ↔ <b>{b}</b>  <code>{coef:+.2f}</code>"

                text = "🔗 <b>Forex Pair Correlations</b>\n"
                text += f"<i>{note}</i>\n\n"

                if strong_pos:
                    text += "🟢 <b>Strong Positive</b> (both move together)\n"
                    text += "\n".join(fmt(p) for p in strong_pos) + "\n\n"

                if mod_pos:
                    text += "🟡 <b>Moderate Positive</b>\n"
                    text += "\n".join(fmt(p) for p in mod_pos) + "\n\n"

                if mod_neg:
                    text += "🟠 <b>Moderate Negative</b> (tend to oppose)\n"
                    text += "\n".join(fmt(p) for p in mod_neg) + "\n\n"

                if strong_neg:
                    text += "🔴 <b>Strong Negative</b> (move inversely)\n"
                    text += "\n".join(fmt(p) for p in strong_neg)

                if not pairs:
                    text += "⏳ Collecting price data — check back after a few scan cycles."

                telegram_channel.edit_inline_menu(chat_id, message_id, text, back_btn)

            except Exception as exc:
                telegram_channel.edit_inline_menu(chat_id, message_id, f"⚠️ Error computing correlations: {exc}", back_btn)

        elif data == "mkt:news":
            telegram_channel.edit_inline_menu(chat_id, message_id, "📅 <i>Checking calendar...</i>", [])
            try:
                from core.analytics.economic_calendar import get_events_near_time
                from datetime import datetime, timedelta, timezone
                
                # Check next 4 hours (center is 2 hours from now, window is 120 mins)
                now_utc = datetime.now(timezone.utc)
                center_time = now_utc + timedelta(hours=2)
                
                events = get_events_near_time(dt=center_time, window_minutes=120, min_impact="High")
                
                if not events:
                    text = "📅 <b>Upcoming News (4H)</b>\n\nNo high-impact events in the next 4 hours."
                else:
                    lines = []
                    for ev in events:
                        mins = ev.get("minutes_from_trade", 0)
                        # We used a future center_time, so true minutes from now:
                        true_mins_from_now = mins + 120
                        
                        time_str = "Happening Now!" if -15 <= true_mins_from_now <= 15 else f"In {true_mins_from_now} mins"
                        if true_mins_from_now < -15:
                            time_str = f"{abs(true_mins_from_now)} mins ago"

                        lines.append(f"🔴 <b>{ev['currency']}</b>: {ev['title']} (<i>{time_str}</i>)")
                        
                    text = "📅 <b>Upcoming News (Next 4 Hours)</b>\n\n" + "\n".join(lines)
                
                telegram_channel.edit_inline_menu(chat_id, message_id, text, back_btn)

            except Exception as exc:
                telegram_channel.edit_inline_menu(chat_id, message_id, f"⚠️ Error fetching news: {exc}", back_btn)

    def _handle_risk_callback(self, data: str, chat_id: str, message_id: int, telegram_channel) -> None:
        """Handle risk management menu logic (live PnL and Emergency Stop)."""
        back_btn = [[{"text": "↩️ Back to Risk", "callback_data": "menu:risk"},
                     {"text": "🏠 Main Menu",    "callback_data": "menu:main"}]]

        if data == "risk:pause":
            self._is_paused = True
            logger.warning('{"event":"emergency_stop_triggered"}')
        elif data == "risk:resume":
            self._is_paused = False
            logger.info('{"event":"scanner_resumed"}')

        try:
            import MetaTrader5 as mt5
            account_info = mt5.account_info()
            if not account_info:
                text = "⚠️ MT5 not connected."
            else:
                balance = Decimal(str(account_info.balance))
                daily_pnl, daily_pnl_available = self._get_atlax_daily_pnl()
                max_loss_pct = self._risk_cfg.max_daily_loss_pct
                loss_limit = balance * Decimal(str(max_loss_pct / 100))
                
                # Active drawdown circuit breaker status
                cb_triggered = daily_pnl_available and daily_pnl <= -loss_limit
                if cb_triggered:
                    self._is_paused = True # enforce pause if circuit breaker hit
                
                status_text = "🛑 PAUSED (Emergency Stop)" if self._is_paused else "✅ ACTIVE"
                if cb_triggered:
                    status_text = "🚨 PAUSED (Circuit Breaker Triggered)"
                elif not daily_pnl_available:
                    status_text = "⚠️ PNL DATA UNAVAILABLE (Entries Blocked)"

                daily_pnl_text = f"${daily_pnl:,.2f}" if daily_pnl_available else "UNKNOWN"

                text = (
                    f"🛡️ <b>Risk Management (Admin)</b>\n\n"
                    f"<b>Status:</b> {status_text}\n"
                    f"<b>Account Balance:</b> ${balance:,.2f}\n"
                    f"<b>Daily Realized PnL:</b> {daily_pnl_text}\n"
                    f"<b>Max Daily Loss Limit:</b> -${loss_limit:,.2f} ({max_loss_pct}%)\n"
                )

                # Dynamic buttons based on state
                action_btn = []
                if self._is_paused:
                    action_btn = [{"text": "▶️ Resume Trading", "callback_data": "risk:resume"}]
                else:
                    action_btn = [{"text": "🛑 EMERGENCY STOP", "callback_data": "risk:pause"}]

                back_btn = [action_btn, [{"text": "🏠 Main Menu", "callback_data": "menu:main"}]]

            telegram_channel.edit_inline_menu(chat_id, message_id, text, back_btn)

        except Exception as exc:
            telegram_channel.edit_inline_menu(chat_id, message_id, f"⚠️ Error loading risk: {exc}", back_btn)

    def _handle_stats_callback(self, data: str, chat_id: str, message_id: int, telegram_channel) -> None:
        """Fetch stats from MCP and update the menu message with results."""
        from core.analytics.mcp_server import get_daily_performance, get_historical_performance
        back_btn = [[{"text": "↩️ Back to Stats", "callback_data": "menu:stats"},
                     {"text": "🏠 Main Menu",    "callback_data": "menu:main"}]]
        try:
            if data == "stats:today":
                perf = get_daily_performance(self._analytics_cfg.db_path)
                label = "Today"
            elif data == "stats:7d":
                perf = get_historical_performance(self._analytics_cfg.db_path, days=7)
                label = "Last 7 Days"
            elif data == "stats:30d":
                perf = get_historical_performance(self._analytics_cfg.db_path, days=30)
                label = "Last 30 Days"
            elif data == "stats:learning":
                import subprocess, html
                try:
                    res = subprocess.run(["python", "scripts/generate_learning_report.py"], capture_output=True, text=True, check=True)
                    text = f"<pre>\n{html.escape(res.stdout)}\n</pre>"
                    telegram_channel.edit_inline_menu(chat_id, message_id, text, back_btn)
                except Exception as e:
                    telegram_channel.edit_inline_menu(chat_id, message_id, f"Error generating report: {e}", back_btn)
                return
            else:
                perf = get_historical_performance(self._analytics_cfg.db_path, days=365)
                label = "All Time (1yr)"

            trades = perf.get("total_trades", 0)
            if trades == 0:
                text = f"📊 <b>{label}</b>\n\nNo closed trades recorded in this period."
            else:
                wr  = perf.get("win_rate_pct", 0.0)
                pnl = perf.get("total_pnl", 0.0)
                win = perf.get("winners", 0)
                los = perf.get("losers", 0)
                pnl_str = f"+${pnl:.2f}" if pnl >= 0 else f"-${abs(pnl):.2f}"
                text = (
                    f"📊 <b>Performance — {label}</b>\n\n"
                    f"<b>Win Rate:</b> {wr:.1f}%\n"
                    f"<b>Total Trades:</b> {trades} ({win}W / {los}L)\n"
                    f"<b>Total PnL:</b> {pnl_str}\n\n"
                    f"<i>Advisory only.</i>"
                )
            telegram_channel.edit_inline_menu(chat_id, message_id, text, back_btn)
        except Exception as exc:
            telegram_channel.edit_inline_menu(chat_id, message_id, f"⚠️ Error fetching stats: {exc}", back_btn)

    def _handle_ai_callback(self, data: str, chat_id: str, message_id: int, telegram_channel) -> None:
        """Map quick AI buttons to canned questions through the advisory engine."""
        question_map = {
            "ai:winrate":   "What is my overall win rate across all pairs in the last 30 days?",
            "ai:best_pair": "Which currency pair has been my most profitable in the last 30 days?",
            "ai:worst_pair":"Which currency pair has been my worst performing in the last 30 days?",
            "ai:tips":      "Based on my last 30 days of trades, what is the one most important thing I should improve?",
            "ai:notes":     "Based on my trader notes, have I been following my trading plan this week?",
        }
        question = question_map.get(data, "Give me a summary of my recent trading performance.")
        back_btn = [[{"text": "↩️ Back to AI",  "callback_data": "menu:ai"},
                     {"text": "🏠 Main Menu",  "callback_data": "menu:main"}]]

        # Show loading state
        telegram_channel.edit_inline_menu(
            chat_id, message_id,
            "🤖 <i>Analysing your data...</i>", []
        )

        if self._advisory_engine is None:
            telegram_channel.edit_inline_menu(chat_id, message_id, "⚠️ AI advisory is not configured.", back_btn)
            return

        try:
            answer = self._advisory_engine.answer_question(question)
            text = answer if answer else "🤖 <b>ATLAX AI</b>\n\nNo data available yet."
        except Exception as exc:
            text = f"⚠️ AI error: {exc}"
        telegram_channel.edit_inline_menu(chat_id, message_id, text, back_btn)

    def _handle_system_callback(self, data: str, chat_id: str, message_id: int, telegram_channel) -> None:
        """Handle system-level menu actions."""
        back_btn = [[{"text": "↩️ Back to System", "callback_data": "menu:system"},
                     {"text": "🏠 Main Menu",      "callback_data": "menu:main"}]]

        if data == "sys:status":
            from datetime import datetime, timezone
            now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
            text = (
                f"✅ <b>ATLAX Scanner — Online</b>\n\n"
                f"<b>Cycle:</b> {self._cycle_count}\n"
                f"<b>Instruments:</b> {len(self._market_cfg.instruments)}\n"
                f"<b>Profiles:</b> {len(self._strategy_cfg.active_profiles)}\n"
                f"<b>AI:</b> {'Active (Gemini+Groq)' if self._advisory_engine else 'Disabled'}\n"
                f"<b>Checked at:</b> {now}"
            )
            telegram_channel.edit_inline_menu(chat_id, message_id, text, back_btn)

        elif data == "sys:report":
            telegram_channel.edit_inline_menu(
                chat_id, message_id, "📈 <i>Generating AI report...</i>", []
            )
            if self._advisory_engine:
                report = self._advisory_engine.generate_daily_report()
                text = report or "No trade data available for a report today."
            else:
                text = "⚠️ AI advisory is not configured."
            telegram_channel.edit_inline_menu(chat_id, message_id, text, back_btn)

        elif data == "sys:alerts":
            from core.analytics.mcp_server import get_daily_performance
            try:
                perf = get_daily_performance(self._analytics_cfg.db_path)
                by_sym = perf.get("by_symbol", {})
                if by_sym:
                    lines = [f"• <b>{s}</b>: {v['total']} trade(s), {v['win_rate']:.0f}% WR" for s, v in by_sym.items()]
                    text = "🔔 <b>Today's Activity by Symbol</b>\n\n" + "\n".join(lines)
                else:
                    text = "🔔 <b>Recent Alerts</b>\n\nNo activity recorded today."
            except Exception as exc:
                text = f"⚠️ Error: {exc}"
            telegram_channel.edit_inline_menu(chat_id, message_id, text, back_btn)

    def _show_settings_home(self, chat_id: str, message_id: int, telegram_channel) -> None:
        """Show the main settings menu with current values inline."""
        from core.alerts.subscriber_db import STYLE_LABELS
        db = self._get_subscriber_db()
        sub = db.get_subscriber(chat_id)

        if not sub:
            telegram_channel.edit_inline_menu(
                chat_id, message_id,
                "⚠️ You are not registered.\n\nUse /start to get access.",
                [[{"text": "🏠 Main Menu", "callback_data": "menu:main"}]]
            )
            return

        settings = sub.get("settings", {})
        symbols = settings.get("symbols", []) or []
        syms   = ", ".join(symbols) if symbols else "All configured pairs"
        conf   = settings.get("min_confidence", "Profile Default")
        notif  = settings.get("notifications", "all")
        style  = sub.get("style", "day_trader")
        plan   = str(sub.get("subscription_plan", "standard")).lower()

        notif_label = {"all": "🔔 All Updates", "signals_only": "📊 Signals Only", "minimal": "🧙 Minimal"}.get(notif, notif)
        conf_label  = f"{conf}%" if isinstance(conf, int) else str(conf)
        style_label = STYLE_LABELS.get(style, style)

        text = (
            f"⚙️ <b>My Settings</b>\n\n"
            f"🏃 <b>Trading Style:</b> {style_label}\n"
            f"📈 <b>Symbol Filter:</b> {syms}\n"
            f"💯 <b>Min Confidence:</b> {conf_label}\n"
            f"🔔 <b>Notifications:</b> {notif_label}"
        )
        if plan != "premium":
            text += "\n\n⭐ <b>Premium Plan Only:</b> Premium Confluence is available after upgrading."
        keyboard = [
            [{"text": "🏃 Trading Style",      "callback_data": "set:menu_style"}],
            [{"text": "📈 Symbol Filter",      "callback_data": "set:menu_symbols"}],
            [{"text": "💯 Min Confidence",    "callback_data": "set:menu_confidence"}],
            [{"text": "🔔 Notifications",      "callback_data": "set:menu_notif"}],
            [{"text": "🎯 Quality Filter",     "callback_data": "set:menu_quality"}],
            [{"text": "📍 Key-Level Filter",   "callback_data": "set:menu_key_level"}],
            [{"text": "🏠 Main Menu",          "callback_data": "menu:main"}],
        ]
        if plan == "premium":
            keyboard.insert(-1, [{"text": "💎 Premium Confluence", "callback_data": "set:menu_confluence"}])
        telegram_channel.edit_inline_menu(chat_id, message_id, text, keyboard)

    def _show_quality_filter_menu(self, chat_id: str, message_id: int, telegram_channel) -> None:
        telegram_channel.edit_inline_menu(
            chat_id,
            message_id,
            "🎯 <b>Quality Filter</b>\n\nChoose whether to receive only high-quality setups.",
            [
                [{"text": "✅ High-quality setups only", "callback_data": "set:quality_on"}],
                [{"text": "⚪ All qualifying setups", "callback_data": "set:quality_off"}],
                [{"text": "↩️ Back to Settings", "callback_data": "menu:settings"}],
            ],
        )

    def _show_key_level_filter_menu(self, chat_id: str, message_id: int, telegram_channel) -> None:
        telegram_channel.edit_inline_menu(
            chat_id,
            message_id,
            "📍 <b>Key-Level Filter</b>\n\nReceive only setups that form at an approved higher-timeframe key level.",
            [
                [{"text": "✅ Key-level setups only", "callback_data": "set:key_level_on"}],
                [{"text": "⚪ All qualifying setups", "callback_data": "set:key_level_off"}],
                [{"text": "↩️ Back to Settings", "callback_data": "menu:settings"}],
            ],
        )

    def _show_confluence_filter_menu(self, chat_id: str, message_id: int, telegram_channel) -> None:
        db = self._get_subscriber_db()
        sub = db.get_subscriber(chat_id) or {}
        if str(sub.get("subscription_plan", "standard")).lower() != "premium":
            telegram_channel.edit_inline_menu(
                chat_id,
                message_id,
                "💎 <b>Premium Plan Only</b>\n\nThis filter is available on an active Premium Plan.",
                [[{"text": "↩️ Back to Settings", "callback_data": "menu:settings"}]],
            )
            return
        telegram_channel.edit_inline_menu(
            chat_id,
            message_id,
            "💎 <b>Premium Confluence</b>\n\nReceive only setups that satisfy the Premium confluence route.",
            [
                [{"text": "✅ Premium confluence setups only", "callback_data": "set:confluence_on"}],
                [{"text": "⚪ Standard quality route", "callback_data": "set:confluence_off"}],
                [{"text": "↩️ Back to Settings", "callback_data": "menu:settings"}],
            ],
        )

    def _show_confidence_choice_menu(self, chat_id: str, telegram_channel, db, message_id: int) -> None:
        settings = db.get_settings(chat_id) or {}
        current = settings.get("min_confidence")
        current_label = f"{current}%" if current is not None else "Profile Default (off)"
        choices = [
            ("60%+", "set:conf_60", 60),
            ("65%+", "set:conf_65", 65),
            ("70%+ (Recommended)", "set:conf_70", 70),
            ("75%+", "set:conf_75", 75),
            ("80%+", "set:conf_80", 80),
        ]
        keyboard = []
        for label, callback, value in choices:
            tick = " [saved]" if current == value else ""
            keyboard.append([{"text": f"{label}{tick}", "callback_data": callback}])
        keyboard.extend([
            [{"text": "⚙️ Profile Default (off)" + (" [saved]" if current is None else ""), "callback_data": "set:conf_off"}],
            [{"text": "↩️ Back to Settings", "callback_data": "menu:settings"}],
        ])
        telegram_channel.edit_inline_menu(
            chat_id,
            message_id,
            f"Minimum Confidence\n\nCurrent: {current_label}\n\nOnly receive signals at or above this score:",
            keyboard,
        )

    def _show_notification_choice_menu(self, chat_id: str, telegram_channel, db, message_id: int) -> None:
        current = str((db.get_settings(chat_id) or {}).get("notifications", "all"))
        choices = [
            ("All Updates (Signal + TP1/2 + SL)", "set:notif_all", "all"),
            ("Signals Only", "set:notif_signals", "signals_only"),
            ("Minimal (Signal + SL only)", "set:notif_minimal", "minimal"),
        ]
        keyboard = [
            [{"text": f"{label}{' [saved]' if current == value else ''}", "callback_data": callback}]
            for label, callback, value in choices
        ]
        keyboard.append([{"text": "↩️ Back to Settings", "callback_data": "menu:settings"}])
        telegram_channel.edit_inline_menu(
            chat_id,
            message_id,
            f"Notification Mode\n\nCurrent: {current}\n\nChoose what updates you receive:",
            keyboard,
        )

    def _handle_settings_callback(self, data: str, chat_id: str, message_id: int, telegram_channel) -> None:
        """Handle settings sub-menu actions — fully interactive, no text commands needed."""
        db = self._get_subscriber_db()
        back_settings = [[{"text": "↩️ Back to Settings", "callback_data": "menu:settings"}]]

        sub = db.get_subscriber(chat_id)
        if not sub:
            telegram_channel.edit_inline_menu(chat_id, message_id, "⚠️ You are not registered. Use /start.", back_settings)
            return

        if data == "set:menu_quality":
            self._show_quality_filter_menu(chat_id, message_id, telegram_channel)
            return

        if data in {"set:quality_on", "set:quality_off"}:
            enabled = data.endswith("_on")
            if db.update_settings(chat_id, "high_quality_only", enabled):
                telegram_channel.send_reply(
                    chat_id,
                    "Saved: high-quality setups only is now " + ("on." if enabled else "off."),
                )
            self._show_quality_filter_menu(chat_id, message_id, telegram_channel)
            return

        if data == "set:menu_key_level":
            self._show_key_level_filter_menu(chat_id, message_id, telegram_channel)
            return

        if data in {"set:key_level_on", "set:key_level_off"}:
            enabled = data.endswith("_on")
            if db.update_settings(chat_id, "key_level_only", enabled):
                telegram_channel.send_reply(
                    chat_id,
                    "Saved: key-level setups only is now " + ("on." if enabled else "off."),
                )
            self._show_key_level_filter_menu(chat_id, message_id, telegram_channel)
            return

        if data == "set:menu_confluence":
            self._show_confluence_filter_menu(chat_id, message_id, telegram_channel)
            return

        if data in {"set:confluence_on", "set:confluence_off"}:
            if str(sub.get("subscription_plan", "standard")).lower() != "premium":
                telegram_channel.send_reply(chat_id, "This setting requires an active Premium Plan.")
                return
            enabled = data.endswith("_on")
            if db.update_settings(chat_id, "confluence_only", enabled):
                telegram_channel.send_reply(
                    chat_id,
                    "Saved: premium confluence setups only is now " + ("on." if enabled else "off."),
                )
            self._show_confluence_filter_menu(chat_id, message_id, telegram_channel)
            return

        # ── Style selection menu ─────────────────────────────────────────
        if data == "set:menu_style":
            self._show_style_choice_menu(
                chat_id,
                telegram_channel,
                db,
                message_id,
                back_callback="menu:settings",
            )

        elif data in ("set:style_scalper", "set:style_day", "set:style_swing", "set:style_all"):
            style_map = {
                "set:style_scalper": "scalper",
                "set:style_day": "day_trader",
                "set:style_swing": "swing_trader",
                "set:style_all": "all_styles",
            }
            style = style_map[data]
            if db.update_style(chat_id, style):
                telegram_channel.send_reply(chat_id, "Saved: trading style updated.")
                self._record_operational_audit(chat_id, "profile_style_update", metadata={"style": style})
            self._show_style_choice_menu(
                chat_id, telegram_channel, db, message_id, back_callback="menu:settings"
            )

        # ── Symbol filter menu ───────────────────────────────────────────
        elif data == "set:menu_symbols":
            self._show_pair_routing_menu(
                chat_id,
                telegram_channel,
                db,
                message_id=message_id,
                callback_prefix="set:sym",
                done_callback="set:sym:done",
                back_callback="menu:settings",
                title="Pair Routing",
            )

        elif data.startswith("set:sym:"):
            parts = data.split(":")
            action = parts[2] if len(parts) > 2 else ""
            page = int(parts[3]) if action == "page" and len(parts) > 3 and parts[3].isdigit() else 0
            if action == "toggle" and len(parts) > 3:
                symbol = parts[3].strip().upper()
                if symbol in self._available_symbol_options(chat_id, db):
                    current = [
                        str(value).strip().upper()
                        for value in (db.get_settings(chat_id) or {}).get("symbols", []) or []
                        if str(value).strip()
                    ]
                    if symbol in current:
                        current.remove(symbol)
                    elif len(current) < 12:
                        current.append(symbol)
                    else:
                        telegram_channel.send_reply(chat_id, "You can select up to 12 pairs.")
                        return
                    db.update_settings(chat_id, "symbols", current)
            elif action == "all":
                db.update_settings(chat_id, "symbols", [])
            elif action == "done":
                self._show_settings_home(chat_id, message_id, telegram_channel)
                return
            self._show_pair_routing_menu(
                chat_id,
                telegram_channel,
                db,
                message_id=message_id,
                page=page,
                callback_prefix="set:sym",
                done_callback="set:sym:done",
                back_callback="menu:settings",
                title="Pair Routing",
            )

        elif data.startswith("set:sym_"):
            sym_map = {
                "set:sym_all":     [],
                "set:sym_xauusd":  ["XAUUSD"],
                "set:sym_gbpusd":  ["GBPUSD"],
                "set:sym_eurusd":  ["EURUSD"],
                "set:sym_usdjpy":  ["USDJPY"],
                "set:sym_xau_gbp": ["XAUUSD", "GBPUSD"],
                "set:sym_eur_gbp": ["EURUSD", "GBPUSD"],
            }
            symbols = sym_map.get(data, [])
            if db.update_settings(chat_id, "symbols", symbols):
                telegram_channel.send_reply(chat_id, "Saved: symbol filter updated.")
                self._record_operational_audit(chat_id, "profile_symbols_update", metadata={"symbols": symbols})
            self._show_pair_routing_menu(
                chat_id,
                telegram_channel,
                db,
                message_id=message_id,
                callback_prefix="set:sym",
                done_callback="set:sym:done",
                back_callback="menu:settings",
                title="Pair Routing",
            )

        # ── Confidence filter menu ───────────────────────────────────────
        elif data == "set:menu_confidence":
            self._show_confidence_choice_menu(chat_id, telegram_channel, db, message_id)

        elif data == "set:menu_confidence_legacy":
            curr = sub.get("settings", {}).get("min_confidence", "Profile Default")
            curr_label = f"{curr}%" if isinstance(curr, int) else str(curr)
            telegram_channel.edit_inline_menu(
                chat_id, message_id,
                f"💯 <b>Minimum Confidence</b>\n\n<b>Current:</b> {curr_label}\n\nOnly receive signals at or above this score:",
                [
                    [{"text": "🔵 60%+",              "callback_data": "set:conf_60"},
                     {"text": "🟡 65%+",              "callback_data": "set:conf_65"}],
                    [{"text": "🟢 70%+ (Recommended)","callback_data": "set:conf_70"}],
                    [{"text": "🟠 75%+",              "callback_data": "set:conf_75"},
                     {"text": "🔴 80%+",              "callback_data": "set:conf_80"}],
                    [{"text": "✴️ Profile Default (off)", "callback_data": "set:conf_off"}],
                    [{"text": "↩️ Back", "callback_data": "menu:settings"}],
                ]
            )

        elif data.startswith("set:conf_"):
            val_str = data.replace("set:conf_", "")
            allowed_confidence = {"60", "65", "70", "75", "80", "off"}
            if val_str not in allowed_confidence:
                telegram_channel.send_reply(chat_id, "Confidence change rejected.")
                return
            conf = None if val_str == "off" else int(val_str)
            if db.update_settings(chat_id, "min_confidence", conf):
                telegram_channel.send_reply(chat_id, "Saved: confidence preference updated.")
                self._record_operational_audit(chat_id, "profile_confidence_update", metadata={"min_confidence": conf})
            self._show_confidence_choice_menu(chat_id, telegram_channel, db, message_id)

        # ── Notification mode menu ───────────────────────────────────────
        elif data == "set:menu_notif":
            self._show_notification_choice_menu(chat_id, telegram_channel, db, message_id)

        elif data == "set:menu_notif_legacy":
            curr = sub.get("settings", {}).get("notifications", "all")
            all_tick     = " ✅" if curr == "all"          else ""
            sig_tick     = " ✅" if curr == "signals_only" else ""
            min_tick     = " ✅" if curr == "minimal"      else ""
            telegram_channel.edit_inline_menu(
                chat_id, message_id,
                "🔔 <b>Notification Mode</b>\n\nChoose what updates you receive:",
                [
                    [{"text": f"🔔 All Updates (Signal + TP1/2 + SL){all_tick}", "callback_data": "set:notif_all"}],
                    [{"text": f"📊 Signals Only{sig_tick}",                       "callback_data": "set:notif_signals"}],
                    [{"text": f"🧙 Minimal (Signal + SL only){min_tick}",        "callback_data": "set:notif_minimal"}],
                    [{"text": "↩️ Back", "callback_data": "menu:settings"}],
                ]
            )

        elif data.startswith("set:notif_"):
            notif_map = {
                "set:notif_all":     "all",
                "set:notif_signals": "signals_only",
                "set:notif_minimal": "minimal",
            }
            notif = notif_map.get(data, "all")
            if db.update_settings(chat_id, "notifications", notif):
                telegram_channel.send_reply(chat_id, "Saved: notification preference updated.")
                self._record_operational_audit(chat_id, "profile_notifications_update", metadata={"notifications": notif})
            self._show_notification_choice_menu(chat_id, telegram_channel, db, message_id)

        # ── Legacy style callbacks (keep backward compat) ────────────────
        elif data in ("set:scalper", "set:day", "set:swing"):
            style_map = {"set:scalper": "scalper", "set:day": "day_trader", "set:swing": "swing_trader"}
            if db.update_style(chat_id, style_map[data]):
                telegram_channel.send_reply(chat_id, "Saved: trading style updated.")
                self._record_operational_audit(chat_id, "profile_style_update", metadata={"style": style_map[data]})
            self._show_style_choice_menu(
                chat_id, telegram_channel, db, message_id, back_callback="menu:settings"
            )

        elif data == "set:view":
            self._show_settings_home(chat_id, message_id, telegram_channel)

    def _handle_telegram_commands(self, commands: list[dict], telegram_channel) -> None:

        """Process incoming Telegram commands from admin and subscribers."""
        from core.alerts.subscriber_db import STYLE_LABELS, STYLE_TIMEFRAMES

        db = self._get_subscriber_db()
        admin_chat_id = telegram_channel._config.chat_id

        for entry in commands:
            raw_cmd   = entry["cmd"].strip()
            chat_id   = entry["chat_id"]
            username  = entry["username"]
            is_admin  = entry["is_admin"]
            msg_type  = entry.get("type", "command")
            message_id = entry.get("message_id")
            cmd_lower = raw_cmd.lower()

            # Route inline keyboard callbacks to dedicated handler
            if msg_type == "callback":
                cqid = entry.get("callback_query_id", "")
                telegram_channel.answer_callback_query(cqid)
                self._handle_menu_callback(raw_cmd, chat_id, message_id, telegram_channel, username, db)
                continue

            # Account setup accepts only non-secret identity values. Consume
            # it before generic command logging so account details are not
            # copied into ordinary Telegram command logs.
            if self._consume_trading_account_draft_input(
                raw_cmd,
                chat_id,
                is_admin,
                telegram_channel,
                message_id=message_id,
            ):
                continue
            if msg_type == "message":
                # Ordinary chat is not a bot command unless a server-side
                # setup draft explicitly consumes it above.
                continue

            logger.info(f"Received Telegram command: {raw_cmd} from {username} ({chat_id})")

            # ---- UNIVERSAL COMMANDS (admin + all users) ----
            if cmd_lower in ("/menu", "/start") and raw_cmd.strip() == cmd_lower:
                self._show_main_menu(chat_id, telegram_channel, db, username)
                continue

            if cmd_lower.startswith("/start"):
                parts = raw_cmd.split()
                if len(parts) < 2:
                    self._show_main_menu(chat_id, telegram_channel, db, username)
                else:
                    code = parts[1].strip().upper()
                    is_valid, reason = db.validate_code(code)
                    if not is_valid:
                        telegram_channel.send_reply(
                            chat_id,
                            f"? <b>Access Denied</b>\n\n{reason}\n\nPlease contact the admin for a valid invite code.",
                        )
                    elif db.is_registered(chat_id):
                        telegram_channel.send_reply(
                            chat_id,
                            "? <b>You are already registered!</b>\n\nUse /style to change your trading profile.",
                        )
                    else:
                        default_style = self._payment_cfg.default_style if self._payment_cfg else "day_trader"
                        db.register(chat_id, username, default_style, invite_code=code)
                        db.consume_code(code, chat_id)
                        label = STYLE_LABELS.get(default_style, default_style)
                        tfs = ", ".join(STYLE_TIMEFRAMES[default_style])
                        telegram_channel.send_reply(
                            chat_id,
                            "? <b>Invite Code Accepted!</b>\n\n"
                            "Welcome to <b>ATLAX AI Signals</b> ??\n\n"
                            f"Your access is active with the default routing profile: <b>{label}</b>\n"
                            f"Signals will route on: <b>{tfs}</b>\n\n"
                            "You can change your profile anytime with /style."
                        )
                        self._show_style_choice_menu(chat_id, telegram_channel, db, back_callback="menu:main")

            elif cmd_lower in ("/subscribe", "/renew") or cmd_lower == "/buy":
                self._show_subscription_menu(chat_id, telegram_channel, db)

            elif cmd_lower.startswith("/buy "):
                parts = raw_cmd.split()
                method = parts[1].lower() if len(parts) > 1 else ""
                if method in ("crypto", "mpesa"):
                    self._start_payment_flow(chat_id, username, method, telegram_channel, db)
                else:
                    self._show_subscription_menu(chat_id, telegram_channel, db)

            elif cmd_lower == "/status":
                if is_admin:
                    state = "?? Paused" if self._is_paused else "?? Running"
                    active_preds = len(self._alert_engine.tracker.predictions)
                    total_subs = db.total_active()
                    pending_payments = self._payment_service.pending_count() if self._payment_service else 0
                    telegram_channel.send_reply(
                        chat_id,
                        f"?? <b>ATLAX Engine</b>\n\n"
                        f"State: {state}\n"
                        f"Active Predictions: {active_preds}\n"
                        f"Active Subscribers: {total_subs}\n"
                        f"Pending Payments: {pending_payments}"
                    )
                else:
                    self._show_access_status(chat_id, telegram_channel, db)

            elif cmd_lower.startswith("/paid"):
                parts = raw_cmd.split(maxsplit=2)
                if len(parts) < 2:
                    telegram_channel.send_reply(
                        chat_id,
                        "?? <b>Usage:</b> <code>/paid REF TX_REFERENCE</code>\n\nReply with the payment reference that ATLAX gave you.",
                    )
                else:
                    reference = parts[1].strip().upper()
                    tx_reference = parts[2].strip() if len(parts) > 2 else ""
                    payment_service = self._ensure_payment_service(db)
                    if payment_service is None:
                        telegram_channel.send_reply(chat_id, "Payments are not configured yet. Please contact the admin.")
                    else:
                        intent, message = payment_service.submit_payment(reference, tx_reference=tx_reference)
                        if intent is None:
                            telegram_channel.send_reply(chat_id, f"? {message}")
                        else:
                            telegram_channel.send_reply(
                                chat_id,
                                f"? <b>Payment proof received</b>\n\nReference: <code>{reference}</code>\nStatus: <b>{intent.get('status', 'submitted')}</b>\n\nAn admin will review it shortly.",
                            )
                            self._notify_admin_payment_review(intent, telegram_channel, db)

            elif cmd_lower in ("/profile", "/terminal"):
                if cmd_lower == "/profile":
                    self._show_user_profile(chat_id, telegram_channel, db)
                else:
                    self._show_signal_terminal(chat_id, telegram_channel, db)

            elif cmd_lower in ("/signals", "/signal"):
                self._show_signal_terminal(chat_id, telegram_channel, db)

            elif cmd_lower in ("/history", "/signal_history", "/signalhistory"):
                self._show_signal_passport_history(chat_id, telegram_channel, db)

            elif cmd_lower in ("/weekly", "/weekly_report", "/weeklyreport"):
                self._show_signal_passport_weekly(chat_id, telegram_channel, db)

            elif cmd_lower in ("/about", "/howitworks"):
                self._show_about_menu(chat_id, telegram_channel)

            elif cmd_lower in ("/help", "/commands"):
                self._show_help_menu(chat_id, telegram_channel)

            elif cmd_lower in ("/faq", "/questions"):
                self._show_faq_menu(chat_id, telegram_channel)

            elif cmd_lower in ("/support", "/contact"):
                self._show_help_menu(chat_id, telegram_channel)

            elif (
                cmd_lower in ("/ask", "/ai")
                or cmd_lower.startswith("/ask ")
                or cmd_lower.startswith("/ai ")
            ):
                question = raw_cmd.split(maxsplit=1)[1].strip() if len(raw_cmd.split(maxsplit=1)) > 1 else ""
                if not question:
                    if is_admin:
                        telegram_channel.send_reply(
                            chat_id,
                            "🤖 <b>Usage:</b> <code>/ask What is my win rate on GBPUSD?</code>",
                        )
                    else:
                        self._show_subscriber_ai_menu(chat_id, telegram_channel, db)
                elif self._advisory_engine is None:
                    telegram_channel.send_reply(chat_id, "⚠️ AI advisory is not configured.")
                elif not is_admin and not db.is_registered(chat_id):
                    telegram_channel.send_reply(
                        chat_id,
                        "You need an active subscription before using subscriber AI. Use /subscribe.",
                    )
                else:
                    thinking_message_id = self._send_one_time_telegram_prompt(telegram_channel, chat_id, "Thinking...")
                    try:
                        answer = (
                            self._advisory_engine.answer_question(question)
                            if is_admin
                            else self._advisory_engine.answer_subscriber_question(question, chat_id)
                        )
                        telegram_channel.send_reply(chat_id, answer)
                    except Exception as exc:
                        telegram_channel.send_reply(chat_id, "⚠️ AI query failed. Please try again later.")
                        logger.error('{"event":"ai_command_error","error":"%s"}', str(exc)[:120])
                    finally:
                        self._delete_one_time_telegram_message(telegram_channel, chat_id, thinking_message_id)

            elif cmd_lower == "/style" or cmd_lower.startswith("/style "):
                sub = db.get_subscriber(chat_id)
                if not sub or not db.is_registered(chat_id):
                    telegram_channel.send_reply(chat_id, "You need an active subscription first. Use /subscribe.")
                else:
                    parts = raw_cmd.split()
                    requested = parts[1].strip().lower() if len(parts) > 1 else ""
                    aliases = {
                        "scalp": "scalper",
                        "scalper": "scalper",
                        "day": "day_trader",
                        "day_trader": "day_trader",
                        "swing": "swing_trader",
                        "swing_trader": "swing_trader",
                        "all": "all_styles",
                        "both": "all_styles",
                        "all_styles": "all_styles",
                    }
                    style = aliases.get(requested)
                    if not requested or style is None:
                        self._show_style_choice_menu(chat_id, telegram_channel, db)
                    elif db.update_style(chat_id, style):
                        telegram_channel.send_reply(
                            chat_id,
                            f"Saved: routing style is now <b>{STYLE_LABELS[style]}</b>.",
                        )
                    else:
                        telegram_channel.send_reply(chat_id, "Style change rejected.")

            elif cmd_lower == "/mystyle":
                sub = db.get_subscriber(chat_id)
                if sub and db.is_registered(chat_id):
                    label = STYLE_LABELS.get(sub["style"], sub["style"])
                    tfs = ", ".join(sub.get("timeframes", []))
                    settings = db.get_settings(chat_id)
                    syms = ", ".join(settings["symbols"]) if settings["symbols"] else "All symbols"
                    conf = f"{settings['min_confidence']}%" if settings["min_confidence"] is not None else "Profile default"
                    notifs = {
                        "all": "All updates",
                        "signals_only": "Signals only",
                        "minimal": "Minimal",
                    }.get(settings["notifications"], settings["notifications"])
                    telegram_channel.send_reply(
                        chat_id,
                        f"?? <b>Your Profile & Settings</b>\n\n"
                        f"Style: <b>{label}</b>\n"
                        f"Timeframes: <b>{tfs}</b>\n"
                        f"Symbols: <b>{syms}</b>\n"
                        f"Min Confidence: <b>{conf}</b>\n"
                        f"Notifications: <b>{notifs}</b>\n\n"
                        f"Use /settings to change these."
                    )
                else:
                    telegram_channel.send_reply(chat_id, "You are not registered yet. Use /subscribe or /start YOUR_CODE.")

            elif cmd_lower.startswith("/setting"):
                sub = db.get_subscriber(chat_id)
                if not sub or not db.is_registered(chat_id):
                    telegram_channel.send_reply(chat_id, "?? You need an active subscription first. Use /subscribe or /start YOUR_CODE.")
                else:
                    parts = cmd_lower.split()
                    sub_cmd = parts[1] if len(parts) > 1 else ""

                    if not sub_cmd:
                        telegram_channel.send_reply(
                            chat_id,
                            "?? <b>ATLAX Settings</b>\n\n"
                            "<code>/settings symbols XAUUSD,GBPUSD</code> - Limit symbol routing\n"
                            "<code>/settings symbols all</code> - Receive all symbols\n"
                            "<code>/settings confidence 70</code> - Minimum confidence\n"
                            "<code>/settings confidence off</code> - Use profile default\n"
                            "<code>/settings alerts all</code> - Full updates\n"
                            "<code>/settings alerts signals_only</code> - Signals only\n"
                            "<code>/settings alerts minimal</code> - Signals + SL only"
                        )
                    elif sub_cmd == "symbols":
                        value_raw = raw_cmd.split(maxsplit=2)[2].strip().upper() if len(raw_cmd.split()) > 2 else ""
                        if not value_raw or value_raw == "ALL":
                            db.update_settings(chat_id, "symbols", [])
                            telegram_channel.send_reply(chat_id, "? Symbol filter cleared. You will now receive signals for all pairs.")
                        else:
                            symbols = [s.strip() for s in value_raw.split(",") if s.strip()]
                            available = set(self._available_symbol_options(chat_id, db))
                            normalized = list(dict.fromkeys(symbols))
                            if len(normalized) > 12 or (available and any(symbol not in available for symbol in normalized)):
                                telegram_channel.send_reply(chat_id, "Choose up to 12 pairs from the configured market list.")
                            else:
                                db.update_settings(chat_id, "symbols", normalized)
                                telegram_channel.send_reply(chat_id, f"? Symbol filter set: {', '.join(normalized)}")
                    elif sub_cmd == "confidence":
                        value_raw = parts[2] if len(parts) > 2 else ""
                        if value_raw == "off" or not value_raw:
                            db.update_settings(chat_id, "min_confidence", None)
                            telegram_channel.send_reply(chat_id, "? Confidence override cleared.")
                        elif value_raw.isdigit() and 0 <= int(value_raw) <= 100:
                            db.update_settings(chat_id, "min_confidence", int(value_raw))
                            telegram_channel.send_reply(chat_id, f"? Minimum confidence set to {value_raw}%.")
                        else:
                            telegram_channel.send_reply(chat_id, "?? Enter a number between 0 and 100, or 'off'.")
                    elif sub_cmd == "alerts":
                        value_raw = parts[2] if len(parts) > 2 else ""
                        valid_modes = {"all", "signals_only", "minimal"}
                        if value_raw in valid_modes:
                            db.update_settings(chat_id, "notifications", value_raw)
                            telegram_channel.send_reply(chat_id, f"? Notification mode updated: {value_raw}")
                        else:
                            telegram_channel.send_reply(chat_id, "?? Choose all, signals_only, or minimal.")
                    else:
                        telegram_channel.send_reply(chat_id, "?? Unknown setting. Send /settings to see the menu.")

            elif cmd_lower == "/help":
                self._show_help_menu(chat_id, telegram_channel)
                return
                if db.is_registered(chat_id):
                    telegram_channel.send_reply(
                        chat_id,
                        "?? <b>ATLAX User Commands</b>\n\n"
                        "<code>/subscribe</code> - Open the purchase menu\n"
                        "<code>/buy crypto</code> - Start crypto payment\n"
                        "<code>/buy mpesa</code> - Start M-Pesa payment\n"
                        "<code>/paid REF TX_ID</code> - Submit payment proof\n"
                        "<code>/status</code> - Show access status\n"
                        "<code>/mystyle</code> - View your profile and settings\n"
                        "<code>/style scalper</code> - Switch to Scalper\n"
                        "<code>/style day</code> - Switch to Day Trader\n"
                        "<code>/style swing</code> - Switch to Swing Trader\n"
                        "<code>/style all</code> - Receive all configured style timeframes\n"
                         "<code>/ai</code> - Open subscriber-safe ATLAX AI\n"
                         "<code>/ask your question</code> - Ask about ATLAX, markets, news, or education\n"
                         "<code>/settings</code> - Open settings\n"
                         "<code>/signals</code> - Open the signal terminal\n"
                         "<code>/profile</code> - View your ATLAX profile\n"
                         "<code>/about</code> - Learn how ATLAX works\n"
                         "<code>/support</code> - Get help with access or payments"
                    )
                else:
                    telegram_channel.send_reply(chat_id, "Use /subscribe to start, or /start YOUR_CODE if you already have an invite code.")

            # ---- ADMIN-ONLY COMMANDS ----
            elif is_admin:
                if cmd_lower == "/pause":
                    self._is_paused = True
                    telegram_channel.send_reply(chat_id, "?? <b>ATLAX Scanner Paused.</b>\n\nNo further scans will run until /resume.")

                elif cmd_lower == "/resume":
                    self._is_paused = False
                    telegram_channel.send_reply(chat_id, "?? <b>ATLAX Scanner Resumed.</b>\n\nScanning active markets...")

                elif cmd_lower == "/engine":
                    state = "?? Paused" if self._is_paused else "?? Running"
                    active_preds = len(self._alert_engine.tracker.predictions)
                    total_subs = db.total_active()
                    pending_payments = self._payment_service.pending_count() if self._payment_service else 0
                    telegram_channel.send_reply(
                        chat_id,
                        f"?? <b>ATLAX Engine</b>\n\n"
                        f"State: {state}\n"
                        f"Active Predictions: {active_preds}\n"
                        f"Active Subscribers: {total_subs}\n"
                        f"Pending Payments: {pending_payments}"
                    )

                elif cmd_lower == "/stats":
                    stats = self._alert_engine.tracker.get_daily_report()
                    total = stats.get("total", 0)
                    success = stats.get("success", 0)
                    failed = stats.get("failed", 0)
                    win_rate = (success / (success + failed) * 100) if (success + failed) > 0 else 0
                    telegram_channel.send_reply(
                        chat_id,
                        f"?? <b>ATLAX DAILY SUMMARY ({stats.get('date', 'Today')})</b>\n\n"
                        f"<b>Total Signals:</b> {total}\n"
                        f"? <b>Successful:</b> {success}\n"
                        f"? <b>Failed:</b> {failed}\n\n"
                        f"<b>Win Rate:</b> {win_rate:.1f}%"
                    )

                elif cmd_lower.startswith("/ask"):
                    question = raw_cmd[4:].strip()
                    if not question:
                        telegram_channel.send_reply(chat_id, "?? <b>Usage:</b> <code>/ask What is my win rate on GBPUSD?</code>")
                    elif self._advisory_engine is None:
                        telegram_channel.send_reply(chat_id, "? AI advisory is not configured.")
                    else:
                        thinking_message_id = self._send_one_time_telegram_prompt(telegram_channel, chat_id, "Thinking...")
                        try:
                            answer = self._advisory_engine.answer_question(question)
                            telegram_channel.send_reply(chat_id, answer)
                        except Exception as _exc:
                            telegram_channel.send_reply(chat_id, "? AI query failed. Check logs.")
                            logger.error('{"event":"ask_command_error","error":"%s"}', str(_exc)[:120])
                        finally:
                            self._delete_one_time_telegram_message(telegram_channel, chat_id, thinking_message_id)

                elif cmd_lower.startswith("/note"):
                    parts = raw_cmd.split(maxsplit=2)
                    if len(parts) < 3:
                        telegram_channel.send_reply(chat_id, "?? <b>Usage:</b> <code>/note GBPUSD I closed early due to fear of news</code>")
                    else:
                        symbol = parts[1].upper()
                        note_text = parts[2]
                        try:
                            from core.analytics.note_store import save_note
                            if self._analytics_cfg:
                                saved = save_note(self._analytics_cfg.db_path, symbol, note_text)
                                if saved:
                                    telegram_channel.send_reply(chat_id, f"? <b>Note saved</b> for {symbol}: <i>{note_text[:100]}</i>")
                                else:
                                    telegram_channel.send_reply(chat_id, f"?? No recent trade found for <b>{symbol}</b>. Check the symbol name.")
                            else:
                                telegram_channel.send_reply(chat_id, "? Analytics not configured.")
                        except Exception as _exc:
                            telegram_channel.send_reply(chat_id, "? Note save failed. Check logs.")
                            logger.error('{"event":"note_command_error","error":"%s"}', str(_exc)[:120])

                elif cmd_lower == "/report":
                    if self._advisory_engine is None:
                        telegram_channel.send_reply(chat_id, "? AI advisory is not configured.")
                    else:
                        telegram_channel.send_reply(chat_id, "?? Generating AI report, please wait...")
                        try:
                            report = self._advisory_engine.generate_daily_report()
                            if report:
                                telegram_channel.broadcast_advisory(report)
                            else:
                                telegram_channel.send_reply(chat_id, "?? No trades recorded yet today.")
                        except Exception as _exc:
                            telegram_channel.send_reply(chat_id, "? Report generation failed. Check logs.")
                            logger.error('{"event":"report_command_error","error":"%s"}', str(_exc)[:120])

                elif cmd_lower in ("/admin", "/adminpanel", "/operations"):
                    self._show_admin_home(chat_id, telegram_channel, db)

                elif cmd_lower == "/subscribers":
                    subs = db.list_subscribers()
                    if not subs:
                        telegram_channel.send_reply(chat_id, "?? No subscribers yet.")
                    else:
                        lines = [f"?? <b>Subscribers ({len(subs)})</b>"]
                        for s in subs:
                            status = "?" if s.get("active") else "?"
                            lines.append(f"{status} @{s.get('username', 'unknown')} ? {STYLE_LABELS.get(s.get('style', ''), s.get('style', ''))}")
                        telegram_channel.send_reply(chat_id, "\n".join(lines))

                elif cmd_lower == "/payments":
                    if self._payment_service is None:
                        telegram_channel.send_reply(chat_id, "Payments are not configured yet.")
                    else:
                        pending = self._payment_service._store.list_pending()
                        if not pending:
                            telegram_channel.send_reply(chat_id, "?? No pending payments.")
                        else:
                            lines = [f"?? <b>Pending Payments ({len(pending)})</b>"]
                            for intent in pending[:20]:
                                lines.append(
                                    f"{intent['reference']} ? @{intent.get('username', 'unknown')} ? {intent.get('method', 'unknown')} ? {intent.get('status', 'unknown')}"
                                )
                            telegram_channel.send_reply(chat_id, "\n".join(lines))

                elif cmd_lower in ("/payment_queue", "/paymentqueue"):
                    payment_service = self._ensure_payment_service(db)
                    if payment_service is None:
                        telegram_channel.send_reply(chat_id, "Payments are not configured yet.")
                    else:
                        pending = payment_service._store.list_pending()
                        if not pending:
                            telegram_channel.send_reply(chat_id, "No pending payments.")
                        else:
                            lines = [f"<b>Payment Review Queue ({len(pending)})</b>", ""]
                            keyboard = []
                            for intent in pending[:20]:
                                reference = str(intent.get("reference", ""))
                                display_reference = escape(reference)
                                display_username = escape(str(intent.get("username", "unknown")))
                                display_method = escape(str(intent.get("method", "unknown")))
                                display_status = escape(str(intent.get("status", "unknown")))
                                lines.append(
                                    f"<code>{display_reference}</code> | @{display_username} | "
                                    f"{display_method} | <b>{display_status}</b>"
                                )
                                keyboard.append([
                                    {"text": f"Approve {reference}", "callback_data": f"payment:approve:{reference}"},
                                    {"text": f"Reject {reference}", "callback_data": f"payment:reject:{reference}"},
                                ])
                            keyboard.append([{"text": "📜 Review History", "callback_data": "payment:history"}])
                            telegram_channel.send_inline_menu(chat_id, "\n".join(lines), keyboard)

                elif cmd_lower in ("/payment_history", "/paymenthistory"):
                    self._show_payment_history(chat_id, telegram_channel, db)

                elif cmd_lower in ("/revenue", "/revenue_summary", "/revenuesummary"):
                    payment_service = self._ensure_payment_service(db)
                    if payment_service is None:
                        telegram_channel.send_reply(chat_id, "Payments are not configured yet.")
                    else:
                        self._record_operational_audit(chat_id, "admin_revenue_view")
                        telegram_channel.send_reply(
                            chat_id,
                            payment_service.build_admin_revenue_summary_message(),
                        )

                elif cmd_lower.startswith("/gencode"):
                    parts = raw_cmd.split()
                    days = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 30
                    label = " ".join(parts[2:]) if len(parts) > 2 else ""
                    code = db.generate_code(label=label, days_valid=days)
                    telegram_channel.send_reply(
                        chat_id,
                        f"?? <b>New Invite Code Generated</b>\n\n"
                        f"<code>{code}</code>\n\n"
                        f"Valid for <b>{days} days</b>.\n"
                        f"Share this with your subscriber."
                    )

                elif cmd_lower.startswith("/revoke"):
                    parts = raw_cmd.split()
                    if len(parts) > 1:
                        db.revoke_code(parts[1])
                        telegram_channel.send_reply(chat_id, f"??? Code <code>{parts[1]}</code> revoked.")

                elif cmd_lower.startswith("/ban"):
                    parts = raw_cmd.split()
                    if len(parts) > 1:
                        db.deactivate(parts[1])
                        telegram_channel.send_reply(chat_id, f"?? Subscriber {parts[1]} deactivated.")

                elif cmd_lower == "/ping":
                    telegram_channel.send_reply(chat_id, "?? <b>Pong!</b> Engine is online.")

                elif cmd_lower == "/clear_predictions":
                    self._alert_engine.tracker.predictions.clear()
                    self._alert_engine.tracker._save()
                    telegram_channel.send_reply(chat_id, "?? <b>Active predictions cleared.</b>")

                elif cmd_lower == "/help":
                    telegram_channel.send_reply(
                        chat_id,
                        "??? <b>ATLAX Admin Commands</b>\n\n"
                        "<code>/pause</code> ? Pause scanner\n"
                        "<code>/resume</code> ? Resume scanner\n"
                        "<code>/engine</code> ? Engine state + payments\n"
                        "<code>/stats</code> ? Daily prediction scorecard\n"
                        "<code>/report</code> ? Force AI daily report now\n"
                        "<code>/ask [question]</code> ? Ask AI about your performance\n"
                        "<code>/note [SYMBOL] [text]</code> ? Add trade note\n"
                        "<code>/subscribers</code> ? List all subscribers\n"
                        "<code>/payments</code> ? List pending payment intents\n"
                        "<code>/admin</code> ? Open the admin operations console\n"
                        "<code>/payment_queue</code> ? Review pending payments\n"
                        "<code>/payment_history</code> ? View payment review history\n"
                        "<code>/revenue</code> ? View the payment revenue summary\n"
                        "<code>/gencode [days] [label]</code> ? Generate invite code\n"
                        "<code>/revoke [CODE]</code> ? Revoke an invite code\n"
                        "<code>/ban [chat_id]</code> ? Deactivate a subscriber\n"
                        "<code>/ping</code> ? Connection check\n"
                        "<code>/clear_predictions</code> ? Wipe trackers"
                    )

            # ---- UNREGISTERED USER FALLBACK ----
            else:
                if not db.is_registered(chat_id):
                    telegram_channel.send_reply(
                        chat_id,
                        "?? <b>Welcome to ATLAX AI!</b>\n\nUse /subscribe to get access, or <code>/start YOUR_CODE</code> if you already have an invite code."
                    )
                else:
                    telegram_channel.send_reply(
                        chat_id,
                        "?? <b>Unknown command or permission denied.</b>\n\nUse /help to see available commands."
                    )

    # ------------------------------------------------------------------
    # Core cycle
    # ------------------------------------------------------------------

    def _run_cycle(self) -> None:
        """
        Execute one full scan across all instruments and all profiles.
        Errors in one instrument never abort the others.
        """
        self._cycle_count += 1
        self._cycle_instruments_processed = 0
        self._cycle_instrument_failures = 0
        cycle_ts = datetime.now(timezone.utc).isoformat()
        logger.info(
            '{"event":"scan_cycle_start","cycle":%d,"at":"%s","instruments":%d}',
            self._cycle_count, cycle_ts, len(self._market_cfg.instruments),
        )
        self._last_cycle_at = cycle_ts

        self._profile_monitor_report = self._refresh_prop_firm_profile_monitor()

        self._run_subscription_lifecycle()
        self._reconcile_trade_state()

        current_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        stats = self._alert_engine.tracker.get_daily_report()
        
        if stats.get("date") and stats.get("date") != current_date:
            telegram_channel = next((c for c in self._alert_engine._channels if c.name == "telegram"), None)
            if stats.get("total", 0) > 0 and telegram_channel and telegram_channel.is_enabled:
                total = stats["total"]
                success = stats["success"]
                failed = stats["failed"]
                win_rate = (success / (success + failed) * 100) if (success + failed) > 0 else 0
                
                report = (
                    f"📊 <b>ATLAX DAILY SUMMARY ({stats['date']})</b>\n\n"
                    f"<b>Total Signals:</b> {total}\n"
                    f"✅ <b>Successful (Hit T1/T2):</b> {success}\n"
                    f"❌ <b>Failed (Hit SL):</b> {failed}\n\n"
                    f"<b>Win Rate:</b> {win_rate:.1f}%"
                )
                telegram_channel.send_reply(
                    chat_id=telegram_channel._config.chat_id,
                    text=report,
                    message_id=None
                )
            self._alert_engine.tracker.reset_daily_report()
        elif not stats.get("date"):
            self._alert_engine.tracker.reset_daily_report()

        for instrument in self._market_cfg.instruments:
            try:
                self._process_instrument(instrument)
            except Exception as exc:  # noqa: BLE001
                self._cycle_instrument_failures += 1
                logger.error(
                    '{"event":"instrument_error","symbol":"%s","timeframe":"%s",'
                    '"error":"%s"}',
                    instrument.symbol, instrument.timeframe, str(exc)[:200],
                )

        # End-of-day AI advisory report
        self._maybe_send_daily_advisory()

        # Weekly strategy optimizer (fires Sunday 22:00 UTC)
        self._maybe_send_weekly_advisory()

        # Intra-day anomaly detection (runs every 15 cycles)
        self._check_performance_anomaly()

        # Partial-profit management — check open ATLAX positions every cycle
        try:
            import MetaTrader5 as _mt5_pp  # noqa: F811
            self._trade_manager.check_and_manage(_mt5_pp, self._execution_cfg.magic_number)
        except ImportError:
            pass
        except Exception as _pp_exc:
            logger.warning(
                '{"event":"trade_manager_check_error","error":"%s"}', str(_pp_exc)[:120]
            )

        logger.info(
            '{"event":"scan_cycle_done","cycle":%d}', self._cycle_count
        )
        self._last_cycle_completed_at = datetime.now(timezone.utc).isoformat()

    def _key_level_evaluation_for_parent(
        self,
        instrument: WatchedInstrument,
        sequence,
        parent,
    ):
        """Return immutable V1 key-level evidence for one closed parent.

        This belongs to the market-data orchestration boundary. It requests
        only configured source timeframes, evaluates them as of the parent's
        close, and returns ``UNKNOWN`` through the evaluator when history is
        incomplete. It does not inspect a CRT result or make trade decisions.
        """
        evaluator = getattr(self, "_key_level_evaluator", None)
        if evaluator is None or parent is None or not parent.is_closed:
            return None

        cache_key = "|".join(
            (
                parent.symbol,
                parent.timeframe,
                parent.open_time.astimezone(timezone.utc).isoformat(),
            )
        )
        cached = self._key_level_evaluations.get(cache_key)
        if cached is not None:
            return cached

        try:
            sources = {}
            for source_timeframe in self._key_level_cfg.source_timeframes:
                source_instrument = self._key_level_source_instrument(
                    instrument,
                    source_timeframe,
                )
                sources[source_timeframe] = MT5CandleFeed(source_instrument).get_candles(
                    include_live=False
                ).candles

            evaluation = evaluator.evaluate(
                parent=parent,
                parent_atr=simple_atr_for_parent(
                    sequence.candles,
                    parent,
                    instrument.atr_period,
                ),
                pip_size=Decimal(str(instrument.pip_size)),
                daily_candles=sources["D1"],
                weekly_candles=sources["W1"],
                h4_candles=sources["H4"],
                d1_candles=sources["D1"],
            )
        except Exception as exc:  # noqa: BLE001 - required evidence fails closed
            logger.warning(
                '{"event":"key_level_evaluation_failed","symbol":"%s",'
                '"timeframe":"%s","parent_open":"%s","reason":"%s"}',
                instrument.symbol,
                instrument.timeframe,
                parent.open_time.isoformat(),
                str(exc)[:160],
            )
            return None

        self._key_level_evaluations[cache_key] = evaluation
        if self._key_level_shadow_ledger is not None:
            self._key_level_shadow_ledger.observe_evaluation(evaluation)
        logger.info(
            '{"event":"key_level_evaluated","symbol":"%s","timeframe":"%s",'
            '"parent_open":"%s","status":"%s","parent_at_htf_zone":%s,'
            '"matched_levels":%d}',
            parent.symbol,
            parent.timeframe,
            parent.open_time.isoformat(),
            evaluation.status,
            str(evaluation.parent_at_htf_zone).lower(),
            evaluation.matched_level_count,
        )
        return evaluation

    def _key_level_source_instrument(
        self,
        instrument: WatchedInstrument,
        source_timeframe: str,
    ) -> WatchedInstrument:
        """Resolve a configured source timeframe without hidden parameters."""
        for configured in self._market_cfg.instruments:
            if (
                configured.symbol == instrument.symbol
                and configured.timeframe == source_timeframe
            ):
                return configured
        # W1 is an approved evaluator source but not a scanner destination.
        # Its timeframe comes from key_level_evaluator.source_timeframes; the
        # history depth and instrument precision inherit the active config.
        return WatchedInstrument(
            symbol=instrument.symbol,
            timeframe=source_timeframe,
            candle_count=instrument.candle_count,
            atr_period=instrument.atr_period,
            pip_size=instrument.pip_size,
        )

    def _process_instrument(self, instrument: WatchedInstrument) -> None:
        """
        Run the full detection + strategy + confidence + alert pipeline
        for one instrument against all active profiles.
        """
        from decimal import Decimal

        # 1. Fetch candles and ATR from MT5
        feed = MT5CandleFeed(instrument)
        seq_live   = feed.get_candles(include_live=True)
        seq_closed = feed.get_candles(include_live=False)
        # Reuse the same closed broker snapshot that the detector receives.
        # A second MT5 history request can fail independently and turn a
        # valid premium-quality event into ATR UNKNOWN.
        atr  = feed.get_atr_from_sequence(seq_closed)
        if seq_closed.candles:
            self._cycle_instruments_processed += 1
            self._last_market_data_at = datetime.now(timezone.utc).isoformat()

        # Feed closed candles to the Trade Manager so Phase 3 momentum
        # detection (_observe_momentum_loss) has live candle data every cycle.
        # The Trade Manager uses (symbol, momentum_source_timeframe) as the key
        # so only the matching timeframe matters for each watched position.
        try:
            self._trade_manager.update_closed_candles(
                instrument.symbol,
                instrument.timeframe,
                seq_closed.candles,
            )
        except Exception as _tm_candle_exc:
            logger.warning(
                '{"event":"trade_manager_candle_update_error","symbol":"%s","timeframe":"%s","error":"%s"}',
                instrument.symbol, instrument.timeframe, str(_tm_candle_exc)[:120],
            )

        # Update Heatmap Radar using live data
        self._heatmap.update(instrument.symbol, instrument.timeframe, seq_live)

        # Update real price correlation using closed D1 candles (Pearson from close prices)
        if seq_closed.candles and instrument.timeframe == 'D1':
            last_close = float(seq_closed.candles[-1].close)
            self._correlation.update(instrument.symbol, instrument.timeframe, last_close)

        logger.debug(
            '{"event":"instrument_fetched","symbol":"%s","timeframe":"%s",'
            '"candles":%d,"atr":"%s"}',
            instrument.symbol, instrument.timeframe, len(seq_closed.candles), str(atr),
        )

        # 1b. Check active predictions against the latest LIVE candle
        if seq_live.candles:
            current_high = float(seq_live.candles[-1].high)
            current_low = float(seq_live.candles[-1].low)
            current_candle_open = seq_live.candles[-1].open_time.timestamp()
            telegram_channel = next((c for c in self._alert_engine._channels if c.name == "telegram"), None)
            self._alert_engine.tracker.check_market_data(
                symbol=instrument.symbol,
                current_high=current_high,
                current_low=current_low,
                current_candle_open=current_candle_open,
                telegram_channel=telegram_channel
            )

        # 1c. Check pending FVG watches only when the deferred-entry workflow
        # is explicitly enabled. With the approved default disabled, FVG is
        # confluence only and a CRT is alerted/executed at formation.
        if mt5 is None:
            logger.warning('{"event":"fvg_check_skipped","reason":"mt5_not_installed"}')
            return
        deferred_fvg_enabled = bool(
            getattr(self, "_fvg_cfg", None) is not None
            and getattr(self._fvg_cfg, "enabled", False)
            and getattr(self._fvg_cfg, "execution_gate_enabled", False)
        )
        if not deferred_fvg_enabled:
            self._fvg_watcher.clear_watches(reason="execution_gate_disabled")
        else:
            tick = mt5.symbol_info_tick(instrument.symbol)
        if deferred_fvg_enabled and tick is not None:
            triggered_watches = self._fvg_watcher.check_live_price(
                symbol=instrument.symbol,
                live_bid=tick.bid,
                live_ask=tick.ask,
            )
            if triggered_watches:
                telegram_channel = next(
                    (c for c in self._alert_engine._channels if c.name == "telegram"), None
                )
                for watch in triggered_watches:
                    logger.info(
                        '{"event":"fvg_entry_triggered_dispatch","watch_id":"%s",'
                        '"symbol":"%s","direction":"%s"}',
                        watch.watch_id, watch.symbol, watch.direction,
                    )
                    fvg_entry_delivered = False
                    if telegram_channel and telegram_channel.is_enabled:
                        delivered = telegram_channel.broadcast_fvg_entry(
                            symbol=watch.symbol,
                            htf_timeframe=watch.htf_timeframe,
                            ltf_timeframe=watch.ltf_timeframe,
                            direction=watch.direction,
                            fvg_high=watch.fvg_high,
                            fvg_low=watch.fvg_low,
                            invalidation_level=watch.invalidation_level,
                            midpoint_target=watch.midpoint_target,
                            opposite_extreme_target=watch.opposite_extreme_target,
                            confidence_score=watch.confidence_score,
                            profile=watch.profile,
                            candidate_id=watch.candidate_id,
                            parent_at_htf_zone=watch.parent_at_htf_zone,
                            profile_min_confidence=watch.profile_min_confidence,
                            quality_status=watch.quality_status,
                            quality_would_suppress=watch.quality_would_suppress,
                            atr_quality_config=getattr(self, "_atr_quality_cfg", None),
                        )
                        logger.info(
                            '{"event":"fvg_entry_delivery_result","watch_id":"%s",'
                            '"candidate_id":"%s","symbol":"%s","delivered":%d}',
                            watch.watch_id,
                            watch.candidate_id,
                            watch.symbol,
                            len(delivered),
                        )
                        fvg_entry_delivered = bool(delivered)
                    else:
                        logger.warning(
                            '{"event":"fvg_entry_delivery_unavailable","watch_id":"%s",'
                            '"candidate_id":"%s","symbol":"%s",'
                            '"reason":"telegram_channel_unavailable"}',
                            watch.watch_id,
                            watch.candidate_id,
                            watch.symbol,
                        )

                    if (
                        getattr(
                            getattr(self, "_execution_cfg", None),
                            "require_alert_eligibility",
                            True,
                        )
                        and not fvg_entry_delivered
                    ):
                        logger.warning(
                            '{"event":"fvg_execution_blocked_alert_not_delivered",'
                            '"candidate_id":"%s","watch_id":"%s"}',
                            watch.candidate_id,
                            watch.watch_id,
                        )
                        continue

                    # 1d. Auto-Execute the delayed FVG trade
                    request = self._approval_engine.get_request(watch.candidate_id)
                    if request is not None:
                        if request.risk_evaluation.approved:
                            logger.info('{"event":"fvg_auto_execute","candidate_id":"%s"}', watch.candidate_id)
                            record = self._execution_engine.execute(request)
                            self._persist_execution_record(record)
                            logger.info(
                                '{"event":"fvg_execution_attempted","candidate_id":"%s","status":"%s"}',
                                watch.candidate_id, record.status
                            )
                            self._try_register_partial_watch(record, request)
                        else:
                            logger.warning('{"event":"fvg_execution_blocked","candidate_id":"%s","reason":"risk_not_approved"}', watch.candidate_id)
                    else:
                        logger.warning('{"event":"fvg_execution_failed","candidate_id":"%s","reason":"request_not_found_in_approval_db"}', watch.candidate_id)

        # Evaluate each newly closed parent independently of CRT detection.
        # A later CRT sequence retrieves the result for its exact parent; no
        # sweep, confirmation, or future bar affects this evidence.
        if seq_closed.candles:
            self._key_level_evaluation_for_parent(
                instrument,
                seq_closed,
                seq_closed.candles[-1],
            )

        required_candles = 2 if not self._crt_cfg.require_confirmation_candle else 3
        detector_parent = (
            seq_closed.candles[-required_candles]
            if len(seq_closed.candles) >= required_candles
            else None
        )
        key_level_evaluation = self._key_level_evaluation_for_parent(
            instrument,
            seq_closed,
            detector_parent,
        )

        # 2. Run CRT Detector on strictly closed data. Key-level evidence is
        # external metadata only; it never changes whether a CRT exists.
        det_output = self._detector.detect(
            seq_closed, 
            atr=atr,
            pip_size=instrument.pip_size,
            parent_at_htf_zone=(
                key_level_evaluation.parent_at_htf_zone
                if key_level_evaluation is not None
                else None
            ),
        )

        if not det_output.detected:
            logger.debug(
                '{"event":"no_detection","symbol":"%s","timeframe":"%s",'
                '"reason":"%s"}',
                instrument.symbol, instrument.timeframe,
                det_output.reason[:120],
            )
            return  # Nothing to route — skip all profiles

        # 2b. Spam Prevention / Deduplication
        # Normalize close_time to a naive datetime before comparing.
        # MT5 may return timezone-aware datetimes; our persisted state is always
        # naive. Python raises TypeError (or silently returns False) when comparing
        # aware vs naive datetimes, so we strip tzinfo here at the source.
        raw_close_time = seq_closed.candles[-1].close_time
        if hasattr(raw_close_time, "tzinfo") and raw_close_time.tzinfo is not None:
            last_candle_time = raw_close_time.replace(tzinfo=None)
        else:
            last_candle_time = raw_close_time

        state_key = f"{instrument.symbol}_{instrument.timeframe}"
        if self._last_alerted.get(state_key) == last_candle_time:
            logger.debug(
                '{"event":"duplicate_detection_skipped","symbol":"%s","timeframe":"%s"}',
                instrument.symbol, instrument.timeframe
            )
            return

        logger.info(
            '{"event":"crt_detected","symbol":"%s","timeframe":"%s",'
            '"classification":"%s"}',
            instrument.symbol, instrument.timeframe, det_output.classification,
        )

        # 2c. LTF FVG Detection (multi-timeframe confluence)
        # Look up which LTF to use from the configured timeframe pairings.
        # The lower-timeframe confluence source is configuration-driven.
        ltf_tf = self._crt_cfg.timeframe_pairings.get(instrument.timeframe)
        fvg_result = None
        fvg_present = None  # None = not computed (will be excluded from scoring)

        if ltf_tf:
            try:
                from core.settings.market_data_config import WatchedInstrument as WI
                ltf_instrument = WI(
                    symbol=instrument.symbol,
                    timeframe=ltf_tf,
                    candle_count=30,  # enough to find a 3-candle FVG in the sweep window
                    atr_period=14,
                    pip_size=instrument.pip_size,
                )
                ltf_feed = MT5CandleFeed(ltf_instrument)
                ltf_seq = ltf_feed.get_candles(count=30, include_live=False)

                # Determine CRT direction for FVG search
                crt_direction = (
                    "bullish" if det_output.classification == "bullish_crt" else "bearish"
                )

                # Update FVG detector pip_size for this instrument
                self._fvg_detector = LTFFVGDetector(
                    min_fvg_pips=0.5,
                    pip_size=instrument.pip_size,
                )
                fvg_result = self._fvg_detector.detect(ltf_seq, crt_direction)
                fvg_present = fvg_result.found

                logger.info(
                    '{"event":"ltf_fvg_scan","symbol":"%s","htf":"%s","ltf":"%s",'
                    '"fvg_found":%s}',
                    instrument.symbol, instrument.timeframe, ltf_tf,
                    str(fvg_present).lower(),
                )
            except Exception as exc:
                logger.warning(
                    '{"event":"ltf_fvg_scan_failed","symbol":"%s","timeframe":"%s",'
                    '"reason":"%s"}',
                    instrument.symbol, instrument.timeframe, str(exc)[:120],
                )
                fvg_present = None
        else:
            logger.debug(
                '{"event":"ltf_fvg_no_pairing","symbol":"%s","timeframe":"%s"}',
                instrument.symbol, instrument.timeframe,
            )

        # Quality overlays classify an already-detected CRT for routing. They
        # never alter the detector's pattern decision or the strategy outcome.
        atr_quality = None
        if getattr(self, "_atr_quality_cfg", None) is not None:
            atr_quality = evaluate_atr_quality(
                det_output,
                atr,
                self._atr_quality_cfg,
            )
        quality_tightening = self._detector._quality_tightening_for_sequence(
            seq_closed,
            atr,
        )

        # 3. Route to each active profile with FVG and quality context for
        # confidence scoring and final subscriber delivery.
        self._current_detection_delivery_retryable = False
        for profile in self._strategy_cfg.active_profiles:
            self._process_profile(
                det_output, profile, instrument,
                fvg_present=fvg_present,
                fvg_result=fvg_result,
                ltf_tf=ltf_tf,
                parent_at_htf_zone=(
                    key_level_evaluation.parent_at_htf_zone
                    if key_level_evaluation is not None
                    else None
                ),
                key_level_evaluation=key_level_evaluation,
                atr_quality=atr_quality,
                quality_tightening=quality_tightening,
            )

        # Do not consume scanner-level deduplication state when Telegram had
        # an eligible subscriber but delivery failed. The next cycle can then
        # retry the same closed setup after a transient API/network failure.
        if getattr(self, "_current_detection_delivery_retryable", False):
            logger.warning(
                '{"event":"detection_delivery_retryable","symbol":"%s",'
                '"timeframe":"%s","candle_time":"%s"}',
                instrument.symbol,
                instrument.timeframe,
                last_candle_time.isoformat(),
            )
        else:
            self._last_alerted[state_key] = last_candle_time
            self._save_alerted_state()


    def _process_profile(
        self,
        det_output,
        profile: ProfileConfig,
        instrument: WatchedInstrument,
        fvg_present: Optional[bool] = None,
        fvg_result=None,
        ltf_tf: Optional[str] = None,
        parent_at_htf_zone: Optional[bool] = None,
        key_level_evaluation=None,
        atr_quality=None,
        quality_tightening=None,
    ) -> None:
        """Run strategy → confidence → alert for one detection × profile pair."""

        # 3a. Strategy Engine
        decision = self._strategy.evaluate(det_output, profile, self._strategy_cfg, pip_size=instrument.pip_size)

        if not decision.is_trade:
            logger.debug(
                '{"event":"strategy_no_candidate","symbol":"%s","timeframe":"%s",'
                '"profile":"%s","outcome":"%s","reason":"%s"}',
                instrument.symbol, instrument.timeframe, profile.name,
                decision.outcome, decision.reason[:120],
            )
            return

        candidate = decision.candidate

        # Inject FVG result into the candidate confidence_inputs so the
        # Confidence Engine can score it as fvg_present_after_sweep.
        if fvg_present is not None:
            # confidence_inputs is a dict on the candidate; update it in-place
            # via object.__setattr__ since TradeCandidate may be frozen.
            try:
                candidate.confidence_inputs["fvg_present_after_sweep"] = fvg_present
            except (AttributeError, TypeError):
                pass  # If frozen, scoring will fall back to None (UNKNOWN)

        if parent_at_htf_zone is not None:
            try:
                candidate.confidence_inputs["parent_at_htf_zone"] = parent_at_htf_zone
                if key_level_evaluation is not None:
                    candidate.confidence_inputs["key_level_evaluation"] = (
                        key_level_evaluation.to_dict()
                    )
            except (AttributeError, TypeError):
                pass

        # 3b. Confidence Engine
        conf_score = self._conf_engine.score(candidate)

        logger.info(
            '{"event":"candidate_scored","candidate_id":"%s","profile":"%s",'
            '"score":%.2f,"missing":%s}',
            candidate.candidate_id, profile.name,
            conf_score.final_score, list(conf_score.missing_factors),
        )

        # 3c. FVG Two-Alert Flow
        telegram_channel = next(
            (c for c in self._alert_engine._channels if c.name == "telegram"), None
        )

        # A watched FVG is an explicitly enabled delayed-entry workflow. With
        # the approved default gate disabled, FVG remains confluence evidence
        # and the CRT follows the ordinary immediate alert/execution path.
        fvg_execution_gated = bool(
            fvg_result is not None
            and fvg_result.found
            and ltf_tf
            and getattr(self, "_fvg_cfg", None) is not None
            and getattr(self._fvg_cfg, "enabled", False)
            and getattr(self._fvg_cfg, "execution_gate_enabled", False)
        )
        quality_status = getattr(quality_tightening, "status", "UNKNOWN")
        quality_would_suppress = bool(
            getattr(quality_tightening, "would_suppress", True)
        )

        # Signals belong to the alert path and must not wait for MT5 account
        # state, risk approval, or execution readiness.
        if fvg_execution_gated:
            logger.info(
                '{"event":"fvg_alert1_dispatch","candidate_id":"%s","symbol":"%s",'
                '"htf":"%s","ltf":"%s","direction":"%s"}',
                candidate.candidate_id, instrument.symbol,
                instrument.timeframe, ltf_tf, fvg_result.direction,
            )
            if telegram_channel and telegram_channel.is_enabled and hasattr(telegram_channel, "broadcast_fvg_watch"):
                fvg_mid = str(fvg_result.midpoint) if fvg_result.midpoint else str(fvg_result.fvg_low)
                telegram_channel.broadcast_fvg_watch(
                    symbol=instrument.symbol,
                    htf_timeframe=instrument.timeframe,
                    ltf_timeframe=ltf_tf,
                    direction=fvg_result.direction,
                    fvg_high=str(fvg_result.fvg_high),
                    fvg_low=str(fvg_result.fvg_low),
                    fvg_midpoint=fvg_mid,
                    invalidation_level=str(candidate.invalidation_model.invalidation_level),
                    midpoint_target=str(candidate.target_model.midpoint_target),
                    opposite_extreme_target=str(candidate.target_model.opposite_extreme_target),
                    confidence_score=conf_score.final_score,
                    profile=profile.name,
                    candidate_id=candidate.candidate_id,
                    parent_at_htf_zone=parent_at_htf_zone,
                    profile_min_confidence=profile.min_confidence_threshold,
                    quality_status=quality_status,
                    quality_would_suppress=quality_would_suppress,
                    atr_quality_config=getattr(self, "_atr_quality_cfg", None),
                )
                alert_delivery_status = (
                    "delivered"
                    if getattr(telegram_channel, "last_delivery_records", None)
                    else "suppressed"
                )
                if getattr(telegram_channel, "last_delivery_status", "suppressed") == "retryable":
                    self._current_detection_delivery_retryable = True
            else:
                # A missing FVG-specific delivery adapter must not suppress a
                # valid ordinary alert.
                alert_event = self._alert_engine.process(
                    candidate,
                    conf_score,
                    profile,
                    atr_quality=atr_quality,
                    quality_tightening=quality_tightening,
                )
                alert_delivery_status = getattr(
                    self._alert_engine, "last_telegram_delivery_status", "unavailable"
                )
                if getattr(self._alert_engine, "last_delivery_status", "suppressed") == "retryable":
                    self._current_detection_delivery_retryable = True
        else:
            alert_event = self._alert_engine.process(
                candidate,
                conf_score,
                profile,
                atr_quality=atr_quality,
                quality_tightening=quality_tightening,
            )
            alert_delivery_status = getattr(
                self._alert_engine, "last_telegram_delivery_status", "unavailable"
            )
            if getattr(self._alert_engine, "last_delivery_status", "suppressed") == "retryable":
                self._current_detection_delivery_retryable = True

        # A risk-approved candidate is not enough for automatic entry. When
        # enabled, Telegram must have accepted the alert before MT5 risk and
        # approval can create an executable request.
        execution_cfg = getattr(self, "_execution_cfg", None)
        if execution_cfg is not None and getattr(
            execution_cfg, "require_alert_eligibility", True
        ):
            if alert_delivery_status != "delivered":
                logger.warning(
                    '{"event":"execution_blocked_alert_not_eligible",'
                    '"candidate_id":"%s","symbol":"%s",'
                    '"timeframe":"%s","alert_status":"%s",'
                    '"alert_event_built":%s}',
                    candidate.candidate_id,
                    candidate.symbol,
                    candidate.timeframe,
                    alert_delivery_status,
                    str(alert_event is not None).lower(),
                )
                return

        # 3c. Risk Engine
        if mt5 is None:
            logger.warning('{"event":"risk_evaluation_skipped","reason":"mt5_not_installed"}')
            return
        account_info = mt5.account_info()
        positions = mt5.positions_get()
        if account_info is None or positions is None:
            logger.warning('{"event":"mt5_account_fetch_failed","reason":"not logged in or connection error"}')
            return

        account_state = self._build_live_risk_account_state(
            account_info,
            positions,
            candidate.symbol,
        )

        mode_state = load_account_mode_state(self._runtime_store)
        prop_firm_context = None
        loss_budget = None
        enforced_modes = {
            TradingAccountMode.PROP_FIRM_ENFORCED,
            TradingAccountMode.AUTO_ENFORCED,
        }
        if mode_state.mode in enforced_modes or (
            mode_state.mode is TradingAccountMode.NORMAL_ACCOUNT
            and bool(getattr(self._account_mode_cfg, "auto_enforce_when_bound", False))
        ):
            prop_firm_context = self._resolve_prop_firm_context(
                candidate,
                account_state,
                positions=positions,
            )
            mode_state = load_account_mode_state(self._runtime_store)
            if self._circuit_breaker is not None and prop_firm_context is not None:
                loss_budget = evaluate_loss_budget(
                    prop_firm_context,
                    as_of=datetime.now(timezone.utc),
                )
                if not self._persist_prop_firm_recovery_state(
                    prop_firm_context,
                    loss_budget,
                ):
                    logger.warning(
                        '{"event":"prop_firm_recovery_persistence_blocked",'
                        '"candidate_id":"%s"}',
                        candidate.candidate_id,
                    )
                    return

        if getattr(self, '_navigator', None) and loss_budget and prop_firm_context:
            nav_action = self._navigator.evaluate_navigator(loss_budget, prop_firm_context)
            if nav_action.halt_trading:
                logger.warning(
                    '{"event":"navigator_circuit_breaker_tripped","reason":"%s"}',
                    nav_action.reason[:200],
                )
                # Persist halt flag so it survives restarts and is visible via watchdog
                self._runtime_store.set(
                    "navigator.halt_trading",
                    {"halted": True, "reason": nav_action.reason, "at": datetime.now(timezone.utc).isoformat()},
                )
                return
            if nav_action.enforce_scalper:
                logger.info(
                    '{"event":"navigator_coast_mode","reason":"%s","current_profile":"%s"}',
                    nav_action.reason[:200], profile.name,
                )
                if profile.name != 'scalper':
                    # Coast mode: silently skip all non-scalper profiles this cycle
                    return

        circuit_decision = self._evaluate_circuit_breaker(
            account_info=account_info,
            account_state=account_state,
            mode_state=mode_state,
            prop_firm_context=prop_firm_context,
            loss_budget=loss_budget,
            telegram_channel=telegram_channel,
        )
        if circuit_decision is not None and not circuit_decision.allowed:
            risk_eval = self._risk_engine.reject_external(
                candidate,
                circuit_decision.reason_code,
                circuit_decision.reason,
            )
            logger.warning(
                '{"event":"circuit_breaker_blocked","candidate_id":"%s",'
                '"reason_code":"%s"}',
                candidate.candidate_id,
                circuit_decision.reason_code,
            )
        elif mode_state.mode in enforced_modes and prop_firm_context is None:
            reason = (
                "Prop-Firm enforcement requires a verified account, profile, "
                "and current risk context. New entry blocked."
            )
            risk_eval = self._risk_engine.reject_external(
                candidate,
                "PROP_FIRM_CONTEXT_UNKNOWN",
                reason,
            )
            logger.warning(
                '{"event":"prop_firm_enforcement_blocked","candidate_id":"%s",'
                '"reason":"%s"}',
                candidate.candidate_id,
                reason,
            )
        else:
            risk_eval = self._risk_engine.evaluate(
                candidate,
                self._risk_cfg,
                account_state,
                prop_firm_context,
            )
        if mode_state.mode is TradingAccountMode.PROP_FIRM_SHADOW:
            self._observe_prop_firm_shadow(candidate, account_state, risk_eval)
        
        if not risk_eval.approved:
            logger.info(
                '{"event":"risk_rejected","candidate_id":"%s","profile":"%s",'
                '"reason":"%s"}',
                candidate.candidate_id, profile.name, risk_eval.reason
            )
            return

        logger.info(
            '{"event":"risk_approved","candidate_id":"%s","profile":"%s",'
            '"lot_size":"%s"}',
            candidate.candidate_id, profile.name, str(risk_eval.lot_size)
        )

        # 3d. Approval Engine
        if getattr(self, '_simulator', None):
            self._simulator.simulate_entry(candidate, account_state, self._risk_cfg, prop_firm_context)
            
        request = ExecutionRequest(
            candidate=candidate,
            risk_evaluation=risk_eval,
            magic_number=self._execution_cfg.magic_number,
            requested_at=datetime.now(timezone.utc)
        )

        pending_trade = self._approval_engine.submit_for_approval(request)

        # 3e. FVG Two-Alert Flow & Execution Branching
        if fvg_execution_gated:
            # --- Alert 1: CRT detected + LTF FVG found → tell traders to wait ---
            # Register watch for Alert 2
            self._fvg_watcher.add_watch_full(
                symbol=instrument.symbol,
                fvg=fvg_result,
                candidate_id=candidate.candidate_id,
                confidence_score=conf_score.final_score,
                profile=profile.name,
                entry_zone_high=candidate.entry_model.entry_zone_high,
                entry_zone_low=candidate.entry_model.entry_zone_low,
                invalidation_level=candidate.invalidation_model.invalidation_level,
                midpoint_target=candidate.target_model.midpoint_target,
                opposite_extreme_target=candidate.target_model.opposite_extreme_target,
                htf_timeframe=instrument.timeframe,
                parent_at_htf_zone=parent_at_htf_zone,
                profile_min_confidence=profile.min_confidence_threshold,
                quality_status=quality_status,
                quality_would_suppress=quality_would_suppress,
            )
            logger.info('{"event":"fvg_watch_registered_awaiting_execution","candidate_id":"%s"}', candidate.candidate_id)

        else:
            # No FVG found → fire the regular CRT alert and execute immediately if approved
            if pending_trade.status == ApprovalStatus.APPROVED:
                logger.info('{"event":"auto_approved","candidate_id":"%s"}', candidate.candidate_id)
                record = self._execution_engine.execute(request)
                self._persist_execution_record(record)
                logger.info(
                    '{"event":"execution_attempted","candidate_id":"%s","status":"%s"}',
                    candidate.candidate_id, record.status
                )
                self._try_register_partial_watch(record, request)
            else:
                logger.info(
                    '{"event":"pending_approval","candidate_id":"%s","status":"%s"}',
                    candidate.candidate_id, pending_trade.status
                )

    # ------------------------------------------------------------------
    # Trade Manager — Partial Profit Registration Helper
    # ------------------------------------------------------------------

    def _persist_execution_record(self, record) -> None:
        """Persist every execution attempt for restart-safe reconciliation."""
        try:
            self._journal.record_execution(record)
        except Exception as exc:  # noqa: BLE001 - execution result is not hidden
            logger.error(
                '{"event":"execution_record_persist_failed","record_id":"%s",'
                '"error":"%s"}',
                str(getattr(record, "record_id", ""))[:80],
                str(exc)[:160],
            )

    def _record_execution_quality_event(self, event: ExecutionQualityEvent) -> None:
        """Persist a broker or operations fact without changing runtime behavior."""
        recorder = getattr(
            getattr(self, "_journal", None),
            "record_execution_quality_event",
            None,
        )
        if not callable(recorder):
            logger.warning(
                '{"event":"execution_quality_recorder_unavailable",'
                '"quality_event_type":"%s"}',
                getattr(event, "event_type", "UNKNOWN"),
            )
            return
        try:
            recorder(event)
        except Exception as exc:  # noqa: BLE001 - quality telemetry cannot block trading
            logger.error(
                '{"event":"execution_quality_record_failed",'
                '"quality_event_type":"%s","error":"%s"}',
                getattr(event, "event_type", "UNKNOWN"),
                str(exc)[:160],
            )

    @staticmethod
    def _current_spread(symbol: str) -> Optional[Decimal]:
        """Return broker-reported bid/ask spread when both sides are valid."""
        try:
            if mt5 is None:
                return None
            broker_symbol = resolve_mt5_symbol(mt5, symbol)
            if broker_symbol is None:
                return None
            tick = mt5.symbol_info_tick(broker_symbol)
            bid = Decimal(str(getattr(tick, "bid")))
            ask = Decimal(str(getattr(tick, "ask")))
            spread = ask - bid
            if not bid.is_finite() or not ask.is_finite() or spread < 0:
                return None
            return spread
        except (AttributeError, TypeError, ValueError, ArithmeticError):
            return None

    def _record_position_link_quality(self, record, request, position) -> None:
        """Record actual fill facts discovered when the broker position is linked."""
        try:
            position_id = getattr(
                position,
                "identifier",
                getattr(position, "ticket", None),
            )
            actual_price = Decimal(str(position.price_open))
            actual_volume = Decimal(str(position.volume))
            requested_price = (
                Decimal(str(record.mt5_request.price))
                if getattr(record.mt5_request, "price", None) is not None
                else None
            )
            slippage = (
                abs(actual_price - requested_price)
                if requested_price is not None
                else None
            )
            self._record_execution_quality_event(
                ExecutionQualityEvent.new(
                    ExecutionQualityEventType.POSITION_LINKED,
                    execution_record_id=str(record.record_id),
                    candidate_id=str(request.candidate.candidate_id),
                    profile=str(request.candidate.profile),
                    symbol=str(request.candidate.symbol),
                    timeframe=str(request.candidate.timeframe),
                    direction=str(getattr(request.candidate.direction, "value", request.candidate.direction)),
                    position_id=int(position_id) if position_id is not None else None,
                    requested_price=(str(requested_price) if requested_price is not None else None),
                    actual_price=str(actual_price),
                    requested_volume=getattr(record.mt5_request, "volume", None),
                    actual_volume=str(actual_volume),
                    spread=(
                        str(spread)
                        if (spread := self._current_spread(request.candidate.symbol)) is not None
                        else None
                    ),
                    slippage=(str(slippage) if slippage is not None else None),
                    stop_loss=str(getattr(position, "sl", "") or "") or None,
                    take_profit=str(getattr(position, "tp", "") or "") or None,
                    broker_retcode=record.order_send_retcode,
                    broker_comment=str(record.broker_comment or "")[:240] or None,
                    status="LINKED",
                    latency_ms=max(
                        0,
                        int((record.completed_at - record.attempted_at).total_seconds() * 1000),
                    ),
                    occurred_at=datetime.now(timezone.utc),
                    metadata={"link_method": "scanner_position_discovery"},
                )
            )
        except (AttributeError, TypeError, ValueError, ArithmeticError) as exc:
            logger.warning(
                '{"event":"execution_quality_position_link_unknown",'
                '"record_id":"%s","error":"%s"}',
                str(getattr(record, "record_id", ""))[:80],
                str(exc)[:160],
            )

    def _try_register_partial_watch(self, record, request) -> None:
        """
        After a successful execution, find the open MT5 position by magic number
        and register it with TradeManager for partial-profit monitoring.

        Uses mt5.positions_get(magic=...) to find the ticket since the order_id
        returned by order_send may be the order ticket, not the position ticket.
        """
        from core.models.execution import ExecutionStatus
        if record.status not in (ExecutionStatus.FILLED, ExecutionStatus.SENT,
                                  ExecutionStatus.PARTIALLY_FILLED):
            return  # Not filled — nothing to track

        try:
            import MetaTrader5 as mt5
            from decimal import Decimal

            broker_symbol = resolve_mt5_symbol(mt5, request.candidate.symbol)
            if broker_symbol is None:
                logger.warning(
                    '{"event":"partial_watch_symbol_unavailable",'
                    '"candidate_id":"%s","symbol":"%s"}',
                    request.candidate.candidate_id,
                    request.candidate.symbol,
                )
                return
            all_positions = mt5.positions_get(symbol=broker_symbol)
            positions = tuple(
                position
                for position in (all_positions or ())
                if int(
                    getattr(position, "magic", self._execution_cfg.magic_number)
                    or self._execution_cfg.magic_number
                ) == self._execution_cfg.magic_number
            )
            if not positions:
                logger.warning(
                    '{"event":"partial_watch_no_position","candidate_id":"%s"}',
                    request.candidate.candidate_id,
                )
                return

            # Find the most recently opened position (highest ticket = latest)
            position = max(positions, key=lambda p: p.ticket)
            position_id = getattr(
                position,
                "identifier",
                getattr(position, "ticket", None),
            )
            if position_id is None:
                logger.warning(
                    '{"event":"execution_position_link_unknown","record_id":"%s"}',
                    str(getattr(record, "record_id", ""))[:80],
                )
            else:
                linked = False
                try:
                    linked = self._journal.link_execution_position(
                        record.record_id,
                        int(position_id),
                    )
                except Exception as exc:  # noqa: BLE001 - reconciliation fails closed later
                    logger.warning(
                        '{"event":"execution_position_link_failed","record_id":"%s",'
                        '"error":"%s"}',
                        str(getattr(record, "record_id", ""))[:80],
                        str(exc)[:160],
                    )
                if linked:
                    self._record_position_link_quality(record, request, position)

            self._trade_manager.register(
                ticket         = position.ticket,
                symbol         = request.candidate.symbol,
                direction      = str(
                    getattr(request.candidate.direction, "value", request.candidate.direction)
                ).upper(),
                entry_price    = Decimal(str(position.price_open)),
                midpoint       = Decimal(str(request.candidate.target_model.midpoint_target)),
                extreme_target = Decimal(str(request.candidate.target_model.opposite_extreme_target)),
                lot_size       = request.risk_evaluation.lot_size,
                magic_number   = self._execution_cfg.magic_number,
                original_stop  = Decimal(
                    str(request.candidate.invalidation_model.invalidation_level)
                ),
                execution_record_id = record.record_id,
                candidate_id   = request.candidate.candidate_id,
                timeframe      = request.candidate.timeframe,
                profile        = request.candidate.profile,
            )
        except Exception as exc:
            logger.warning(
                '{"event":"partial_watch_register_failed","error":"%s"}', str(exc)[:120]
            )

    # ------------------------------------------------------------------
    # AI Advisory — Daily Report
    # ------------------------------------------------------------------

    def _maybe_send_daily_advisory(self) -> None:
        """
        Check if it is time to fire the end-of-day AI advisory report.
        Fires once per UTC calendar day at the configured hour and minute.
        Authority: docs/12_ANALYTICS_ENGINE.md
        """
        if self._advisory_engine is None:
            return

        today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if self._last_report_date == today_str:
            return
        if not self._advisory_engine.is_report_time():
            return

        logger.info('{"event":"ai_advisory_triggered","date":"%s"}', today_str)
        self._last_report_date = today_str

        try:
            report = self._advisory_engine.generate_daily_report()
            if report:
                telegram_channel = next(
                    (c for c in self._alert_engine._channels if c.name == "telegram"), None
                )
                if telegram_channel and telegram_channel.is_enabled:
                    telegram_channel.broadcast_advisory(report)
                    logger.info('{"event":"ai_advisory_dispatched","date":"%s"}', today_str)
                else:
                    logger.warning('{"event":"ai_advisory_no_telegram","date":"%s"}', today_str)
            else:
                logger.info('{"event":"ai_advisory_empty","date":"%s"}', today_str)
        except Exception as exc:  # noqa: BLE001
            logger.error(
                '{"event":"ai_advisory_error","date":"%s","error":"%s"}',
                today_str, str(exc)[:200],
            )

    # ------------------------------------------------------------------
    # AI Advisory — Weekly Strategy Optimizer
    # ------------------------------------------------------------------

    def _maybe_send_weekly_advisory(self) -> None:
        """
        Check if it is time to fire the weekly strategy optimizer report.
        Fires once per week on the configured UTC weekday at the configured hour.
        Authority: docs/12_ANALYTICS_ENGINE.md
        """
        if self._advisory_engine is None:
            return
        if not self._advisory_engine.is_weekly_report_time():
            return

        week_key = datetime.now(timezone.utc).strftime("%Y-W%W")
        if getattr(self, "_last_weekly_report_key", None) == week_key:
            return  # Already fired this week

        self._last_weekly_report_key = week_key
        logger.info('{"event":"ai_weekly_triggered","week":"%s"}', week_key)

        try:
            report = self._advisory_engine.generate_weekly_report()
            if report:
                telegram_channel = next(
                    (c for c in self._alert_engine._channels if c.name == "telegram"), None
                )
                if telegram_channel and telegram_channel.is_enabled:
                    telegram_channel.broadcast_advisory(report)
                    logger.info('{"event":"ai_weekly_dispatched","week":"%s"}', week_key)
            else:
                logger.info('{"event":"ai_weekly_empty","week":"%s"}', week_key)
        except Exception as exc:  # noqa: BLE001
            logger.error(
                '{"event":"ai_weekly_error","week":"%s","error":"%s"}',
                week_key, str(exc)[:200],
            )

    # ------------------------------------------------------------------
    # AI Advisory — Anomaly Detection
    # ------------------------------------------------------------------

    def _check_performance_anomaly(self) -> None:
        """
        Compare today's performance against the 30-day baseline.
        Fires an immediate Telegram alert if win rate drops significantly.
        Runs every N cycles (roughly once per hour) to avoid over-alerting.
        Authority: docs/12_ANALYTICS_ENGINE.md
        """
        if self._advisory_engine is None:
            return

        # Only check every 15 cycles (~15 min if poll_interval=60s) to limit API calls
        if self._cycle_count % 15 != 0:
            return

        try:
            alert = self._advisory_engine.check_for_anomaly()
            if alert:
                telegram_channel = next(
                    (c for c in self._alert_engine._channels if c.name == "telegram"), None
                )
                if telegram_channel and telegram_channel.is_enabled:
                    telegram_channel.broadcast_advisory(alert)
                    logger.warning('{"event":"ai_anomaly_alert_sent"}')
        except Exception as exc:  # noqa: BLE001
            logger.error(
                '{"event":"ai_anomaly_check_error","error":"%s"}', str(exc)[:200]
            )

    # ------------------------------------------------------------------
    # Signal handling
    # ------------------------------------------------------------------

    def _register_signal_handlers(self) -> None:
        """Register SIGINT and SIGTERM for graceful shutdown."""
        def _handle(signum: int, _frame: object) -> None:
            logger.info(
                '{"event":"scanner_signal","signal":%d}', signum
            )
            self.stop()

        signal.signal(signal.SIGINT, _handle)
        signal.signal(signal.SIGTERM, _handle)
