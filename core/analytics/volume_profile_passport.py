"""
Serialization helpers for Volume Profile evidence stored in the Signal Passport.

Follows the same pattern as ``atr_passport.py`` and ``key_level_passport.py``:
- A tuple of column names (``VOLUME_PROFILE_PASSPORT_FIELDS``) for schema reference.
- A pure function ``volume_profile_passport_fields()`` that converts a
  ``VolumeProfileResult`` or its ``as_log_dict()`` output into stable
  journal columns.

Public Passport renderers expose only the status, period, and level prices.
The full ``volume_profile_evidence`` JSON blob is internal and never exposed
directly to subscribers.

Authority:
    docs/21_SIGNAL_PASSPORT.md
    docs/40_VOLUME_PROFILE_CONFLUENCE_PLAN.md §5 (Signal Passport)
    docs/decisions/ADR-017.md
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any, Optional

from core.models.volume_profile_result import VolumeProfileResult, VolumeProfileStatus


VOLUME_PROFILE_PASSPORT_FIELDS = (
    "volume_profile_status",
    "volume_profile_period",
    "volume_profile_source_timeframe",
    "volume_profile_volume_source",
    "volume_profile_volume_quality",
    "volume_profile_poc",
    "volume_profile_vah",
    "volume_profile_val",
    "volume_profile_total_volume",
    "volume_profile_source_bars",
    "volume_profile_configuration_snapshot_id",
    "volume_profile_evidence",
    "volume_profile_unknown_reason",
)


def volume_profile_passport_fields(
    result: Optional[VolumeProfileResult | Sequence[VolumeProfileResult]],
) -> dict[str, Any]:
    """Convert a VolumeProfileResult into stable journal columns.

    POC / VAH / VAL prices are stored as text strings to preserve
    Decimal precision. The full ``as_log_dict()`` snapshot is stored
    as ``volume_profile_evidence`` for admin diagnostics and replay.

    Raw bin data and per-bar volume breakdown remain internal and are
    not written to the passport.

    Args:
        result: The VolumeProfileResult from the engine, or None when the
            feature was not evaluated (e.g. disabled or not yet integrated).

    Returns:
        Dict with one key per VOLUME_PROFILE_PASSPORT_FIELDS entry.
        All keys are always present; values are None when not applicable.
    """
    if result is None:
        return {
            "volume_profile_status": "NOT_APPLICABLE",
            "volume_profile_period": None,
            "volume_profile_source_timeframe": None,
            "volume_profile_volume_source": None,
            "volume_profile_volume_quality": None,
            "volume_profile_poc": None,
            "volume_profile_vah": None,
            "volume_profile_val": None,
            "volume_profile_total_volume": None,
            "volume_profile_source_bars": None,
            "volume_profile_configuration_snapshot_id": "",
            "volume_profile_evidence": "{}",
            "volume_profile_unknown_reason": None,
        }

    if isinstance(result, VolumeProfileResult):
        results = (result,)
    else:
        results = tuple(item for item in result if isinstance(item, VolumeProfileResult))
        if not results:
            return volume_profile_passport_fields(None)

    if len(results) == 1:
        result = results[0]
        evidence = json.dumps(result.as_log_dict(), sort_keys=True, separators=(",", ":"))
        poc = str(result.levels.poc.price) if result.levels else None
        vah = str(result.levels.vah.price) if result.levels else None
        val = str(result.levels.val.price) if result.levels else None
        total_volume = str(result.levels.total_volume) if result.levels else None
        source_bars = result.levels.source_bars if result.levels else None
        return {
            "volume_profile_status": result.status.value,
            "volume_profile_period": result.period_label,
            "volume_profile_source_timeframe": result.source_timeframe,
            "volume_profile_volume_source": result.volume_source,
            "volume_profile_volume_quality": result.volume_quality,
            "volume_profile_poc": poc,
            "volume_profile_vah": vah,
            "volume_profile_val": val,
            "volume_profile_total_volume": total_volume,
            "volume_profile_source_bars": source_bars,
            "volume_profile_configuration_snapshot_id": result.configuration_snapshot_id,
            "volume_profile_evidence": evidence,
            "volume_profile_unknown_reason": result.unknown_reason,
        }

    def join_field(values: list[Optional[str]]) -> Optional[str]:
        present = [value for value in values if value not in (None, "")]
        return " | ".join(present) if present else None

    evidence = json.dumps(
        [item.as_log_dict() for item in results],
        sort_keys=True,
        separators=(",", ":"),
    )
    valid_results = [item for item in results if item.status == VolumeProfileStatus.VALID]
    status = "VALID" if valid_results else VolumeProfileStatus.UNKNOWN.value
    poc = join_field([
        f"{item.period_label}:{item.levels.poc.price}" if item.levels else None
        for item in results
    ])
    vah = join_field([
        f"{item.period_label}:{item.levels.vah.price}" if item.levels else None
        for item in results
    ])
    val = join_field([
        f"{item.period_label}:{item.levels.val.price}" if item.levels else None
        for item in results
    ])
    total_volume = join_field([
        f"{item.period_label}:{item.levels.total_volume}" if item.levels else None
        for item in results
    ])
    source_bars = join_field([
        f"{item.period_label}:{item.levels.source_bars}" if item.levels else None
        for item in results
    ])

    return {
        "volume_profile_status": status,
        "volume_profile_period": join_field([item.period_label for item in results]),
        "volume_profile_source_timeframe": join_field([item.source_timeframe for item in results]),
        "volume_profile_volume_source": join_field([item.volume_source for item in results]),
        "volume_profile_volume_quality": join_field([item.volume_quality for item in results]),
        "volume_profile_poc": poc,
        "volume_profile_vah": vah,
        "volume_profile_val": val,
        "volume_profile_total_volume": total_volume,
        "volume_profile_source_bars": source_bars,
        "volume_profile_configuration_snapshot_id": join_field(
            [item.configuration_snapshot_id for item in results]
        ) or "",
        "volume_profile_evidence": evidence,
        "volume_profile_unknown_reason": join_field(
            [item.unknown_reason for item in results]
        ),
    }
