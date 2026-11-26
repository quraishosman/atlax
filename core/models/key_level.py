"""Typed key-level evidence produced by the Market Data layer."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Optional


_LEVEL_TYPES = frozenset(
    {
        "PREVIOUS_DAY_HIGH",
        "PREVIOUS_DAY_LOW",
        "PREVIOUS_WEEK_HIGH",
        "PREVIOUS_WEEK_LOW",
        "H4_SWING_HIGH",
        "H4_SWING_LOW",
        "D1_SWING_HIGH",
        "D1_SWING_LOW",
        # Volume Profile levels (Doc 40 §3, ADR-017)
        "PREVIOUS_DAY_POC",
        "PREVIOUS_DAY_VAH",
        "PREVIOUS_DAY_VAL",
        "PREVIOUS_WEEK_POC",
        "PREVIOUS_WEEK_VAH",
        "PREVIOUS_WEEK_VAL",
    }
)


@dataclass(frozen=True)
class KeyLevel:
    """One deterministic price level available for parent matching."""

    level_type: str
    price: Decimal
    source_timeframe: str
    reference_open_time: datetime

    def __post_init__(self) -> None:
        if self.level_type not in _LEVEL_TYPES:
            raise ValueError(f"Unsupported key-level type: {self.level_type!r}")
        if self.price <= Decimal("0"):
            raise ValueError("Key-level price must be positive.")
        if self.reference_open_time.tzinfo is None:
            raise ValueError("Key-level reference time must be timezone-aware.")


@dataclass(frozen=True)
class KeyLevelMatch:
    """A level that matched the evaluated parent candle."""

    level_type: str
    price: Decimal
    source_timeframe: str
    distance: Decimal
    distance_atr: Optional[Decimal]

    def to_dict(self) -> dict[str, Optional[str]]:
        return {
            "level_type": self.level_type,
            "price": str(self.price),
            "source_timeframe": self.source_timeframe,
            "distance": str(self.distance),
            "distance_atr": str(self.distance_atr) if self.distance_atr is not None else None,
        }


@dataclass(frozen=True)
class KeyLevelEvaluation:
    """Complete, immutable result of evaluating one closed parent candle."""

    status: str
    parent_at_htf_zone: Optional[bool]
    symbol: str
    parent_timeframe: str
    parent_open_time: datetime
    tolerance: Optional[Decimal]
    tolerance_atr_multiplier: Optional[Decimal]
    minimum_tolerance_pips: Optional[Decimal]
    matched_levels: tuple[KeyLevelMatch, ...]
    missing_sources: tuple[str, ...]
    configuration_snapshot_id: str
    explanation: str

    def __post_init__(self) -> None:
        if self.status not in {"VALID", "UNKNOWN"}:
            raise ValueError(f"Unsupported key-level evaluation status: {self.status!r}")
        if self.status == "UNKNOWN" and self.parent_at_htf_zone is not None:
            raise ValueError("UNKNOWN key-level evaluations must have no boolean result.")
        if self.status == "VALID" and self.parent_at_htf_zone is None:
            raise ValueError("VALID key-level evaluations require a boolean result.")
        if self.parent_open_time.tzinfo is None:
            raise ValueError("Parent open time must be timezone-aware.")

    @property
    def matched_level_count(self) -> int:
        return len(self.matched_levels)

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "parent_at_htf_zone": self.parent_at_htf_zone,
            "symbol": self.symbol,
            "parent_timeframe": self.parent_timeframe,
            "parent_open_time": self.parent_open_time.isoformat(),
            "tolerance": str(self.tolerance) if self.tolerance is not None else None,
            "tolerance_atr_multiplier": (
                str(self.tolerance_atr_multiplier)
                if self.tolerance_atr_multiplier is not None
                else None
            ),
            "minimum_tolerance_pips": (
                str(self.minimum_tolerance_pips)
                if self.minimum_tolerance_pips is not None
                else None
            ),
            "matched_level_count": self.matched_level_count,
            "matched_levels": [level.to_dict() for level in self.matched_levels],
            "missing_sources": list(self.missing_sources),
            "configuration_snapshot_id": self.configuration_snapshot_id,
            "explanation": self.explanation,
        }
