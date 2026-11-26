"""Read-only preflight for ATLAX Prop-Firm Phase 8.

This command never changes account mode, binds an MT5 account, starts MT5, or
enables live enforcement. It reports the exact blockers that must be cleared
before shadow validation can begin.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.risk.account_mode import load_account_mode_state
from core.settings.account_mode_config import load_account_mode_config
from core.settings.prop_firm_profiles import load_prop_firm_profiles
from core.storage.factory import get_runtime_state


def build_report(config_path: str, profile_path: str | None = None) -> dict:
    """Build a secret-free Phase 8 readiness report without side effects."""

    report: dict = {
        "phase": "PROP_FIRM_PHASE_8_PREFLIGHT",
        "config_path": str(config_path),
        "profile_path": profile_path,
        "profile_count": 0,
        "account_mode_enabled": False,
        "auto_enforce_when_bound": False,
        "selected_mode": None,
        "account_binding_present": False,
        "selected_profile": None,
        "mt5_python_module_available": importlib.util.find_spec("MetaTrader5") is not None,
        "blockers": [],
        "next_actions": [],
    }

    config = load_account_mode_config(config_path)
    report["account_mode_enabled"] = config.enabled
    report["auto_enforce_when_bound"] = config.auto_enforce_when_bound
    if not config.enabled:
        report["blockers"].append("account_mode.enabled is false")
        report["next_actions"].append(
            "Set approved account-mode operational settings before shadow validation"
        )
    if config.max_open_risk_fraction is None:
        report["blockers"].append("account_mode.max_open_risk_fraction is missing")
    if config.new_trade_buffer_fraction is None:
        report["blockers"].append("account_mode.new_trade_buffer_fraction is missing")

    profiles = load_prop_firm_profiles(profile_path)
    report["profile_count"] = len(profiles)
    if not profiles:
        report["blockers"].append("no typed source-backed Prop-Firm profiles are loaded")

    try:
        runtime_state = get_runtime_state()
        state = load_account_mode_state(runtime_state)
    except Exception as exc:  # noqa: BLE001 - report readiness without mutation
        report["blockers"].append(
            f"runtime state could not be read: {type(exc).__name__}"
        )
        report["next_actions"].append(
            "Restore read access to the configured runtime state store before binding an account"
        )
        return report
    report["selected_mode"] = state.mode.value
    report["account_binding_present"] = state.binding is not None
    if state.binding is None:
        report["blockers"].append("no MT5 account binding is persisted")
        report["next_actions"].append(
            "Bind and verify the intended MT5 account from the admin Telegram menu"
        )
    if not state.selected_profile_id or not state.selected_profile_version:
        report["blockers"].append("no Prop-Firm profile selection is persisted")
        if profiles:
            report["next_actions"].append(
                "Select one exact firm, program, phase, and account size for the first rollout"
            )
    else:
        selected = next(
            (
                profile
                for profile in profiles
                if profile.profile_id == state.selected_profile_id
                and profile.version == state.selected_profile_version
            ),
            None,
        )
        if selected is None:
            report["blockers"].append(
                "persisted profile selection does not match the loaded typed catalog"
            )
        else:
            report["selected_profile"] = {
                "profile_id": selected.profile_id,
                "version": selected.version,
                "currency": selected.currency,
            }

    if not report["mt5_python_module_available"]:
        report["blockers"].append("MetaTrader5 Python module is unavailable in this environment")
    if not report["blockers"]:
        if report["selected_mode"] in {"PROP_FIRM_ENFORCED", "AUTO_ENFORCED"} or (
            report["auto_enforce_when_bound"]
            and report["account_binding_present"]
            and report["selected_profile"] is not None
        ):
            report["next_actions"].extend(
                [
                    "Monitor automatic account verification, blocked entries, and resized entries under enforcement",
                    "Review UNKNOWN contexts immediately; they must remain blocked",
                    "Keep the rollback-to-NORMAL action available for operator response",
                ]
            )
        else:
            report["next_actions"].extend(
                [
                    "Collect real normal-versus-Prop-Firm evaluations in Shadow mode",
                    "Review UNKNOWN contexts, blocked entries, resized entries, and rollback evidence",
                    "Keep live enforcement disabled until Shadow evidence is approved",
                ]
            )
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="config/atlax.yaml")
    parser.add_argument("--profile", default=None)
    args = parser.parse_args()
    report = build_report(args.config, args.profile)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if not report["blockers"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
