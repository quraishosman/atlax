"""
ATLAX Volume Source Enumeration.

Defines the approved vocabulary for identifying the origin and quality of
volume data used by the Volume Profile engine.

Authority:
    docs/40_VOLUME_PROFILE_CONFLUENCE_PLAN.md §4 (Volume Source Contract)
    docs/decisions/ADR-017.md

Volume Source Policy (V1):
    real_volume available and approved  ->  REAL_VOLUME
    broker tick volume approved         ->  MT5_TICK_VOLUME
    neither available or volume invalid ->  UNKNOWN

Broker tick volume is a local MT5 activity proxy and must never be
presented as consolidated global Forex volume.
"""

from __future__ import annotations

from enum import Enum


class VolumeSource(str, Enum):
    """Origin of the volume data used to build a profile."""

    REAL_VOLUME = "REAL_VOLUME"
    """MT5 real_volume field is available and approved for use."""

    MT5_TICK_VOLUME = "MT5_TICK_VOLUME"
    """MT5 tick_volume (broker-local activity proxy) is used as primary source."""

    UNKNOWN = "UNKNOWN"
    """Volume data is unavailable, zero, or fails the minimum-data contract."""


class VolumeQuality(str, Enum):
    """Quality classification of the volume data in a candle or profile."""

    REAL = "REAL"
    """Real consolidated volume; most reliable."""

    BROKER_TICK_PROXY = "BROKER_TICK_PROXY"
    """Broker tick volume proxy; useful but not consolidated global volume."""

    UNKNOWN = "UNKNOWN"
    """Volume data absent, zero, or insufficient."""
