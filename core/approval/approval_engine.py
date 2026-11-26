"""
Approval Workflow Engine (SQLite Backed for IPC).
"""
from typing import Dict, List, Optional
from datetime import datetime, timezone
import sqlite3
import json
import logging
import re
from decimal import Decimal
from dataclasses import asdict, is_dataclass
from pathlib import Path

from core.models.execution import ExecutionRequest
from core.models.prop_firm import LossBudgetDecision
from core.models.trade_candidate import TradeCandidate, EntryModel, InvalidationModel, TargetModel
from core.models.risk import RiskEvaluation, RiskCalculation
from core.models.approval import ApprovalStatus, PendingTrade
from core.settings.approval_config import ApprovalConfig

logger = logging.getLogger(__name__)

class SQLiteApprovalQueue:
    """Manages the lifecycle of ExecutionRequests pending human approval using SQLite for IPC."""
    
    def __init__(self, db_path: str, config: ApprovalConfig):
        self.db_path = db_path
        self.config = config
        self._init_db()
        
    def _init_db(self):
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS pending_approvals (
                    candidate_id TEXT PRIMARY KEY,
                    symbol TEXT,
                    timeframe TEXT,
                    profile TEXT,
                    direction TEXT,
                    lot_size REAL,
                    entry_price REAL,
                    sl_price REAL,
                    tp_price REAL,
                    status TEXT,
                    created_at TEXT,
                    expires_at TEXT,
                    request_json TEXT
                )
            ''')
            existing_columns = {
                row[1]
                for row in cursor.execute("PRAGMA table_info(pending_approvals)")
            }
            if "quarantine_reason" not in existing_columns:
                cursor.execute(
                    "ALTER TABLE pending_approvals ADD COLUMN quarantine_reason TEXT"
                )
            if "quarantined_at" not in existing_columns:
                cursor.execute(
                    "ALTER TABLE pending_approvals ADD COLUMN quarantined_at TEXT"
                )
            conn.commit()

    def submit_for_approval(
        self,
        request: ExecutionRequest,
        *,
        execution_ready: bool = True,
    ) -> PendingTrade:
        pending = PendingTrade(request=request)
        pending.set_expiration(self.config.auto_reject_after_seconds)
        pending.execution_ready = execution_ready
        
        if not self.config.require_manual:
            pending.status = ApprovalStatus.APPROVED
        
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            # serialize request into json string for re-hydration
            
            # Helper to convert dataclasses to dict avoiding decimal errors
            def to_dict(obj):
                if obj is None: return None
                custom_serializer = getattr(obj, "to_dict", None)
                if callable(custom_serializer):
                    return json.loads(json.dumps(custom_serializer(), default=str))
                if is_dataclass(obj):
                    return json.loads(json.dumps(asdict(obj), default=str))
                if isinstance(obj, dict):
                    return json.loads(json.dumps(obj, default=str))
                return json.loads(json.dumps(obj.__dict__, default=str))

            request_dict = {
                "schema_version": 1,
                "candidate": to_dict(request.candidate),
                "risk_evaluation": to_dict(request.risk_evaluation),
                "magic_number": request.magic_number,
                "requested_at": request.requested_at.isoformat(),
                "execution_ready": execution_ready,
            }
            cursor.execute('''
                INSERT OR REPLACE INTO pending_approvals 
                (candidate_id, symbol, timeframe, profile, direction, lot_size, entry_price, sl_price, tp_price, status, created_at, expires_at, request_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                request.candidate.candidate_id,
                request.candidate.symbol,
                request.candidate.timeframe,
                request.candidate.profile,
                request.candidate.direction,
                float(request.risk_evaluation.lot_size) if request.risk_evaluation.lot_size else 0.0,
                float(request.candidate.entry_model.reference_price),
                float(request.candidate.invalidation_model.invalidation_level),
                float(request.candidate.target_model.midpoint_target) if request.candidate.target_model else 0.0,
                pending.status.value,
                pending.created_at.isoformat(),
                pending.expires_at.isoformat(),
                json.dumps(request_dict)
            ))
            conn.commit()
            
        return pending

    def _hydrate(self, row) -> PendingTrade:
        req_data = json.loads(row[12])

        schema_version = req_data.get("schema_version", 0)
        if schema_version not in (0, 1):
            raise ValueError(f"unsupported approval request schema_version={schema_version}")

        c_data = req_data["candidate"]
        entry_data = self._decode_model(c_data["entry_model"], "EntryModel")
        inv_data = self._decode_model(c_data["invalidation_model"], "InvalidationModel")
        target_data = self._decode_model(c_data["target_model"], "TargetModel") if c_data["target_model"] else None
        entry = EntryModel(
            entry_zone_high=Decimal(str(entry_data["entry_zone_high"])),
            entry_zone_low=Decimal(str(entry_data["entry_zone_low"])),
            reference_price=Decimal(str(entry_data["reference_price"])),
            description=entry_data["description"],
        )
        inv = InvalidationModel(
            invalidation_level=Decimal(str(inv_data["invalidation_level"])),
            description=inv_data["description"],
        )
        tgt = (
            TargetModel(
                midpoint_target=Decimal(str(target_data["midpoint_target"])),
                opposite_extreme_target=Decimal(str(target_data["opposite_extreme_target"])),
                description=target_data["description"],
            )
            if target_data
            else None
        )
        
        candidate = TradeCandidate(
            candidate_id=c_data["candidate_id"],
            strategy_name=c_data["strategy_name"],
            source_detector_event_ids=tuple(c_data["source_detector_event_ids"]),
            symbol=c_data["symbol"],
            timeframe=c_data["timeframe"],
            profile=c_data["profile"],
            direction=c_data["direction"],
            entry_model=entry,
            invalidation_model=inv,
            target_model=tgt,
            confidence_inputs=c_data["confidence_inputs"],
            explanation=c_data["explanation"],
            created_at=datetime.fromisoformat(c_data["created_at"])
        )
        
        r_data = req_data["risk_evaluation"]
        calculation_data = r_data.get("calculation")
        if calculation_data:
            calculation_data = self._decode_model(calculation_data, "RiskCalculation")
            calculation_data = {
                key: Decimal(str(value)) for key, value in calculation_data.items()
            }
        calc = RiskCalculation(**calculation_data) if calculation_data else None
        risk = RiskEvaluation(
            candidate_id=r_data["candidate_id"],
            approved=r_data["approved"],
            lot_size=Decimal(str(r_data["lot_size"])) if r_data["lot_size"] else None,
            calculation=calc,
            rejection_code=r_data.get("rejection_code"),
            reason=r_data["reason"],
            evaluated_at=datetime.fromisoformat(r_data["evaluated_at"]),
            loss_budget=(
                LossBudgetDecision.from_dict(r_data["loss_budget"])
                if isinstance(r_data.get("loss_budget"), dict)
                else None
            ),
        )
        
        request = ExecutionRequest(
            candidate=candidate,
            risk_evaluation=risk,
            magic_number=req_data["magic_number"],
            requested_at=datetime.fromisoformat(req_data["requested_at"])
        )
        
        pending = PendingTrade(request=request)
        pending.status = ApprovalStatus(row[9])
        pending.created_at = datetime.fromisoformat(row[10])
        pending.expires_at = datetime.fromisoformat(row[11])
        pending.execution_ready = bool(req_data.get("execution_ready", True))
        return pending

    @staticmethod
    def _decode_model(value, model_name: str) -> dict:
        """Decode current dict payloads and the legacy dataclass repr format.

        Legacy rows are accepted only when every required field is present and
        represented by the expected Decimal/string syntax. No summary-column
        values are used as a fallback for missing execution data.
        """
        if isinstance(value, dict):
            return value
        if not isinstance(value, str):
            raise TypeError(f"{model_name} must be an object, got {type(value).__name__}")
        prefix = f"{model_name}("
        if not value.startswith(prefix) or not value.endswith(")"):
            raise ValueError(f"invalid legacy {model_name} representation")

        fields = dict(re.findall(r"(\w+)=Decimal\('([^']+)'\)", value))
        if model_name == "EntryModel":
            required = {"entry_zone_high", "entry_zone_low", "reference_price"}
        elif model_name == "InvalidationModel":
            required = {"invalidation_level"}
        elif model_name == "TargetModel":
            required = {"midpoint_target", "opposite_extreme_target"}
        elif model_name == "RiskCalculation":
            required = {
                "sl_distance_price", "sl_distance_pips", "pip_value",
                "risk_amount", "raw_lot_size", "lot_size", "min_lot",
                "max_lot", "lot_step",
            }
        else:
            raise ValueError(f"unsupported legacy model {model_name}")
        if not required.issubset(fields):
            missing = sorted(required.difference(fields))
            raise ValueError(f"legacy {model_name} missing fields: {missing}")

        if model_name in {"EntryModel", "InvalidationModel", "TargetModel"}:
            description = re.search(r", description='(.*)'\)$", value)
            if description is None:
                raise ValueError(f"legacy {model_name} missing description")
            fields["description"] = description.group(1)
        return fields

    def _quarantine_row(self, row, reason: Exception) -> None:
        """Preserve and deactivate an unreadable persisted request."""
        candidate_id = row[0]
        previous_status = row[9]
        if previous_status not in {
            ApprovalStatus.PENDING.value,
            ApprovalStatus.APPROVED.value,
        }:
            return
        reason_text = f"{type(reason).__name__}: {str(reason)[:400]}"
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                UPDATE pending_approvals
                SET status = ?, quarantine_reason = ?, quarantined_at = ?
                WHERE candidate_id = ? AND status = ?
                """,
                (
                    ApprovalStatus.QUARANTINED.value,
                    reason_text,
                    datetime.now(timezone.utc).isoformat(),
                    candidate_id,
                    previous_status,
                ),
            )
        logger.error(
            "approval_request_quarantined candidate_id=%s previous_status=%s reason=%s",
            candidate_id,
            previous_status,
            reason_text,
        )

    def _hydrate_or_quarantine(self, row) -> Optional[PendingTrade]:
        """Hydrate one row without aborting the queue on bad persisted data."""
        try:
            return self._hydrate(row)
        except Exception as exc:  # noqa: BLE001 - persisted payload is untrusted
            self._quarantine_row(row, exc)
            return None

    def get_pending(self, candidate_id: str) -> Optional[PendingTrade]:
        self.cleanup_expired()
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM pending_approvals WHERE candidate_id = ? AND status = ?', (candidate_id, ApprovalStatus.PENDING.value))
            row = cursor.fetchone()
            if row:
                return self._hydrate_or_quarantine(row)
        return None
        
    def get_request(self, candidate_id: str) -> Optional[ExecutionRequest]:
        """Fetch a request from the DB regardless of its current status."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM pending_approvals WHERE candidate_id = ?', (candidate_id,))
            row = cursor.fetchone()
            if row:
                pending = self._hydrate_or_quarantine(row)
                if pending is None:
                    return None
                return pending.request
        return None

    def get_status(self, candidate_id: str) -> Optional[ApprovalStatus]:
        """Return the persisted approval status for an execution request."""
        self.cleanup_expired()
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT status FROM pending_approvals WHERE candidate_id = ?",
                (candidate_id,),
            ).fetchone()
        return ApprovalStatus(row[0]) if row else None
        
    def get_all_pending(self, profile: str = None) -> List[PendingTrade]:
        self.cleanup_expired()
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            if profile and profile != "Global View":
                cursor.execute('SELECT * FROM pending_approvals WHERE status = ? AND profile = ? ORDER BY created_at DESC', (ApprovalStatus.PENDING.value, profile))
            else:
                cursor.execute('SELECT * FROM pending_approvals WHERE status = ? ORDER BY created_at DESC', (ApprovalStatus.PENDING.value,))
            rows = cursor.fetchall()
            hydrated = []
            for row in rows:
                pending = self._hydrate_or_quarantine(row)
                if pending is not None:
                    hydrated.append(pending)
            return hydrated

    def get_all_approved_ready(self, profile: str = None) -> List[PendingTrade]:
        """Return approved requests whose entry gate has been satisfied."""
        self.cleanup_expired()
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            if profile and profile != "Global View":
                cursor.execute(
                    "SELECT * FROM pending_approvals WHERE status = ? AND profile = ? ORDER BY created_at ASC",
                    (ApprovalStatus.APPROVED.value, profile),
                )
            else:
                cursor.execute(
                    "SELECT * FROM pending_approvals WHERE status = ? ORDER BY created_at ASC",
                    (ApprovalStatus.APPROVED.value,),
                )
            hydrated = []
            for row in cursor.fetchall():
                pending = self._hydrate_or_quarantine(row)
                if pending is not None and pending.execution_ready:
                    hydrated.append(pending)
            return hydrated

    def mark_execution_ready(self, candidate_id: str) -> bool:
        """Release a delayed request after its market-entry condition fires."""
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT request_json FROM pending_approvals WHERE candidate_id = ? AND status IN (?, ?)",
                (candidate_id, ApprovalStatus.PENDING.value, ApprovalStatus.APPROVED.value),
            ).fetchone()
            if not row:
                return False
            request_data = json.loads(row[0])
            request_data["execution_ready"] = True
            cursor = conn.execute(
                "UPDATE pending_approvals SET request_json = ? WHERE candidate_id = ? AND status IN (?, ?)",
                (
                    json.dumps(request_data),
                    candidate_id,
                    ApprovalStatus.PENDING.value,
                    ApprovalStatus.APPROVED.value,
                ),
            )
            return cursor.rowcount > 0

    def claim_approved(self, candidate_id: str) -> Optional[PendingTrade]:
        """Atomically claim one ready request so only one worker can execute it."""
        self.cleanup_expired()
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM pending_approvals WHERE candidate_id = ? AND status = ?",
                (candidate_id, ApprovalStatus.APPROVED.value),
            ).fetchone()
            if not row:
                return None
            pending = self._hydrate_or_quarantine(row)
            if pending is None:
                return None
            if not pending.execution_ready:
                return None
            cursor = conn.execute(
                "UPDATE pending_approvals SET status = ? WHERE candidate_id = ? AND status = ?",
                (ApprovalStatus.EXECUTING.value, candidate_id, ApprovalStatus.APPROVED.value),
            )
            if cursor.rowcount != 1:
                return None
            pending.status = ApprovalStatus.EXECUTING
            return pending

    def mark_execution_status(self, candidate_id: str, status: ApprovalStatus) -> bool:
        """Persist a terminal execution outcome exactly once."""
        if status not in {
            ApprovalStatus.EXECUTED,
            ApprovalStatus.EXECUTION_PENDING_RECONCILIATION,
            ApprovalStatus.EXECUTION_AMBIGUOUS,
            ApprovalStatus.EXECUTION_FAILED,
        }:
            raise ValueError(f"Invalid terminal execution status: {status}")
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                "UPDATE pending_approvals SET status = ? WHERE candidate_id = ? AND status = ?",
                (status.value, candidate_id, ApprovalStatus.EXECUTING.value),
            )
            return cursor.rowcount == 1

    def approve_trade(self, candidate_id: str) -> Optional[ExecutionRequest]:
        self.cleanup_expired()
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM pending_approvals WHERE candidate_id = ? AND status = ?', (candidate_id, ApprovalStatus.PENDING.value))
            row = cursor.fetchone()
            if not row:
                return None

            pending = self._hydrate_or_quarantine(row)
            if pending is None:
                return None

            cursor.execute(
                'UPDATE pending_approvals SET status = ? WHERE candidate_id = ?',
                (ApprovalStatus.APPROVED.value, candidate_id),
            )
            conn.commit()
            return pending.request

    def reject_trade(self, candidate_id: str) -> bool:
        self.cleanup_expired()
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('UPDATE pending_approvals SET status = ? WHERE candidate_id = ? AND status = ?', (ApprovalStatus.REJECTED.value, candidate_id, ApprovalStatus.PENDING.value))
            if cursor.rowcount > 0:
                conn.commit()
                return True
        return False

    def cleanup_expired(self) -> int:
        now_str = datetime.now(timezone.utc).isoformat()
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                UPDATE pending_approvals
                SET status = ?
                WHERE status IN (?, ?) AND expires_at < ?
                """,
                (
                    ApprovalStatus.EXPIRED.value,
                    ApprovalStatus.PENDING.value,
                    ApprovalStatus.APPROVED.value,
                    now_str,
                ),
            )
            count = cursor.rowcount
            if count > 0:
                conn.commit()
            return count

class ApprovalEngine:
    """Legacy proxy that wraps the new SQLite Queue to maintain interface compatibility."""
    def __init__(self, config: ApprovalConfig, db_path: str = "data/journal.sqlite"):
        self.config = config
        self._queue = SQLiteApprovalQueue(db_path, config)

    def submit_for_approval(
        self,
        request: ExecutionRequest,
        *,
        execution_ready: bool = True,
    ) -> PendingTrade:
        return self._queue.submit_for_approval(request, execution_ready=execution_ready)

    def get_pending(self, candidate_id: str) -> Optional[PendingTrade]:
        return self._queue.get_pending(candidate_id)

    def get_request(self, candidate_id: str) -> Optional[ExecutionRequest]:
        return self._queue.get_request(candidate_id)

    def get_status(self, candidate_id: str) -> Optional[ApprovalStatus]:
        return self._queue.get_status(candidate_id)

    def get_all_pending(self, profile: str = None) -> List[PendingTrade]:
        return self._queue.get_all_pending(profile)

    def get_all_approved_ready(self, profile: str = None) -> List[PendingTrade]:
        return self._queue.get_all_approved_ready(profile)

    def mark_execution_ready(self, candidate_id: str) -> bool:
        return self._queue.mark_execution_ready(candidate_id)

    def claim_approved(self, candidate_id: str) -> Optional[PendingTrade]:
        return self._queue.claim_approved(candidate_id)

    def mark_execution_status(self, candidate_id: str, status: ApprovalStatus) -> bool:
        return self._queue.mark_execution_status(candidate_id, status)

    def approve_trade(self, candidate_id: str) -> Optional[ExecutionRequest]:
        return self._queue.approve_trade(candidate_id)

    def reject_trade(self, candidate_id: str) -> bool:
        return self._queue.reject_trade(candidate_id)

    def cleanup_expired(self) -> int:
        return self._queue.cleanup_expired()
