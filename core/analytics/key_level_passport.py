"""Serialization helpers for key-level evidence stored with a Signal Passport."""

from __future__ import annotations

import json
from typing import Any, Optional


KEY_LEVEL_PASSPORT_FIELDS = (
    "key_level_status",
    "key_level_zone",
    "key_level_match_count",
    "key_level_configuration_snapshot_id",
    "key_level_evidence",
    "key_level_explanation",
)


def key_level_passport_fields(evaluation: Optional[dict[str, Any]]) -> dict[str, Any]:
    """Convert evaluator output into stable journal columns.

    Raw matched prices remain internal evidence. Public Passport renderers use
    the status/count summary and never expose this JSON blob directly.
    """
    if not isinstance(evaluation, dict):
        return {
            "key_level_status": "NOT_APPLICABLE",
            "key_level_zone": None,
            "key_level_match_count": None,
            "key_level_configuration_snapshot_id": "",
            "key_level_evidence": "{}",
            "key_level_explanation": "Key-level evaluation was not available for this signal.",
        }

    status = str(evaluation.get("status", "UNKNOWN")).upper()
    if status not in {"VALID", "UNKNOWN"}:
        status = "UNKNOWN"
    zone = evaluation.get("parent_at_htf_zone")
    zone_value = True if zone is True else False if zone is False else None
    try:
        match_count = max(int(evaluation.get("matched_level_count", 0)), 0)
    except (TypeError, ValueError):
        match_count = 0
    evidence = json.dumps(evaluation, sort_keys=True, separators=(",", ":"))
    return {
        "key_level_status": status,
        "key_level_zone": zone_value,
        "key_level_match_count": match_count,
        "key_level_configuration_snapshot_id": str(
            evaluation.get("configuration_snapshot_id", "")
        ),
        "key_level_evidence": evidence,
        "key_level_explanation": str(evaluation.get("explanation", "")),
    }
