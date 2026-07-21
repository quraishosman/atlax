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
import signal
import time
from datetime import datetime, timezone
from typing import Optional

from core.alerts.alert_engine import AlertEngine
from core.confidence.confidence_engine import ConfidenceEngine
from core.detectors.crt.detector import CRTDetector
from core.market_data.mt5_candle_feed import MT5CandleFeed
from core.market_data.mt5_connection import MT5Connection, MT5ConnectionError
from core.settings.alert_config import AlertConfig
from core.settings.confidence_config import ConfidenceConfig
from core.settings.crt_config import CRTConfig
from core.settings.market_data_config import MarketDataConfig, WatchedInstrument
from core.settings.strategy_config import ProfileConfig, StrategyConfig
from core.strategy.crt_strategy import CRTStrategy

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
        alert_cfg: AlertConfig,
    ) -> None:
        self._market_cfg   = market_cfg
        self._detector     = CRTDetector(crt_cfg)
        self._strategy     = CRTStrategy()
        self._conf_engine  = ConfidenceEngine(conf_cfg)
        self._alert_engine = AlertEngine(alert_cfg)
        self._strategy_cfg = strategy_cfg
        self._running      = False
        self._cycle_count  = 0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

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

        connection = MT5Connection(self._market_cfg.mt5)
        try:
            connection.initialize()
        except MT5ConnectionError as exc:
            logger.error(
                '{"event":"scanner_mt5_connect_failed","reason":"%s"}', str(exc)
            )
            return

        try:
            with connection:
                while self._running:
                    cycle_start = time.monotonic()
                    self._run_cycle()
                    elapsed = time.monotonic() - cycle_start
                    sleep_time = max(
                        0, self._market_cfg.poll_interval_seconds - elapsed
                    )
                    if self._running:
                        time.sleep(sleep_time)
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

    def run_once(self) -> None:
        """
        Run a single scan cycle without connecting to MT5.
        Used for testing with a mock feed.
        """
        self._run_cycle()

    # ------------------------------------------------------------------
    # Core cycle
    # ------------------------------------------------------------------

    def _run_cycle(self) -> None:
        """
        Execute one full scan across all instruments and all profiles.
        Errors in one instrument never abort the others.
        """
        self._cycle_count += 1
        cycle_ts = datetime.now(timezone.utc).isoformat()
        logger.info(
            '{"event":"scan_cycle_start","cycle":%d,"at":"%s","instruments":%d}',
            self._cycle_count, cycle_ts, len(self._market_cfg.instruments),
        )

        for instrument in self._market_cfg.instruments:
            try:
                self._process_instrument(instrument)
            except Exception as exc:  # noqa: BLE001
                logger.error(
                    '{"event":"instrument_error","symbol":"%s","timeframe":"%s",'
                    '"error":"%s"}',
                    instrument.symbol, instrument.timeframe, str(exc)[:200],
                )

        logger.info(
            '{"event":"scan_cycle_done","cycle":%d}', self._cycle_count
        )

    def _process_instrument(self, instrument: WatchedInstrument) -> None:
        """
        Run the full detection + strategy + confidence + alert pipeline
        for one instrument against all active profiles.
        """
        from decimal import Decimal

        # 1. Fetch candles and ATR from MT5
        feed = MT5CandleFeed(instrument)
        seq  = feed.get_candles()
        atr  = feed.get_atr()

        logger.debug(
            '{"event":"instrument_fetched","symbol":"%s","timeframe":"%s",'
            '"candles":%d,"atr":"%s"}',
            instrument.symbol, instrument.timeframe, len(seq.candles), str(atr),
        )

        # 2. Run CRT Detector
        det_output = self._detector.detect(seq, atr=atr)

        if not det_output.detected:
            logger.debug(
                '{"event":"no_detection","symbol":"%s","timeframe":"%s",'
                '"reason":"%s"}',
                instrument.symbol, instrument.timeframe,
                det_output.reason[:120],
            )
            return  # Nothing to route — skip all profiles

        logger.info(
            '{"event":"crt_detected","symbol":"%s","timeframe":"%s",'
            '"classification":"%s"}',
            instrument.symbol, instrument.timeframe, det_output.classification,
        )

        # 3. Route to each active profile
        for profile in self._strategy_cfg.active_profiles:
            self._process_profile(det_output, profile, instrument)

    def _process_profile(
        self,
        det_output,
        profile: ProfileConfig,
        instrument: WatchedInstrument,
    ) -> None:
        """Run strategy → confidence → alert for one detection × profile pair."""

        # 3a. Strategy Engine
        decision = self._strategy.evaluate(det_output, profile, self._strategy_cfg)

        if not decision.is_trade:
            logger.debug(
                '{"event":"strategy_no_candidate","symbol":"%s","timeframe":"%s",'
                '"profile":"%s","outcome":"%s","reason":"%s"}',
                instrument.symbol, instrument.timeframe, profile.name,
                decision.outcome, decision.reason[:120],
            )
            return

        candidate = decision.candidate

        # 3b. Confidence Engine
        conf_score = self._conf_engine.score(candidate)

        logger.info(
            '{"event":"candidate_scored","candidate_id":"%s","profile":"%s",'
            '"score":%.2f,"missing":%s}',
            candidate.candidate_id, profile.name,
            conf_score.final_score, list(conf_score.missing_factors),
        )

        # 3c. Alert Engine
        self._alert_engine.process(candidate, conf_score, profile)

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
