"""Load source-backed Prop-Firm profiles without inventing missing rules."""

from __future__ import annotations

import json
import hashlib
import os
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

import yaml

from core.models.prop_firm import (
    AccountReferenceBasis,
    LossLimitMode,
    LossLimitSpec,
    PropFirmRuleProfile,
)


def _enum(enum_type, value: Any, field_name: str):
    try:
        return enum_type(str(value))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} is not an approved enum value") from exc


def _limit(raw: Any, field_name: str) -> LossLimitSpec | None:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ValueError(f"{field_name} must be a mapping or null")
    amount = raw.get("amount")
    percentage = raw.get("percentage")
    return LossLimitSpec(
        mode=_enum(LossLimitMode, raw.get("mode"), f"{field_name}.mode"),
        basis=_enum(AccountReferenceBasis, raw.get("basis"), f"{field_name}.basis"),
        amount=Decimal(str(amount)) if amount is not None else None,
        percentage=Decimal(str(percentage)) if percentage is not None else None,
        reset_timezone=(
            str(raw["reset_timezone"]).strip()
            if raw.get("reset_timezone")
            else None
        ),
    )


def _datetime(value: Any, field_name: str) -> datetime:
    parsed = datetime.fromisoformat(str(value))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return parsed.astimezone(timezone.utc)


def _canonical_profile_hash(raw: dict[str, Any]) -> str:
    """Hash the record without its self-referential integrity field."""
    payload = {
        key: value for key, value in raw.items() if key != "content_hash"
    }
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _profile(raw: Any) -> PropFirmRuleProfile:
    if not isinstance(raw, dict):
        raise ValueError("each Prop-Firm profile must be a mapping")
    declared_hash = str(raw.get("content_hash") or "")
    if declared_hash.startswith("sha256:"):
        expected_hash = f"sha256:{_canonical_profile_hash(raw)}"
        if declared_hash != expected_hash:
            raise ValueError(
                f"profile {raw.get('profile_id', '<unknown>')} content_hash does not match its record "
                f"(declared={declared_hash}, expected={expected_hash})"
            )
    return PropFirmRuleProfile(
        profile_id=str(raw["profile_id"]),
        firm_name=str(raw["firm_name"]),
        program_name=str(raw["program_name"]),
        phase=str(raw["phase"]),
        account_type=str(raw["account_type"]),
        currency=str(raw["currency"]).upper(),
        overall_loss=_limit(raw.get("overall_loss"), "overall_loss"),
        daily_loss=_limit(raw.get("daily_loss"), "daily_loss"),
        source_urls=tuple(str(value) for value in raw.get("source_urls", ())),
        source_retrieved_at=_datetime(raw["source_retrieved_at"], "source_retrieved_at"),
        version=str(raw["version"]),
        content_hash=str(raw["content_hash"]),
        nominal_balance=(
            Decimal(str(raw["nominal_balance"]))
            if raw.get("nominal_balance") is not None
            else None
        ),
        effective_at=(
            _datetime(raw["effective_at"], "effective_at")
            if raw.get("effective_at")
            else None
        ),
        max_position_size=(
            Decimal(str(raw["max_position_size"]))
            if raw.get("max_position_size") is not None
            else None
        ),
        minimum_trading_days=(
            int(raw["minimum_trading_days"])
            if raw.get("minimum_trading_days") is not None
            else None
        ),
    )


def load_prop_firm_profiles(path: str | Path | None = None) -> tuple[PropFirmRuleProfile, ...]:
    """Return only explicitly supplied typed profiles; absent file is safe.

    JSON remains supported for deployment compatibility. The repository
    catalog is YAML, so the default resolver checks YAML before JSON. An
    explicitly configured path always wins and is parsed according to its
    extension; unsupported extensions fail closed.
    """

    configured = path or os.environ.get("ATLAX_PROP_FIRM_PROFILE_FILE")
    if configured:
        selected = Path(configured)
    else:
        repository_root = Path(__file__).resolve().parents[2]
        candidates = (
            repository_root / "config/prop_firm_profiles.yaml",
            repository_root / "config/prop_firm_profiles.yml",
            repository_root / "config/prop_firm_profiles.json",
        )
        selected = next((candidate for candidate in candidates if candidate.exists()), candidates[-1])
    if not selected.exists():
        return ()
    with selected.open("r", encoding="utf-8") as handle:
        if selected.suffix.lower() in {".yaml", ".yml"}:
            raw = yaml.safe_load(handle) or {}
        elif selected.suffix.lower() == ".json":
            raw = json.load(handle)
        else:
            raise ValueError("Prop-Firm profile file must use .json, .yaml, or .yml")
    values = raw.get("profiles", []) if isinstance(raw, dict) else raw
    if not isinstance(values, list):
        raise ValueError("Prop-Firm profile file must contain a profiles list")
    return tuple(_profile(value) for value in values)
