"""
ATLAX Volume Profile Engine.

Public entry point for the Volume Profile Confluence feature. Wraps
VolumeProfileCalculator and period-boundary logic into a single call
per symbol + period combination.

The engine caches deterministic completed-period results. Cache keys include
the period boundary and the complete configuration snapshot so a configuration
change cannot reuse stale evidence.

Architecture:
    - Receives closed candles from the Market Data Layer.
    - Delegates calculation to VolumeProfileCalculator.
    - Returns one VolumeProfileResult per period.
    - Never detects CRTs, decides delivery, or executes trades.
    - UNKNOWN results must not suppress any existing CRT alert.

Authority:
    docs/40_VOLUME_PROFILE_CONFLUENCE_PLAN.md §2, §9 Phase 2
    docs/decisions/ADR-017.md
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from decimal import Decimal
from typing import Dict, List, Optional

from core.analytics.volume_profile_calculator import (
    VolumeProfileCalculator,
    config_snapshot_id,
)
from core.analytics.volume_profile_periods import (
    APPROVED_PERIODS,
    candles_in_period,
    period_boundaries,
)
from core.models.volume_profile_result import VolumeProfileResult, VolumeProfileStatus
from core.settings.volume_profile_config import VolumeProfileConfig

logger = logging.getLogger("atlax.analytics.volume_profile_engine")


class VolumeProfileEngine:
    """
    Entry point for Volume Profile evaluation.

    Evaluate one or more profile periods for a single symbol. Returns
    a dict mapping period_label → VolumeProfileResult.

    Args:
        config: A validated VolumeProfileConfig loaded from settings.

    Example::

        engine = VolumeProfileEngine(config)
        results = engine.evaluate(
            symbol="EURUSD",
            instrument_class="forex_non_jpy",
            candles=candle_sequence.candles,
            tick_size=Decimal("0.00001"),
            reference_utc=datetime.now(timezone.utc),
        )
        prev_day = results.get("PREVIOUS_DAY")
    """

    def __init__(self, config: VolumeProfileConfig) -> None:
        self._config = config
        self._calc = VolumeProfileCalculator(config)
        self._cache: dict[tuple, VolumeProfileResult] = {}
        self._last_cache_hits: tuple[str, ...] = ()

    @property
    def configuration_snapshot_id(self) -> str:
        """Return the snapshot identifier used for cache and Passport data."""
        return config_snapshot_id(self._config)

    @property
    def last_cache_hits(self) -> tuple[str, ...]:
        """Return period labels served from cache by the last evaluation."""
        return self._last_cache_hits

    def evaluate(
        self,
        *,
        symbol: str,
        instrument_class: str,
        candles: tuple,
        tick_size: Decimal,
        reference_utc: Optional[datetime] = None,
        periods: Optional[List[str]] = None,
    ) -> Dict[str, VolumeProfileResult]:
        """
        Evaluate Volume Profile for all requested periods.

        Args:
            symbol: Instrument identifier (e.g. ``EURUSD``).
            instrument_class: One of ``forex_non_jpy``, ``forex_jpy``,
                ``gold``.  Used to resolve source timeframe and bin size.
            candles: Closed source candles (any period; engine filters
                to the correct period window internally).
            tick_size: Instrument tick size in price units.
            reference_utc: "Now" reference. Defaults to
                ``datetime.now(timezone.utc)``. Pass an explicit value
                in tests and replay to ensure determinism.
            periods: List of period labels to evaluate. Defaults to
                ``config.profile_periods``.

        Returns:
            Dict mapping period_label → VolumeProfileResult.
            Keys are always present for every requested period, even when
            the result is UNKNOWN.
        """
        cfg = self._config
        self._last_cache_hits = ()
        snapshot_id = self.configuration_snapshot_id

        # Resolve reference time
        ref = reference_utc or datetime.now(timezone.utc)
        if ref.tzinfo is None:
            ref = ref.replace(tzinfo=timezone.utc)

        # Resolve periods
        requested = periods or list(cfg.profile_periods)

        # Resolve bin size for this instrument class / symbol
        bin_size_ticks = cfg.bin_size_ticks_symbol_overrides.get(
            symbol,
            cfg.bin_size_ticks_by_instrument_class.get(instrument_class, 0),
        )

        # Resolve source timeframe for this instrument class / symbol
        source_tf = cfg.source_timeframe_symbol_overrides.get(
            symbol,
            cfg.source_timeframe_by_instrument_class.get(instrument_class, ""),
        )

        results: Dict[str, VolumeProfileResult] = {}

        for period_label in requested:
            if period_label not in APPROVED_PERIODS:
                logger.warning(
                    '{"event":"volume_profile_unknown_period","symbol":"%s",'
                    '"period":"%s"}',
                    symbol, period_label,
                )
                continue

            try:
                start_utc, end_utc = period_boundaries(period_label, ref)
            except ValueError as exc:
                logger.error(
                    '{"event":"volume_profile_period_boundary_error","symbol":"%s",'
                    '"period":"%s","error":"%s"}',
                    symbol, period_label, str(exc),
                )
                continue

            cache_key = (
                symbol,
                instrument_class,
                period_label,
                start_utc.isoformat(),
                end_utc.isoformat(),
                source_tf,
                cfg.volume_source,
                cfg.real_volume_fallback,
                int(bin_size_ticks),
                cfg.value_area_percent,
                cfg.allocation_method,
                snapshot_id,
            )
            cached = self._cache.get(cache_key)
            if cached is not None:
                self._last_cache_hits = (*self._last_cache_hits, period_label)
                logger.debug(
                    '{"event":"volume_profile_cache_hit","symbol":"%s",'
                    '"period":"%s","snapshot_id":"%s"}',
                    symbol,
                    period_label,
                    snapshot_id,
                )
                results[period_label] = cached
                continue

            period_candles = candles_in_period(candles, start_utc, end_utc)

            logger.debug(
                '{"event":"volume_profile_period_candles","symbol":"%s",'
                '"period":"%s","period_start":"%s","period_end":"%s",'
                '"candle_count":%d,"source_tf":"%s","bin_size_ticks":%d}',
                symbol, period_label,
                start_utc.isoformat(), end_utc.isoformat(),
                len(period_candles), source_tf, bin_size_ticks,
            )

            result = self._calc.calculate(
                symbol=symbol,
                period_label=period_label,
                period_start_utc=start_utc,
                period_end_utc=end_utc,
                candles=period_candles,
                tick_size=tick_size,
                bin_size_ticks=bin_size_ticks,
                source_timeframe=source_tf,
            )
            self._cache[cache_key] = result
            results[period_label] = result

        return results
