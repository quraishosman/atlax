"""
Approval Workflow Models.
"""
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum

from core.models.execution import ExecutionRequest

class ApprovalStatus(str, Enum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    EXECUTING = "EXECUTING"
    EXECUTED = "EXECUTED"
    EXECUTION_PENDING_RECONCILIATION = "EXECUTION_PENDING_RECONCILIATION"
    EXECUTION_AMBIGUOUS = "EXECUTION_AMBIGUOUS"
    EXECUTION_FAILED = "EXECUTION_FAILED"
    QUARANTINED = "QUARANTINED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"

@dataclass
class PendingTrade:
    """Wraps an ExecutionRequest waiting for human approval."""
    request: ExecutionRequest
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    expires_at: datetime = field(init=False)
    status: ApprovalStatus = ApprovalStatus.PENDING
    execution_ready: bool = True
    
    def set_expiration(self, seconds: int):
        from datetime import timedelta
        self.expires_at = self.created_at + timedelta(seconds=seconds)
