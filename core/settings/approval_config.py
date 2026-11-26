"""
Approval Configuration.
"""
import yaml
from dataclasses import dataclass

@dataclass(frozen=True)
class ApprovalConfig:
    require_manual: bool
    auto_reject_after_seconds: int

def load_approval_config(path: str) -> ApprovalConfig:
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
        
    if not data or "approval" not in data:
        raise ValueError("Missing 'approval' configuration section.")
        
    sec = data["approval"]
    require_manual = bool(sec.get("require_manual", True))
    auto_reject_after_seconds = int(sec.get("auto_reject_after_seconds", 300))
    
    if auto_reject_after_seconds <= 0:
        raise ValueError("auto_reject_after_seconds must be > 0")
        
    return ApprovalConfig(
        require_manual=require_manual,
        auto_reject_after_seconds=auto_reject_after_seconds
    )
