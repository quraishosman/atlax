"""
Trade Journal Storage.
Authority: docs/12_ANALYTICS_ENGINE.md
"""
import json
import logging
import sqlite3
import os
from contextlib import contextmanager
from dataclasses import asdict
from decimal import Decimal
from typing import Any, List, Optional
from datetime import datetime, timezone

from core.models.analytics import JournalEntry
from core.models.execution import ExecutionRecord
from core.models.execution_quality import (
    ExecutionQualityEvent,
    ExecutionQualityEventType,
)
from core.confidence.atr_quality import ATRQualityResult
from core.analytics.atr_passport import atr_quality_fields
from core.analytics.key_level_passport import key_level_passport_fields
from core.analytics.volume_profile_passport import volume_profile_passport_fields
from core.models.volume_profile_result import VolumeProfileResult

logger = logging.getLogger("atlax.analytics.journal")



class SQLiteJournal:
    """Deterministic SQLite storage for JournalEntry records."""
    
    def __init__(self, db_path: str):
        self.db_path = db_path
        self._init_db()
        
    def _init_db(self):
        """Creates the tables if they don't exist."""
        dname = os.path.dirname(self.db_path)
        if dname:
            os.makedirs(dname, exist_ok=True)
            
        with self._get_connection() as conn:
            conn.execute('''
                CREATE TABLE IF NOT EXISTS journal_entries (
                    record_id TEXT PRIMARY KEY,
                    candidate_id TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    timeframe TEXT NOT NULL,
                    profile TEXT NOT NULL,
                    strategy_name TEXT NOT NULL,
                    direction TEXT NOT NULL,
                    confidence_score REAL NOT NULL,
                    confidence_breakdown TEXT NOT NULL,
                    lot_size TEXT NOT NULL,
                    entry_price TEXT NOT NULL,
                    sl_price TEXT NOT NULL,
                    tp_price TEXT NOT NULL,
                    exit_price TEXT NOT NULL,
                    pnl TEXT NOT NULL,
                    closed_at TEXT NOT NULL,
                    exit_reason TEXT NOT NULL,
                    tags TEXT NOT NULL,
                    signal_status TEXT NOT NULL DEFAULT 'UNKNOWN',
                    invalidation_reason TEXT NOT NULL DEFAULT '',
                    final_outcome TEXT NOT NULL DEFAULT 'UNKNOWN',
                    created_at TEXT,
                    outcome_at TEXT,
                    atr_method TEXT NOT NULL DEFAULT '',
                    atr_period INTEGER,
                    atr_value TEXT,
                    parent_range TEXT,
                    parent_atr_ratio TEXT,
                    sweep_depth TEXT,
                    sweep_atr_ratio TEXT,
                    atr_grade TEXT,
                    atr_configuration_snapshot_id TEXT NOT NULL DEFAULT '',
                    atr_evaluation_status TEXT NOT NULL DEFAULT 'NOT_APPLICABLE',
                    atr_explanation TEXT NOT NULL DEFAULT '',
                    key_level_status TEXT NOT NULL DEFAULT 'NOT_APPLICABLE',
                    key_level_zone INTEGER,
                    key_level_match_count INTEGER,
                    key_level_configuration_snapshot_id TEXT NOT NULL DEFAULT '',
                    key_level_evidence TEXT NOT NULL DEFAULT '{}',
                    key_level_explanation TEXT NOT NULL DEFAULT '',
                    volume_profile_status TEXT NOT NULL DEFAULT 'NOT_APPLICABLE',
                    volume_profile_period TEXT,
                    volume_profile_source_timeframe TEXT,
                    volume_profile_volume_source TEXT,
                    volume_profile_volume_quality TEXT,
                    volume_profile_poc TEXT,
                    volume_profile_vah TEXT,
                    volume_profile_val TEXT,
                    volume_profile_total_volume TEXT,
                    volume_profile_source_bars INTEGER,
                    volume_profile_configuration_snapshot_id TEXT NOT NULL DEFAULT '',
                    volume_profile_evidence TEXT NOT NULL DEFAULT '{}',
                    volume_profile_unknown_reason TEXT
                )
            ''')
            
            # Indexes for common queries
            conn.execute('CREATE INDEX IF NOT EXISTS idx_journal_profile ON journal_entries(profile)')
            conn.execute('CREATE INDEX IF NOT EXISTS idx_journal_symbol ON journal_entries(symbol)')

            conn.execute('''
                CREATE TABLE IF NOT EXISTS shadow_execution_records (
                    shadow_record_id TEXT PRIMARY KEY,
                    candidate_id TEXT NOT NULL,
                    shadow_profile_id TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    timeframe TEXT NOT NULL,
                    direction TEXT NOT NULL,
                    lot_size TEXT,
                    entry_price TEXT,
                    sl_price TEXT,
                    tp_price TEXT,
                    mfe_r TEXT,
                    mae_r TEXT,
                    pnl TEXT,
                    status TEXT NOT NULL,
                    attempted_at TEXT NOT NULL,
                    completed_at TEXT
                )
            ''')
            conn.execute('CREATE INDEX IF NOT EXISTS idx_shadow_exec_candidate ON shadow_execution_records(candidate_id)')

            # Execution attempts are kept separately from signal outcomes so
            # broker reconciliation can be joined to the Signal Passport by
            # candidate_id without changing the subscriber-facing journal row.
            conn.execute('''
                CREATE TABLE IF NOT EXISTS execution_records (
                    execution_record_id TEXT PRIMARY KEY,
                    candidate_id TEXT NOT NULL,
                    profile TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    timeframe TEXT NOT NULL,
                    direction TEXT NOT NULL,
                    lot_size TEXT,
                    risk_evaluation_id TEXT NOT NULL,
                    mt5_request_json TEXT,
                    preflight_result TEXT,
                    order_send_retcode INTEGER,
                    mt5_order_id INTEGER,
                    mt5_deal_id INTEGER,
                    broker_comment TEXT,
                    execution_status TEXT NOT NULL,
                    error_reason TEXT,
                    recovery_suggestion TEXT,
                    attempted_at TEXT NOT NULL,
                    completed_at TEXT NOT NULL,
                    mt5_position_id INTEGER,
                    realized_status TEXT NOT NULL DEFAULT 'PENDING',
                    realized_gross_pnl TEXT,
                    realized_costs TEXT,
                    realized_net_pnl TEXT,
                    realized_deals_count INTEGER NOT NULL DEFAULT 0,
                    realized_reason TEXT NOT NULL DEFAULT '',
                    realized_at TEXT
                )
            ''')
            conn.execute(
                'CREATE INDEX IF NOT EXISTS idx_execution_candidate '
                'ON execution_records(candidate_id)'
            )
            conn.execute(
                'CREATE INDEX IF NOT EXISTS idx_execution_status '
                'ON execution_records(execution_status)'
            )
            conn.execute('''
                CREATE TABLE IF NOT EXISTS execution_quality_events (
                    event_id TEXT PRIMARY KEY,
                    event_type TEXT NOT NULL,
                    execution_record_id TEXT NOT NULL DEFAULT '',
                    candidate_id TEXT NOT NULL DEFAULT '',
                    profile TEXT NOT NULL DEFAULT '',
                    symbol TEXT NOT NULL DEFAULT '',
                    timeframe TEXT NOT NULL DEFAULT '',
                    direction TEXT NOT NULL DEFAULT '',
                    position_id INTEGER,
                    requested_price TEXT,
                    actual_price TEXT,
                    requested_volume TEXT,
                    actual_volume TEXT,
                    spread TEXT,
                    slippage TEXT,
                    stop_loss TEXT,
                    take_profit TEXT,
                    broker_retcode INTEGER,
                    broker_comment TEXT,
                    action TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'UNKNOWN',
                    latency_ms INTEGER,
                    gross_pnl TEXT,
                    costs TEXT,
                    net_pnl TEXT,
                    reason TEXT NOT NULL DEFAULT '',
                    occurred_at TEXT NOT NULL,
                    metadata_json TEXT NOT NULL DEFAULT '{}'
                )
            ''')
            conn.execute(
                'CREATE INDEX IF NOT EXISTS idx_quality_execution '
                'ON execution_quality_events(execution_record_id, occurred_at)'
            )
            conn.execute(
                'CREATE INDEX IF NOT EXISTS idx_quality_type '
                'ON execution_quality_events(event_type, occurred_at)'
            )
            execution_columns = {
                "mt5_position_id": "INTEGER",
                "realized_status": "TEXT NOT NULL DEFAULT 'PENDING'",
                "realized_gross_pnl": "TEXT",
                "realized_costs": "TEXT",
                "realized_net_pnl": "TEXT",
                "realized_deals_count": "INTEGER NOT NULL DEFAULT 0",
                "realized_reason": "TEXT NOT NULL DEFAULT ''",
                "realized_at": "TEXT",
            }
            existing_execution_cols = {
                row[1]
                for row in conn.execute("PRAGMA table_info(execution_records)")
            }
            for column, definition in execution_columns.items():
                if column not in existing_execution_cols:
                    conn.execute(
                        f"ALTER TABLE execution_records ADD COLUMN {column} {definition}"
                    )
                    logger.info(
                        '{"event":"journal_migration","table":"execution_records",'
                        '"added_column":"%s"}',
                        column,
                    )

            # Migration: add trader_notes column if it doesn't exist yet
            existing_cols = {
                row[1]
                for row in conn.execute("PRAGMA table_info(journal_entries)")
            }
            if "trader_notes" not in existing_cols:
                conn.execute(
                    "ALTER TABLE journal_entries ADD COLUMN trader_notes TEXT DEFAULT ''"
                )
                logger.info('{"event":"journal_migration","added_column":"trader_notes"}')

            passport_columns = {
                "signal_status": "TEXT NOT NULL DEFAULT 'UNKNOWN'",
                "invalidation_reason": "TEXT NOT NULL DEFAULT ''",
                "final_outcome": "TEXT NOT NULL DEFAULT 'UNKNOWN'",
                "created_at": "TEXT",
                "outcome_at": "TEXT",
                "atr_method": "TEXT NOT NULL DEFAULT ''",
                "atr_period": "INTEGER",
                "atr_value": "TEXT",
                "parent_range": "TEXT",
                "parent_atr_ratio": "TEXT",
                "sweep_depth": "TEXT",
                "sweep_atr_ratio": "TEXT",
                "atr_grade": "TEXT",
                "atr_configuration_snapshot_id": "TEXT NOT NULL DEFAULT ''",
                "atr_evaluation_status": "TEXT NOT NULL DEFAULT 'NOT_APPLICABLE'",
                "atr_explanation": "TEXT NOT NULL DEFAULT ''",
                "key_level_status": "TEXT NOT NULL DEFAULT 'NOT_APPLICABLE'",
                "key_level_zone": "INTEGER",
                "key_level_match_count": "INTEGER",
                "key_level_configuration_snapshot_id": "TEXT NOT NULL DEFAULT ''",
                "key_level_evidence": "TEXT NOT NULL DEFAULT '{}'",
                "key_level_explanation": "TEXT NOT NULL DEFAULT ''",
                # Volume Profile passport columns (Doc 40 §5, Phase 5)
                "volume_profile_status": "TEXT NOT NULL DEFAULT 'NOT_APPLICABLE'",
                "volume_profile_period": "TEXT",
                "volume_profile_source_timeframe": "TEXT",
                "volume_profile_volume_source": "TEXT",
                "volume_profile_volume_quality": "TEXT",
                "volume_profile_poc": "TEXT",
                "volume_profile_vah": "TEXT",
                "volume_profile_val": "TEXT",
                "volume_profile_total_volume": "TEXT",
                "volume_profile_source_bars": "INTEGER",
                "volume_profile_configuration_snapshot_id": "TEXT NOT NULL DEFAULT ''",
                "volume_profile_evidence": "TEXT NOT NULL DEFAULT '{}'",
                "volume_profile_unknown_reason": "TEXT",
            }
            for column, definition in passport_columns.items():
                if column not in existing_cols:
                    conn.execute(
                        f"ALTER TABLE journal_entries ADD COLUMN {column} {definition}"
                    )
                    logger.info(
                        '{"event":"journal_migration","added_column":"%s"}',
                        column,
                    )

            # Legacy journal rows used closed_at as their only timestamp.
            conn.execute(
                "UPDATE journal_entries SET created_at = closed_at "
                "WHERE created_at IS NULL OR TRIM(created_at) = ''"
            )
            conn.execute(
                "UPDATE journal_entries SET signal_status = "
                "CASE WHEN exit_reason = 'signal_pending' THEN 'OPEN' "
                "WHEN exit_reason IN ('tp2_hit', 'sl_hit') THEN 'RESOLVED' "
                "ELSE 'UNKNOWN' END "
                "WHERE signal_status IS NULL OR signal_status = 'UNKNOWN'"
            )
            conn.execute(
                "UPDATE journal_entries SET final_outcome = "
                "CASE WHEN exit_reason = 'tp2_hit' THEN 'WIN' "
                "WHEN exit_reason = 'sl_hit' THEN 'LOSS' "
                "ELSE 'UNKNOWN' END "
                "WHERE final_outcome IS NULL OR final_outcome = 'UNKNOWN'"
            )

            
    @contextmanager
    def _get_connection(self):
        conn = sqlite3.connect(self.db_path)
        try:
            yield conn
        finally:
            conn.close()

    def add_entry(self, entry: JournalEntry) -> None:
        """Stores a JournalEntry in the database."""
        with self._get_connection() as conn:
            conn.execute('''
                INSERT INTO journal_entries (
                    record_id, candidate_id, symbol, timeframe, profile,
                    strategy_name, direction, confidence_score, confidence_breakdown,
                    lot_size, entry_price, sl_price, tp_price, exit_price,
                    pnl, closed_at, exit_reason, tags, signal_status,
                    invalidation_reason, final_outcome, created_at, outcome_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                entry.record_id,
                entry.candidate_id,
                entry.symbol,
                entry.timeframe,
                entry.profile,
                entry.strategy_name,
                entry.direction,
                entry.confidence_score,
                json.dumps(entry.confidence_breakdown),
                str(entry.lot_size),
                str(entry.entry_price),
                str(entry.sl_price),
                str(entry.tp_price),
                str(entry.exit_price),
                str(entry.pnl),
                entry.closed_at.isoformat(),
                entry.exit_reason,
                json.dumps(entry.tags),
                "RESOLVED",
                "",
                "WIN" if entry.pnl > 0 else "LOSS" if entry.pnl < 0 else "UNKNOWN",
                entry.closed_at.isoformat(),
                entry.closed_at.isoformat(),
            ))
            conn.commit()

    def record_execution(self, record: ExecutionRecord) -> None:
        """Persist one sanitized execution attempt exactly once."""
        request_payload = asdict(record.mt5_request) if record.mt5_request else None
        status = getattr(record.status, "value", str(record.status))
        inserted = False
        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                INSERT OR IGNORE INTO execution_records (
                    execution_record_id, candidate_id, profile, symbol, timeframe,
                    direction, lot_size, risk_evaluation_id, mt5_request_json,
                    preflight_result, order_send_retcode, mt5_order_id,
                    mt5_deal_id, broker_comment, execution_status, error_reason,
                    recovery_suggestion, attempted_at, completed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(record.record_id),
                    str(record.candidate_id),
                    str(record.profile),
                    str(record.symbol),
                    str(record.timeframe),
                    str(getattr(record.direction, "value", record.direction)),
                    str(record.lot_size) if record.lot_size is not None else None,
                    str(record.risk_evaluation_id),
                    json.dumps(request_payload, sort_keys=True) if request_payload is not None else None,
                    record.preflight_result,
                    record.order_send_retcode,
                    record.mt5_order_id,
                    record.mt5_deal_id,
                    record.broker_comment,
                    str(status),
                    record.error_reason,
                    record.recovery_suggestion,
                    record.attempted_at.isoformat(),
                    record.completed_at.isoformat(),
                ),
            )
            conn.commit()
        logger.info(
            '{"event":"execution_record_persisted","record_id":"%s",'
            '"candidate_id":"%s","status":"%s"}',
            str(record.record_id), str(record.candidate_id), str(status),
        )
        inserted = cursor.rowcount > 0
        if inserted:
            self.record_execution_quality_event(
                ExecutionQualityEvent.from_execution_record(record)
            )

    def record_execution_quality_event(self, event: ExecutionQualityEvent) -> None:
        """Persist one append-only execution-quality fact idempotently."""
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO execution_quality_events (
                    event_id, event_type, execution_record_id, candidate_id,
                    profile, symbol, timeframe, direction, position_id,
                    requested_price, actual_price, requested_volume, actual_volume,
                    spread, slippage, stop_loss, take_profit, broker_retcode,
                    broker_comment, action, status, latency_ms, gross_pnl, costs,
                    net_pnl, reason, occurred_at, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                          ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event.event_id,
                    event.event_type,
                    event.execution_record_id,
                    event.candidate_id,
                    event.profile,
                    event.symbol,
                    event.timeframe,
                    event.direction,
                    event.position_id,
                    event.requested_price,
                    event.actual_price,
                    event.requested_volume,
                    event.actual_volume,
                    event.spread,
                    event.slippage,
                    event.stop_loss,
                    event.take_profit,
                    event.broker_retcode,
                    event.broker_comment,
                    event.action,
                    event.status,
                    event.latency_ms,
                    event.gross_pnl,
                    event.costs,
                    event.net_pnl,
                    event.reason[:240],
                    event.occurred_at.isoformat(),
                    json.dumps(event.metadata, sort_keys=True),
                ),
            )
            conn.commit()
        logger.info(
            '{"event":"execution_quality_recorded","quality_event_id":"%s",'
            '"event_type":"%s","execution_record_id":"%s","status":"%s"}',
            event.event_id,
            event.event_type,
            event.execution_record_id,
            event.status,
        )

    def list_execution_quality_events(
        self,
        execution_record_id: Optional[str] = None,
        *,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Return bounded sanitized quality facts for operations analytics."""
        bounded_limit = max(1, min(int(limit), 1000))
        query = "SELECT * FROM execution_quality_events"
        params: list[Any] = []
        if execution_record_id:
            query += " WHERE execution_record_id = ?"
            params.append(str(execution_record_id))
        query += " ORDER BY occurred_at DESC, event_id DESC LIMIT ?"
        params.append(bounded_limit)
        with self._get_connection() as conn:
            rows = conn.execute(query, params).fetchall()
            columns = [item[1] for item in conn.execute(
                "PRAGMA table_info(execution_quality_events)"
            ).fetchall()]
        result = []
        for row in rows:
            item = dict(zip(columns, row))
            if item.get("metadata_json"):
                item["metadata"] = json.loads(item.pop("metadata_json"))
            else:
                item["metadata"] = {}
            result.append(item)
        return result

    def get_execution_record(self, record_id: str) -> Optional[dict]:
        """Return one execution attempt for reconciliation and audit tools."""
        with self._get_connection() as conn:
            row = conn.execute(
                """
                SELECT execution_record_id, candidate_id, profile, symbol, timeframe,
                       direction, lot_size, risk_evaluation_id, mt5_request_json,
                       preflight_result, order_send_retcode, mt5_order_id,
                       mt5_deal_id, broker_comment, execution_status, error_reason,
                       recovery_suggestion, attempted_at, completed_at,
                       mt5_position_id, realized_status, realized_gross_pnl,
                       realized_costs, realized_net_pnl, realized_deals_count,
                       realized_reason, realized_at
                FROM execution_records WHERE execution_record_id = ?
                """,
                (str(record_id),),
            ).fetchone()
        if row is None:
            return None
        columns = (
            "execution_record_id", "candidate_id", "profile", "symbol",
            "timeframe", "direction", "lot_size", "risk_evaluation_id",
            "mt5_request_json", "preflight_result", "order_send_retcode",
            "mt5_order_id", "mt5_deal_id", "broker_comment", "execution_status",
            "error_reason", "recovery_suggestion", "attempted_at", "completed_at",
            "mt5_position_id", "realized_status", "realized_gross_pnl",
            "realized_costs", "realized_net_pnl", "realized_deals_count",
            "realized_reason", "realized_at",
        )
        result = dict(zip(columns, row))
        if result["mt5_request_json"]:
            result["mt5_request"] = json.loads(result.pop("mt5_request_json"))
        else:
            result["mt5_request"] = None
            result.pop("mt5_request_json", None)
        return result

    def link_execution_position(self, record_id: str, position_id: int) -> bool:
        """Attach the broker position identifier discovered after a fill."""
        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                UPDATE execution_records
                SET mt5_position_id = ?
                WHERE execution_record_id = ? AND mt5_position_id IS NULL
                """,
                (int(position_id), str(record_id)),
            )
            conn.commit()
            return cursor.rowcount > 0

    def list_unsettled_executions(self) -> list[dict]:
        """Return filled executions awaiting broker-close reconciliation."""
        with self._get_connection() as conn:
            rows = conn.execute(
                """
                SELECT execution_record_id, candidate_id, symbol, execution_status,
                       mt5_order_id, mt5_deal_id, mt5_position_id, attempted_at
                FROM execution_records
                WHERE execution_status IN ('FILLED', 'PARTIALLY_FILLED', 'SENT')
                  AND realized_status = 'PENDING'
                ORDER BY attempted_at, execution_record_id
                """
            ).fetchall()
        names = (
            "execution_record_id", "candidate_id", "symbol", "execution_status",
            "mt5_order_id", "mt5_deal_id", "mt5_position_id", "attempted_at",
        )
        return [dict(zip(names, row)) for row in rows]

    def record_execution_realization(
        self,
        record_id: str,
        reconciliation: Any,
        realized_at: Optional[datetime] = None,
    ) -> bool:
        """Persist one broker-close reconciliation without overwriting it."""
        status = str(getattr(reconciliation, "status", "UNKNOWN"))
        status = status if status in {"OK", "UNKNOWN"} else "UNKNOWN"
        timestamp = (realized_at or datetime.now(timezone.utc)).isoformat()
        updated = False
        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                UPDATE execution_records
                SET realized_status = ?, realized_gross_pnl = ?,
                    realized_costs = ?, realized_net_pnl = ?,
                    realized_deals_count = ?, realized_reason = ?, realized_at = ?
                WHERE execution_record_id = ? AND realized_status = 'PENDING'
                """,
                (
                    status,
                    str(reconciliation.gross_pnl) if reconciliation.gross_pnl is not None else None,
                    str(reconciliation.costs) if reconciliation.costs is not None else None,
                    str(reconciliation.net_pnl) if reconciliation.net_pnl is not None else None,
                    int(getattr(reconciliation, "deals_count", 0) or 0),
                    str(getattr(reconciliation, "reason", "") or ""),
                    timestamp,
                    str(record_id),
                ),
            )
            conn.commit()
            updated = cursor.rowcount > 0
        if updated:
            execution = self.get_execution_record(record_id) or {}
            self.record_execution_quality_event(
                ExecutionQualityEvent.new(
                    ExecutionQualityEventType.POSITION_REALIZED,
                    execution_record_id=str(record_id),
                    candidate_id=str(execution.get("candidate_id", "")),
                    profile=str(execution.get("profile", "")),
                    symbol=str(execution.get("symbol", "")),
                    timeframe=str(execution.get("timeframe", "")),
                    direction=str(execution.get("direction", "")),
                    position_id=execution.get("mt5_position_id"),
                    status=status,
                    gross_pnl=(
                        str(reconciliation.gross_pnl)
                        if reconciliation.gross_pnl is not None
                        else None
                    ),
                    costs=(
                        str(reconciliation.costs)
                        if reconciliation.costs is not None
                        else None
                    ),
                    net_pnl=(
                        str(reconciliation.net_pnl)
                        if reconciliation.net_pnl is not None
                        else None
                    ),
                    reason=str(getattr(reconciliation, "reason", "") or "")[:240],
                    occurred_at=(realized_at or datetime.now(timezone.utc)),
                )
            )
        return updated
            
    def get_entries(self, profile: Optional[str] = None) -> List[JournalEntry]:
        """Retrieves JournalEntry records, optionally filtered by profile."""
        from decimal import Decimal
        
        query = "SELECT * FROM journal_entries"
        params = []
        if profile:
            query += " WHERE profile = ?"
            params.append(profile)
            
        entries = []
        with self._get_connection() as conn:
            cursor = conn.execute(query, params)
            for row in cursor:
                entries.append(JournalEntry(
                    record_id=row[0],
                    candidate_id=row[1],
                    symbol=row[2],
                    timeframe=row[3],
                    profile=row[4],
                    strategy_name=row[5],
                    direction=row[6],
                    confidence_score=row[7],
                    confidence_breakdown=json.loads(row[8]),
                    lot_size=Decimal(row[9]),
                    entry_price=Decimal(row[10]),
                    sl_price=Decimal(row[11]),
                    tp_price=Decimal(row[12]),
                    exit_price=Decimal(row[13]),
                    pnl=Decimal(row[14]),
                    closed_at=datetime.fromisoformat(row[15]),
                    exit_reason=row[16],
                    tags=json.loads(row[17])
                ))
        return entries

    def get_signal_outcome(self, record_id: str) -> dict[str, Any] | None:
        """Return the authoritative Passport outcome state for one signal."""
        with self._get_connection() as conn:
            row = conn.execute(
                """
                SELECT record_id, symbol, timeframe, signal_status,
                       final_outcome, exit_reason, invalidation_reason,
                       outcome_at
                FROM journal_entries
                WHERE record_id = ?
                """,
                (str(record_id),),
            ).fetchone()
        if row is None:
            return None
        return {
            "record_id": row[0],
            "symbol": row[1],
            "timeframe": row[2],
            "signal_status": row[3],
            "final_outcome": row[4],
            "exit_reason": row[5],
            "invalidation_reason": row[6],
            "outcome_at": row[7],
        }

    def add_signal_entry(
        self,
        record_id: str,
        candidate_id: str,
        symbol: str,
        timeframe: str,
        profile: str,
        direction: str,
        confidence_score: float,
        confidence_breakdown: dict,
        entry_price: Decimal,
        sl_price: Decimal,
        tp_price: Decimal,
        atr_quality: Optional[ATRQualityResult] = None,
        key_level_evaluation: Optional[dict] = None,
        volume_profile: Optional[VolumeProfileResult] = None,
        volume_profiles: Optional[tuple[VolumeProfileResult, ...]] = None,
    ) -> None:
        """
        Write a signal entry to the journal when an alert is constructed.
        This preserves Passport evidence even when delivery is suppressed or
        a configured channel fails.
        exit_reason is set to 'signal_pending' until the outcome is known.
        pnl and exit_price are 0 until the outcome is resolved.
        """
        now = datetime.now(timezone.utc).isoformat()
        atr_fields = atr_quality_fields(atr_quality)
        key_level_fields = key_level_passport_fields(key_level_evaluation)
        vp_fields = volume_profile_passport_fields(
            volume_profiles if volume_profiles is not None else volume_profile
        )
        with self._get_connection() as conn:
            conn.execute('''
                INSERT OR IGNORE INTO journal_entries (
                    record_id, candidate_id, symbol, timeframe, profile,
                    strategy_name, direction, confidence_score, confidence_breakdown,
                    lot_size, entry_price, sl_price, tp_price, exit_price,
                    pnl, closed_at, exit_reason, tags, signal_status,
                    invalidation_reason, final_outcome, created_at, outcome_at,
                    atr_method, atr_period, atr_value, parent_range,
                    parent_atr_ratio, sweep_depth, sweep_atr_ratio, atr_grade,
                    atr_configuration_snapshot_id, atr_evaluation_status,
                    atr_explanation, key_level_status, key_level_zone,
                    key_level_match_count, key_level_configuration_snapshot_id,
                    key_level_evidence, key_level_explanation,
                    volume_profile_status, volume_profile_period,
                    volume_profile_source_timeframe, volume_profile_volume_source,
                    volume_profile_volume_quality, volume_profile_poc,
                    volume_profile_vah, volume_profile_val,
                    volume_profile_total_volume, volume_profile_source_bars,
                    volume_profile_configuration_snapshot_id,
                    volume_profile_evidence, volume_profile_unknown_reason
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                record_id,
                candidate_id,
                symbol,
                timeframe,
                profile,
                "CRT",
                direction,
                confidence_score,
                json.dumps(confidence_breakdown),
                "0",
                str(entry_price),
                str(sl_price),
                str(tp_price),
                "0",
                "0",
                now,
                "signal_pending",
                json.dumps(["signal"]),
                "OPEN",
                "",
                "UNKNOWN",
                now,
                None,
                atr_fields["atr_method"],
                atr_fields["atr_period"],
                atr_fields["atr_value"],
                atr_fields["parent_range"],
                atr_fields["parent_atr_ratio"],
                atr_fields["sweep_depth"],
                atr_fields["sweep_atr_ratio"],
                atr_fields["atr_grade"],
                atr_fields["atr_configuration_snapshot_id"],
                atr_fields["atr_evaluation_status"],
                atr_fields["atr_explanation"],
                key_level_fields["key_level_status"],
                int(key_level_fields["key_level_zone"]) if key_level_fields["key_level_zone"] is not None else None,
                key_level_fields["key_level_match_count"],
                key_level_fields["key_level_configuration_snapshot_id"],
                key_level_fields["key_level_evidence"],
                key_level_fields["key_level_explanation"],
                vp_fields["volume_profile_status"],
                vp_fields["volume_profile_period"],
                vp_fields["volume_profile_source_timeframe"],
                vp_fields["volume_profile_volume_source"],
                vp_fields["volume_profile_volume_quality"],
                vp_fields["volume_profile_poc"],
                vp_fields["volume_profile_vah"],
                vp_fields["volume_profile_val"],
                vp_fields["volume_profile_total_volume"],
                vp_fields["volume_profile_source_bars"],
                vp_fields["volume_profile_configuration_snapshot_id"],
                vp_fields["volume_profile_evidence"],
                vp_fields["volume_profile_unknown_reason"],
            ))
            conn.commit()
        logger.info(
            '{"event":"journal_signal_entry","record_id":"%s","symbol":"%s","direction":"%s"}',
            record_id, symbol, direction,
        )

    def update_signal_outcome(
        self,
        record_id: str,
        exit_reason: str,  # 'tp1_hit', 'tp2_hit', 'sl_hit'
        exit_price: Decimal,
        pnl: Decimal,
    ) -> None:
        """
        Update the outcome of a signal entry once TP or SL is resolved.
        """
        now = datetime.now(timezone.utc).isoformat()
        with self._get_connection() as conn:
            existing = conn.execute(
                "SELECT signal_status, final_outcome FROM journal_entries WHERE record_id = ?",
                (record_id,),
            ).fetchone()
            if existing and (
                existing[0] in {"RESOLVED", "EXPIRED"}
                or existing[1] in {"WIN", "LOSS"}
            ):
                logger.info(
                    '{"event":"journal_signal_outcome_ignored","record_id":"%s","reason":"already_final"}',
                    record_id,
                )
                return
            final_outcome = "WIN" if exit_reason == "tp2_hit" else "LOSS" if exit_reason == "sl_hit" else "UNKNOWN"
            invalidation_reason = "stop_loss_hit" if exit_reason == "sl_hit" else ""
            conn.execute('''
                UPDATE journal_entries
                SET exit_reason = ?, exit_price = ?, pnl = ?, closed_at = ?,
                    signal_status = 'RESOLVED',
                    invalidation_reason = ?,
                    final_outcome = ?,
                    outcome_at = ?
                WHERE record_id = ?
            ''', (
                exit_reason,
                str(exit_price),
                str(pnl),
                now,
                invalidation_reason,
                final_outcome,
                now,
                record_id,
            ))
            conn.commit()
        logger.info('{"event":"journal_signal_outcome","record_id":"%s","exit_reason":"%s"}',
                    record_id, exit_reason)

    def mark_signal_expired(self, record_id: str) -> None:
        """Record an ended monitoring window without inventing an outcome."""
        now = datetime.now(timezone.utc).isoformat()
        with self._get_connection() as conn:
            existing = conn.execute(
                "SELECT signal_status, final_outcome FROM journal_entries WHERE record_id = ?",
                (record_id,),
            ).fetchone()
            if not existing or existing[0] in {"RESOLVED", "EXPIRED"} or existing[1] in {"WIN", "LOSS"}:
                return
            conn.execute(
                """
                UPDATE journal_entries
                SET signal_status = 'EXPIRED',
                    invalidation_reason = '',
                    final_outcome = 'UNKNOWN',
                    exit_reason = 'signal_expired',
                    outcome_at = ?
                WHERE record_id = ?
                """,
                (now, record_id),
            )
            conn.commit()
        logger.info(
            '{"event":"journal_signal_expired","record_id":"%s"}',
            record_id,
        )
