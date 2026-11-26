"""
ATLAX Volume Profile Calculator.

Stateless, deterministic Volume Profile engine. Given a symbol,
a completed profile period, a tuple of closed source candles, a tick
size, and a validated VolumeProfileConfig, it returns a
VolumeProfileResult with POC, VAH, and VAL — or UNKNOWN when the
data contract cannot be satisfied.

Responsibilities:
    - Row construction from closed candles.
    - RANGE_UNIFORM volume allocation (V1 approved method).
    - POC selection with deterministic tie-breaking.
    - Value-area expansion with deterministic tie-breaking.
    - Full UNKNOWN handling without fallback to price-only profiles.
    - Full structured logging for every evaluation.

Architecture boundaries:
    - Pure calculation layer. Receives candles; never fetches data.
    - Never modifies CRT detector behavior.
    - Never decides signal delivery, execution, or routing.
    - Volume Profile UNKNOWN must not suppress any CRT alert.

Authority:
    docs/40_VOLUME_PROFILE_CONFLUENCE_PLAN.md §5 (calculation contract)
    docs/decisions/ADR-017.md
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Dict, List, Optional, Tuple

from core.models.volume_source import VolumeQuality, VolumeSource
from core.models.volume_profile_result import (
    VolumeProfileLevel,
    VolumeProfileLevels,
    VolumeProfileResult,
    VolumeProfileStatus,
)
from core.settings.volume_profile_config import VolumeProfileConfig

logger = logging.getLogger("atlax.analytics.volume_profile")

# ---------------------------------------------------------------------------
# Internal row representation
# ---------------------------------------------------------------------------


@dataclass
class _Row:
    """A single price bin accumulating allocated volume."""
    price: Decimal          # lower bound of this bin
    volume: Decimal = Decimal("0")


# ---------------------------------------------------------------------------
# Snapshot ID helper
# ---------------------------------------------------------------------------


def config_snapshot_id(config: VolumeProfileConfig) -> str:
    """Return a 16-char hex ID uniquely identifying this config version.

    Used to prevent a cached profile built with an old config from being
    reused after a live reload (Doc 40 §9 Phase 3).
    """
    payload = json.dumps(
        {
            "enabled": config.enabled,
            "shadow_mode": config.shadow_mode,
            "version": config.version,
            "volume_source": config.volume_source,
            "real_volume_fallback": config.real_volume_fallback,
            "profile_periods": list(config.profile_periods),
            "source_timeframe_by_instrument_class": config.source_timeframe_by_instrument_class,
            "source_timeframe_symbol_overrides": config.source_timeframe_symbol_overrides,
            "value_area_percent": config.value_area_percent,
            "bin_size_ticks_by_instrument_class": config.bin_size_ticks_by_instrument_class,
            "bin_size_ticks_symbol_overrides": config.bin_size_ticks_symbol_overrides,
            "allocation_method": config.allocation_method,
            "minimum_source_bars": config.minimum_source_bars,
            "minimum_valid_volume_bars": config.minimum_valid_volume_bars,
            "use_current_period": config.use_current_period,
            "confidence_factor_enabled": config.confidence_factor_enabled,
            "confidence_factor_weight": config.confidence_factor_weight,
            "require_for_premium_confluence": config.require_for_premium_confluence,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(payload).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Main calculator
# ---------------------------------------------------------------------------


class VolumeProfileCalculator:
    """
    Stateless Volume Profile calculator.

    Usage::

        calc = VolumeProfileCalculator(config)
        result = calc.calculate(
            symbol="EURUSD",
            period_label="PREVIOUS_DAY",
            period_start_utc=...,
            period_end_utc=...,
            candles=candle_tuple,
            tick_size=Decimal("0.00001"),
        )

    The calculator never stores state between calls. Every call is
    independently reproducible from the same inputs and config.
    """

    def __init__(self, config: VolumeProfileConfig) -> None:
        self._config = config
        self._snapshot_id = config_snapshot_id(config)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def calculate(
        self,
        *,
        symbol: str,
        period_label: str,
        period_start_utc: datetime,
        period_end_utc: datetime,
        candles: tuple,
        tick_size: Decimal,
        bin_size_ticks: int,
        source_timeframe: str = "UNKNOWN",
    ) -> VolumeProfileResult:
        """
        Calculate the Volume Profile for one completed period.

        Args:
            symbol: Instrument identifier (for logging and metadata).
            period_label: ``PREVIOUS_DAY`` or ``PREVIOUS_WEEK``.
            period_start_utc: Inclusive start of the completed period.
            period_end_utc: Exclusive end of the completed period.
            candles: Closed source candles already filtered to the period.
                     Forming candles will be silently skipped.
            tick_size: Instrument tick size (e.g. Decimal("0.00001")).
            bin_size_ticks: Bin width in ticks (from config, possibly
                            symbol-overridden by the caller).
            source_timeframe: The timeframe string used to build these
                              candles (for result metadata only).

        Returns:
            VolumeProfileResult with status VALID or UNKNOWN.
        """
        cfg = self._config
        snap_id = self._snapshot_id

        def _unknown(reason: str) -> VolumeProfileResult:
            logger.info(
                '{"event":"volume_profile_unknown","symbol":"%s",'
                '"period":"%s","reason":"%s","snapshot_id":"%s"}',
                symbol, period_label, reason, snap_id,
            )
            return VolumeProfileResult(
                status=VolumeProfileStatus.UNKNOWN,
                symbol=symbol,
                period_label=period_label,
                period_start_utc=period_start_utc,
                period_end_utc=period_end_utc,
                source_timeframe=source_timeframe,
                volume_source=cfg.volume_source,
                volume_quality=VolumeQuality.UNKNOWN.value,
                allocation_method=cfg.allocation_method,
                value_area_percent=cfg.value_area_percent,
                configuration_snapshot_id=snap_id,
                levels=None,
                unknown_reason=reason,
            )

        # --- 1. Filter to closed candles only ---
        closed = [c for c in candles if c.is_closed]
        if len(closed) < cfg.minimum_source_bars:
            return _unknown(
                f"insufficient_source_bars: need {cfg.minimum_source_bars}, "
                f"got {len(closed)}"
            )

        # --- 2. Select volume field and classify quality ---
        selected_volume, volume_source, quality = self._select_volume_source(closed, cfg)

        valid_vol_bars = sum(1 for v in selected_volume if v > 0)
        if valid_vol_bars < cfg.minimum_valid_volume_bars:
            return _unknown(
                f"insufficient_valid_volume_bars: need {cfg.minimum_valid_volume_bars}, "
                f"got {valid_vol_bars}"
            )

        # --- 3. Build price rows ---
        if tick_size <= 0:
            return _unknown(f"non_positive_tick_size: {tick_size}")
        bin_width = Decimal(str(bin_size_ticks)) * tick_size
        if bin_width <= 0:
            return _unknown(f"non_positive_bin_width: {bin_width}")

        rows = self._build_rows(closed, selected_volume, bin_width)
        if not rows:
            return _unknown("no_rows_built: all candles had zero range")

        total_volume = sum(r.volume for r in rows)
        if total_volume <= 0:
            return _unknown("zero_total_volume")

        # --- 4. POC ---
        profile_midpoint = (rows[0].price + rows[-1].price + bin_width) / 2
        poc_row = self._find_poc(rows, profile_midpoint)

        # --- 5. Value Area ---
        vah_row, val_row = self._expand_value_area(
            rows, poc_row, total_volume, cfg.value_area_percent
        )

        # --- 6. Assemble result ---
        levels = VolumeProfileLevels(
            poc=VolumeProfileLevel(
                level_type=f"{period_label}_POC",
                price=poc_row.price,
                row_volume=poc_row.volume,
            ),
            vah=VolumeProfileLevel(
                level_type=f"{period_label}_VAH",
                price=vah_row.price,
                row_volume=vah_row.volume,
            ),
            val=VolumeProfileLevel(
                level_type=f"{period_label}_VAL",
                price=val_row.price,
                row_volume=val_row.volume,
            ),
            total_volume=total_volume,
            bin_width=bin_width,
            source_bars=len(closed),
            valid_volume_bars=valid_vol_bars,
        )
        result = VolumeProfileResult(
            status=VolumeProfileStatus.VALID,
            symbol=symbol,
            period_label=period_label,
            period_start_utc=period_start_utc,
            period_end_utc=period_end_utc,
            source_timeframe=source_timeframe,
            volume_source=volume_source,
            volume_quality=quality.value,
            allocation_method=cfg.allocation_method,
            value_area_percent=cfg.value_area_percent,
            configuration_snapshot_id=snap_id,
            levels=levels,
        )
        logger.info(
            '{"event":"volume_profile_calculated","symbol":"%s","period":"%s",'
            '"poc":"%s","vah":"%s","val":"%s","total_volume":"%s",'
            '"source_bars":%d,"valid_volume_bars":%d,"snapshot_id":"%s"}',
            symbol, period_label,
            str(poc_row.price), str(vah_row.price), str(val_row.price),
            str(total_volume), len(closed), valid_vol_bars, snap_id,
        )
        return result

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------


    @staticmethod
    def _select_volume_source(
        candles: list,
        cfg: VolumeProfileConfig,
    ) -> Tuple[List[Decimal], str, VolumeQuality]:
        """
        Choose tick_volume or real_volume per ADR-017 policy.

        Returns (volume_list, volume_source_str, quality).
        """
        # Try real_volume first when fallback is enabled
        if cfg.real_volume_fallback:
            real_vals = [
                Decimal(str(c.real_volume)) if c.real_volume else Decimal("0")
                for c in candles
            ]
            real_count = sum(1 for v in real_vals if v > 0)
            if real_count > 0:
                return real_vals, VolumeSource.REAL_VOLUME.value, VolumeQuality.REAL

        # Fall back to tick_volume
        tick_vals = [
            Decimal(str(c.tick_volume)) if c.tick_volume else Decimal("0")
            for c in candles
        ]
        return tick_vals, VolumeSource.MT5_TICK_VOLUME.value, VolumeQuality.BROKER_TICK_PROXY

    @staticmethod
    def _build_rows(
        candles: list,
        volumes: List[Decimal],
        bin_width: Decimal,
    ) -> List[_Row]:
        """
        Build price rows using RANGE_UNIFORM allocation.

        For each candle i touching N_i rows:
            allocated_volume(i, row) = V_i / N_i

        Rows are keyed by their lower-bound price, snapped to the bin grid.
        The grid origin is the lowest low across all candles.
        """
        if not candles:
            return []

        # Grid origin: lowest low across all candles
        low_prices = [c.low for c in candles]
        origin = min(low_prices)

        # Accumulate row volumes in a dict keyed by row index
        row_volumes: Dict[int, Decimal] = {}

        for candle, vol in zip(candles, volumes):
            if vol <= 0:
                continue  # zero-volume candle contributes nothing

            c_low = candle.low
            c_high = candle.high

            # Row indices this candle touches
            idx_low = int((c_low - origin) / bin_width)
            idx_high = int((c_high - origin) / bin_width)

            # Number of distinct rows touched
            n_rows = idx_high - idx_low + 1
            per_row = vol / Decimal(str(n_rows))

            for idx in range(idx_low, idx_high + 1):
                row_volumes[idx] = row_volumes.get(idx, Decimal("0")) + per_row

        if not row_volumes:
            return []

        # Convert to sorted list of _Row objects (ascending price)
        sorted_indices = sorted(row_volumes.keys())
        rows = [
            _Row(
                price=origin + Decimal(str(idx)) * bin_width,
                volume=row_volumes[idx],
            )
            for idx in sorted_indices
        ]
        return rows

    @staticmethod
    def _find_poc(rows: List[_Row], profile_midpoint: Decimal) -> _Row:
        """
        Return the row with the highest volume.

        Tie-breaking (Doc 40 §5.3):
          1. Row closest to profile midpoint.
          2. If still tied: lower price row.
        """
        max_vol = max(r.volume for r in rows)
        candidates = [r for r in rows if r.volume == max_vol]
        if len(candidates) == 1:
            return candidates[0]

        # Tie-break 1: closest to midpoint
        min_dist = min(abs(r.price - profile_midpoint) for r in candidates)
        candidates = [r for r in candidates if abs(r.price - profile_midpoint) == min_dist]
        if len(candidates) == 1:
            return candidates[0]

        # Tie-break 2: lower price
        return min(candidates, key=lambda r: r.price)

    @staticmethod
    def _expand_value_area(
        rows: List[_Row],
        poc_row: _Row,
        total_volume: Decimal,
        value_area_percent: float,
    ) -> Tuple[_Row, _Row]:
        """
        Expand value area from POC until target_volume is accumulated.

        Tie-breaking (Doc 40 §5.4):
          1. Side closer to POC (fewer rows away).
          2. If equally distant: upper row.

        Returns (vah_row, val_row).
        """
        target = total_volume * Decimal(str(value_area_percent))

        poc_idx = rows.index(poc_row)
        accumulated = poc_row.volume
        upper_idx = poc_idx
        lower_idx = poc_idx

        while accumulated < target:
            can_go_up = upper_idx + 1 < len(rows)
            can_go_down = lower_idx - 1 >= 0

            if not can_go_up and not can_go_down:
                break

            if not can_go_up:
                lower_idx -= 1
                accumulated += rows[lower_idx].volume
            elif not can_go_down:
                upper_idx += 1
                accumulated += rows[upper_idx].volume
            else:
                next_up_vol = rows[upper_idx + 1].volume
                next_down_vol = rows[lower_idx - 1].volume

                if next_up_vol > next_down_vol:
                    upper_idx += 1
                    accumulated += rows[upper_idx].volume
                elif next_down_vol > next_up_vol:
                    lower_idx -= 1
                    accumulated += rows[lower_idx].volume
                else:
                    # Tie-break: side closer to POC
                    dist_up = (upper_idx + 1) - poc_idx
                    dist_down = poc_idx - (lower_idx - 1)
                    if dist_up < dist_down:
                        upper_idx += 1
                        accumulated += rows[upper_idx].volume
                    elif dist_down < dist_up:
                        lower_idx -= 1
                        accumulated += rows[lower_idx].volume
                    else:
                        # Still tied: choose upper (Doc 40 §5.4)
                        upper_idx += 1
                        accumulated += rows[upper_idx].volume

        return rows[upper_idx], rows[lower_idx]
