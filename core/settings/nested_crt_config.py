"""Configuration for containment-aware nested CRT routing."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml


_POLICIES = {"suppress_inside", "allow_aligned_only"}
_CONTAINMENT_MODES = {"parent_range_and_child_closes"}


@dataclass(frozen=True)
class NestedCrtRelationshipConfig:
    """One configurable controller-to-child timeframe relationship."""

    controller_timeframe: str
    child_timeframes: tuple[str, ...]
    same_timeframe_policy: str
    child_policy: str
    containment_mode: str

    def __post_init__(self) -> None:
        if not self.controller_timeframe:
            raise ValueError("nested CRT controller_timeframe must not be empty")
        if not self.child_timeframes:
            raise ValueError(
                f"nested CRT relationship {self.controller_timeframe!r} "
                "must define at least one child timeframe"
            )
        if self.same_timeframe_policy not in _POLICIES:
            raise ValueError(
                f"unsupported same_timeframe_policy={self.same_timeframe_policy!r}"
            )
        if self.child_policy not in _POLICIES:
            raise ValueError(f"unsupported child_policy={self.child_policy!r}")
        if self.containment_mode not in _CONTAINMENT_MODES:
            raise ValueError(f"unsupported containment_mode={self.containment_mode!r}")
        if self.controller_timeframe in self.child_timeframes:
            raise ValueError("controller timeframe cannot also be a child timeframe")


@dataclass(frozen=True)
class NestedCrtRoutingConfig:
    """Validated nested CRT routing settings."""

    enabled: bool
    shadow_mode: bool
    relationships: tuple[NestedCrtRelationshipConfig, ...]

    def __post_init__(self) -> None:
        controllers = [item.controller_timeframe for item in self.relationships]
        if len(controllers) != len(set(controllers)):
            raise ValueError("nested CRT controller timeframes must be unique")


def load_nested_crt_routing_config(config_path: str | Path) -> NestedCrtRoutingConfig:
    """Load and validate the optional top-level nested CRT routing section."""
    path = Path(config_path)
    with path.open("r", encoding="utf-8") as handle:
        raw = yaml.safe_load(handle)

    if not isinstance(raw, dict):
        raise ValueError(f"Configuration file {path} must contain a mapping")
    section = raw.get("nested_crt_routing") or {}
    if not isinstance(section, dict):
        raise ValueError("nested_crt_routing must be a mapping")

    allowed_timeframes = {
        str(item).upper()
        for item in (raw.get("crt", {}).get("allowed_timeframes") or [])
    }
    relationships_raw = section.get("relationships") or []
    if not isinstance(relationships_raw, list):
        raise ValueError("nested_crt_routing.relationships must be a list")

    relationships: list[NestedCrtRelationshipConfig] = []
    for index, item in enumerate(relationships_raw):
        if not isinstance(item, dict):
            raise ValueError(f"nested_crt_routing.relationships[{index}] must be a mapping")
        controller = str(item.get("controller_timeframe", "")).strip().upper()
        children_raw = item.get("child_timeframes") or []
        if not isinstance(children_raw, list):
            raise ValueError(
                f"nested_crt_routing.relationships[{index}].child_timeframes must be a list"
            )
        children = tuple(str(child).strip().upper() for child in children_raw)
        if allowed_timeframes and (
            controller not in allowed_timeframes
            or any(child not in allowed_timeframes for child in children)
        ):
            raise ValueError(
                f"nested CRT relationship {controller!r} references a timeframe "
                "outside crt.allowed_timeframes"
            )
        relationships.append(
            NestedCrtRelationshipConfig(
                controller_timeframe=controller,
                child_timeframes=children,
                same_timeframe_policy=str(
                    item.get("same_timeframe_policy", "suppress_inside")
                ).strip().lower(),
                child_policy=str(
                    item.get("child_policy", "allow_aligned_only")
                ).strip().lower(),
                containment_mode=str(
                    item.get("containment_mode", "parent_range_and_child_closes")
                ).strip().lower(),
            )
        )

    enabled = bool(section.get("enabled", False))
    if enabled and not relationships:
        raise ValueError(
            "nested_crt_routing.enabled=true requires at least one relationship"
        )
    return NestedCrtRoutingConfig(
        enabled=enabled,
        shadow_mode=bool(section.get("shadow_mode", True)),
        relationships=tuple(relationships),
    )
