"""
ATLAX Volume Profile Result Models.

Defines the immutable output of the Volume Profile engine: a
VolumeProfileLevel, VolumeProfileLevels, and VolumeProfileResult.

Every result carries full reproducibility metadata so that Signal
Passport, replay, analytics, and the Key-Level Evaluator all work
from the same snapshot that was used at evaluation time.

Authority:
    docs/40_VOLUME_PROFILE_CONFLUENCE_PLAN.md §4–§6
    docs/decisions/ADR-017.md
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Optional


class VolumeProfileStatus(str, Enum):
    """Outcome of a Volume Profile evaluation pass."""

    VALID = "VALID"
    """Profile was calculated successfully with sufficient data."""

    UNKNOWN = "UNKNOWN"
    """Required source data, volume, or configuration was unavailable.

    An UNKNOWN result must never suppress a standard CRT alert.
    """


@dataclass(frozen=True)
class VolumeProfileLevel:
    """
    A single price level produced by the Volume Profile engine.

    Attributes:
        level_type: Human-readable label e.g. ``PREVIOUS_DAY_POC``.
        price: The representative price for this row (row's lower bound).
        row_volume: Total volume allocated to this row across all
            source candles.
    """

    level_type: str
    price: Decimal
    row_volume: Decimal


@dataclass(frozen=True)
class VolumeProfileLevels:
    """
    The three computed levels for one completed profile period.

    Attributes:
        poc: Point of Control — row with highest volume.
        vah: Value Area High — highest row inside the value area.
        val: Value Area Low — lowest row inside the value area.
        total_volume: Sum of all row volumes in the profile.
        bin_width: Actual bin width used (ticks * tick_size).
        source_bars: Number of source candles evaluated.
        valid_volume_bars: Number of those candles with usable volume.
    """

    poc: VolumeProfileLevel
    vah: VolumeProfileLevel
    val: VolumeProfileLevel
    total_volume: Decimal
    bin_width: Decimal
    source_bars: int
    valid_volume_bars: int


@dataclass(frozen=True)
class VolumeProfileResult:
    """
    The complete output of one Volume Profile engine evaluation.

    Carries both the calculated levels (when VALID) and all
    reproducibility metadata needed to recreate the result from
    stored data.

    Attributes:
        status: VALID or UNKNOWN.
        symbol: Instrument identifier.
        period_label: e.g. ``PREVIOUS_DAY`` or ``PREVIOUS_WEEK``.
        period_start_utc: Inclusive start of the completed period (UTC).
        period_end_utc: Exclusive end of the completed period (UTC).
        source_timeframe: Source candle timeframe string (e.g. ``M15``).
        volume_source: The VolumeSource enum value used (as string).
        volume_quality: The VolumeQuality enum value (as string).
        allocation_method: e.g. ``RANGE_UNIFORM``.
        value_area_percent: The approved fraction e.g. ``0.70``.
        configuration_snapshot_id: Hash of the VolumeProfileConfig used,
            linking this result to its exact configuration version.
        levels: The computed POC / VAH / VAL, or None when UNKNOWN.
        unknown_reason: Human-readable explanation when status=UNKNOWN.
    """

    status: VolumeProfileStatus
    symbol: str
    period_label: str
    period_start_utc: datetime
    period_end_utc: datetime
    source_timeframe: str
    volume_source: str
    volume_quality: str
    allocation_method: str
    value_area_percent: float
    configuration_snapshot_id: str
    levels: Optional[VolumeProfileLevels] = None
    unknown_reason: Optional[str] = None

    @property
    def is_valid(self) -> bool:
        """True when the profile was successfully calculated."""
        return self.status == VolumeProfileStatus.VALID

    @property
    def poc(self) -> Optional[VolumeProfileLevel]:
        """Convenience: POC level, or None when UNKNOWN."""
        return self.levels.poc if self.levels else None

    @property
    def vah(self) -> Optional[VolumeProfileLevel]:
        """Convenience: VAH level, or None when UNKNOWN."""
        return self.levels.vah if self.levels else None

    @property
    def val(self) -> Optional[VolumeProfileLevel]:
        """Convenience: VAL level, or None when UNKNOWN."""
        return self.levels.val if self.levels else None

    def as_log_dict(self) -> dict:
        """Return a structured dict suitable for JSON logging."""
        d: dict = {
            "status": self.status.value,
            "symbol": self.symbol,
            "period_label": self.period_label,
            "period_start_utc": self.period_start_utc.isoformat(),
            "period_end_utc": self.period_end_utc.isoformat(),
            "source_timeframe": self.source_timeframe,
            "volume_source": self.volume_source,
            "volume_quality": self.volume_quality,
            "allocation_method": self.allocation_method,
            "value_area_percent": self.value_area_percent,
            "configuration_snapshot_id": self.configuration_snapshot_id,
        }
        if self.levels:
            d["poc"] = str(self.levels.poc.price)
            d["vah"] = str(self.levels.vah.price)
            d["val"] = str(self.levels.val.price)
            d["total_volume"] = str(self.levels.total_volume)
            d["source_bars"] = self.levels.source_bars
            d["valid_volume_bars"] = self.levels.valid_volume_bars
        if self.unknown_reason:
            d["unknown_reason"] = self.unknown_reason
        return d
