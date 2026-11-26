"""
ATLAX Volume Profile → Key-Level Bridge.

Converts VolumeProfileResult levels into KeyLevel instances that the
Key-Level Evaluator can match against the parent candle using the
same tolerance formula as all other key levels.

Responsibilities:
    - Convert POC, VAH, VAL from a VALID VolumeProfileResult into
      KeyLevel objects with correct level_type and source_timeframe.
    - Return an empty list when the profile is UNKNOWN or has no levels.
    - Never invent levels, assume prices, or modify detection behavior.

Architecture:
    - This is a pure stateless converter. No I/O, no caching, no config.
    - The Key-Level Evaluator is the only consumer of these levels.
    - Volume Profile levels are treated identically to period/swing levels
      for matching purposes — same tolerance, same distance calculation.

Authority:
    docs/40_VOLUME_PROFILE_CONFLUENCE_PLAN.md §6 (Confluence Matching)
    docs/27_KEY_LEVEL_EVALUATOR_PLAN.md (provider pattern)
    docs/decisions/ADR-011.md (key-level separation of concerns)
    docs/decisions/ADR-017.md (Volume Profile governance)
"""

from __future__ import annotations

import logging
from typing import List

from core.models.key_level import KeyLevel
from core.models.volume_profile_result import VolumeProfileResult, VolumeProfileStatus

logger = logging.getLogger("atlax.market_data.volume_profile_bridge")


def volume_profile_levels(result: VolumeProfileResult) -> List[KeyLevel]:
    """
    Convert a VolumeProfileResult into a list of KeyLevel objects.

    Returns up to 3 KeyLevel objects (POC, VAH, VAL) when the profile
    status is VALID and levels are present. Returns an empty list
    when the profile is UNKNOWN.

    The returned KeyLevel objects carry:
        - level_type:  e.g. ``PREVIOUS_DAY_POC``
        - price:       the Decimal row price from the profile engine
        - source_timeframe: the source candle timeframe (e.g. ``M15``)
        - reference_open_time: the profile period start UTC
          (the earliest bar that contributed to this level)

    Args:
        result: A complete VolumeProfileResult from the Volume Profile engine.

    Returns:
        List of 0 to 3 KeyLevel objects, suitable for inclusion in the
        Key-Level Evaluator's level list.
    """
    if result.status != VolumeProfileStatus.VALID or result.levels is None:
        logger.debug(
            '{"event":"volume_profile_bridge_skip","symbol":"%s",'
            '"period":"%s","reason":"status=%s"}',
            result.symbol, result.period_label, result.status.value,
        )
        return []

    levels: List[KeyLevel] = []
    for level in (result.levels.poc, result.levels.vah, result.levels.val):
        if level.price > 0:
            key_level = KeyLevel(
                level_type=level.level_type,
                price=level.price,
                source_timeframe=result.source_timeframe,
                reference_open_time=result.period_start_utc,
            )
            levels.append(key_level)

    logger.debug(
        '{"event":"volume_profile_bridge_produced","symbol":"%s",'
        '"period":"%s","level_count":%d}',
        result.symbol, result.period_label, len(levels),
    )
    return levels
