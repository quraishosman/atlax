"""Explicit PostgreSQL repositories for ATLAX shared state.

These repositories are deliberately opt-in.  They provide the row-level
boundary needed for the staged migration, but they do not change any existing
local store constructor or select PostgreSQL from the environment.

Business modules should depend on repository methods, never on raw SQL or a
database URL.  AI and MCP callers must use their approved read-only services.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Callable, Iterable, Optional

from core.settings.storage_config import StorageConfig
from core.models.execution import ExecutionRecord
from core.models.execution_quality import (
    ExecutionQualityEvent,
    ExecutionQualityEventType,
)
from core.models.signal_replay import ReplayLifecycleStatus, ReplaySnapshot
from core.storage.postgres import PostgresDatabase, PostgresUnavailable


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _timestamp(value: Any, default: Optional[datetime] = None) -> Optional[datetime]:
    if value in (None, ""):
        return default
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value))
        except (TypeError, ValueError):
            return default
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _decimal(value: Any) -> Optional[Decimal]:
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value))
    except (TypeError, ValueError):
        return None


def _jsonb(value: Any) -> Any:
    """Adapt JSONB lazily so local mode does not require psycopg."""
    try:
        from psycopg.types.json import Jsonb
    except ImportError as exc:
        raise PostgresUnavailable(
            "PostgreSQL repositories require psycopg[binary]."
        ) from exc
    return Jsonb(value)


def _record(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


NO_WRITE = object()


class _Repository:
    def __init__(self, database: PostgresDatabase) -> None:
        self.database = database


class PostgresAuthRepository(_Repository):
    """Direct row-level access to dashboard/API users."""

    def get(self, username: str) -> Optional[dict[str, Any]]:
        with self.database.connection() as connection:
            row = connection.execute(
                """
                SELECT username, hashed_password, role, created_at
                FROM auth_users WHERE username = %s
                """,
                (str(username),),
            ).fetchone()
        if not row:
            return None
        return {
            "username": row[0],
            "hashed_password": row[1],
            "role": row[2],
            "created_at": row[3].isoformat() if row[3] else None,
        }

    def create(
        self,
        username: str,
        hashed_password: str,
        role: str = "user",
    ) -> dict[str, Any]:
        with self.database.connection() as connection:
            row = connection.execute(
                """
                INSERT INTO auth_users (username, hashed_password, role)
                VALUES (%s, %s, %s)
                RETURNING username, hashed_password, role, created_at
                """,
                (str(username), str(hashed_password), str(role)),
            ).fetchone()
        return {
            "username": row[0],
            "hashed_password": row[1],
            "role": row[2],
            "created_at": row[3].isoformat() if row[3] else None,
        }

    def list(self) -> list[dict[str, Any]]:
        with self.database.connection() as connection:
            rows = connection.execute(
                """
                SELECT username, hashed_password, role, created_at
                FROM auth_users ORDER BY username
                """
            ).fetchall()
        return [
            {
                "username": row[0],
                "hashed_password": row[1],
                "role": row[2],
                "created_at": row[3].isoformat() if row[3] else None,
            }
            for row in rows
        ]


class PostgresSubscriberRepository(_Repository):
    """Row-level subscriber access with stale-write protection."""

    def upsert(self, subscriber: dict[str, Any]) -> None:
        record = _record(subscriber)
        chat_id = str(record.get("chat_id", ""))
        if not chat_id:
            raise ValueError("subscriber chat_id is required")
        updated_at = _timestamp(record.get("updated_at"), _now()) or _now()
        with self.database.connection() as connection:
            connection.execute(
                """
                INSERT INTO subscribers (
                    chat_id, username, active, subscription_status, style,
                    subscription_expires_at, record, updated_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (chat_id) DO UPDATE SET
                    username = EXCLUDED.username,
                    active = EXCLUDED.active,
                    subscription_status = EXCLUDED.subscription_status,
                    style = EXCLUDED.style,
                    subscription_expires_at = EXCLUDED.subscription_expires_at,
                    record = EXCLUDED.record,
                    updated_at = EXCLUDED.updated_at
                WHERE EXCLUDED.updated_at >= subscribers.updated_at
                """,
                (
                    chat_id,
                    str(record.get("username", "")),
                    bool(record.get("active", False)),
                    str(record.get("subscription_status", "inactive")),
                    str(record.get("style", "")),
                    _timestamp(record.get("subscription_expires_at")),
                    _jsonb(record),
                    updated_at,
                ),
            )

    def get(self, chat_id: str) -> Optional[dict[str, Any]]:
        with self.database.connection() as connection:
            row = connection.execute(
                "SELECT record FROM subscribers WHERE chat_id = %s",
                (str(chat_id),),
            ).fetchone()
        return _record(row[0]) if row else None

    def mutate(
        self,
        chat_id: str,
        mutator: Callable[[dict[str, Any]], Any],
    ) -> Any:
        """Lock one subscriber row, apply a mutation, and commit it atomically."""
        with self.database.connection() as connection:
            row = connection.execute(
                "SELECT record FROM subscribers WHERE chat_id = %s FOR UPDATE",
                (str(chat_id),),
            ).fetchone()
            if not row:
                return None
            record = _record(row[0])
            result = mutator(record)
            if result is NO_WRITE:
                return None
            updated_at = _now()
            record["updated_at"] = updated_at.isoformat()
            expires_at = _timestamp(record.get("subscription_expires_at"))
            connection.execute(
                """
                UPDATE subscribers
                SET username = %s, active = %s, subscription_status = %s,
                    style = %s, subscription_expires_at = %s,
                    record = %s, updated_at = %s
                WHERE chat_id = %s
                """,
                (
                    str(record.get("username", "")),
                    bool(record.get("active", False)),
                    str(record.get("subscription_status", "inactive")),
                    str(record.get("style", "")),
                    expires_at,
                    _jsonb(record),
                    updated_at,
                    str(chat_id),
                ),
            )
            return result

    def list(self, active_only: bool = False) -> list[dict[str, Any]]:
        query = "SELECT record FROM subscribers"
        if active_only:
            query += " WHERE active = TRUE"
        query += " ORDER BY chat_id"
        with self.database.connection() as connection:
            rows = connection.execute(query).fetchall()
        return [_record(row[0]) for row in rows]


class PostgresInviteCodeRepository(_Repository):
    """Direct invite-code access; revocation is represented by a row update."""

    def upsert(self, code: str, value: dict[str, Any]) -> None:
        normalized = str(code).strip().upper()
        if not normalized:
            raise ValueError("invite code is required")
        record = {"code": normalized, **_record(value)}
        updated_at = _timestamp(record.get("updated_at"), _now()) or _now()
        with self.database.connection() as connection:
            connection.execute(
                """
                INSERT INTO invite_codes (code, used_by, expires_at, record, updated_at)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (code) DO UPDATE SET
                    used_by = EXCLUDED.used_by,
                    expires_at = EXCLUDED.expires_at,
                    record = EXCLUDED.record,
                    updated_at = EXCLUDED.updated_at
                WHERE EXCLUDED.updated_at >= invite_codes.updated_at
                """,
                (
                    normalized,
                    record.get("used_by"),
                    _timestamp(record.get("expires")),
                    _jsonb(record),
                    updated_at,
                ),
            )

    def get(self, code: str) -> Optional[dict[str, Any]]:
        with self.database.connection() as connection:
            row = connection.execute(
                "SELECT record FROM invite_codes WHERE code = %s",
                (str(code).strip().upper(),),
            ).fetchone()
        return _record(row[0]) if row else None

    def list(self) -> list[dict[str, Any]]:
        with self.database.connection() as connection:
            rows = connection.execute(
                "SELECT record FROM invite_codes ORDER BY code"
            ).fetchall()
        return [_record(row[0]) for row in rows]


class PostgresPaymentRepository(_Repository):
    """Payment intent repository with monotonic, non-destructive updates."""

    def upsert(self, intent: dict[str, Any]) -> None:
        record = _record(intent)
        intent_id = str(record.get("intent_id", ""))
        reference = str(record.get("reference", ""))
        if not intent_id or not reference:
            raise ValueError("payment intent_id and reference are required")
        created_at = _timestamp(record.get("created_at"), _now()) or _now()
        updated_at = _timestamp(record.get("updated_at"), created_at) or created_at
        with self.database.connection() as connection:
            connection.execute(
                """
                INSERT INTO payment_intents (
                    intent_id, reference, chat_id, status, method, style,
                    amount_usd, created_at, expires_at, updated_at, record
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (intent_id) DO UPDATE SET
                    reference = EXCLUDED.reference,
                    chat_id = EXCLUDED.chat_id,
                    status = EXCLUDED.status,
                    method = EXCLUDED.method,
                    style = EXCLUDED.style,
                    amount_usd = EXCLUDED.amount_usd,
                    created_at = EXCLUDED.created_at,
                    expires_at = EXCLUDED.expires_at,
                    updated_at = EXCLUDED.updated_at,
                    record = EXCLUDED.record
                WHERE EXCLUDED.updated_at >= payment_intents.updated_at
                """,
                (
                    intent_id,
                    reference,
                    str(record.get("chat_id", "")),
                    str(record.get("status", "unknown")),
                    str(record.get("method", "")),
                    str(record.get("style", "")),
                    _decimal(record.get("amount_usd")) or Decimal("0"),
                    created_at,
                    _timestamp(record.get("expires_at")),
                    updated_at,
                    _jsonb(record),
                ),
            )

    def get(self, intent_id: str) -> Optional[dict[str, Any]]:
        with self.database.connection() as connection:
            row = connection.execute(
                "SELECT record FROM payment_intents WHERE intent_id = %s",
                (str(intent_id),),
            ).fetchone()
        return _record(row[0]) if row else None

    def get_by_reference(self, reference: str) -> Optional[dict[str, Any]]:
        with self.database.connection() as connection:
            row = connection.execute(
                "SELECT record FROM payment_intents WHERE reference = %s",
                (str(reference).strip().upper(),),
            ).fetchone()
        return _record(row[0]) if row else None

    def mutate_by_reference(
        self,
        reference: str,
        mutator: Callable[[dict[str, Any]], Any],
    ) -> Any:
        """Lock one payment row, apply a mutation, and commit it atomically."""
        with self.database.connection() as connection:
            row = connection.execute(
                "SELECT record FROM payment_intents WHERE reference = %s FOR UPDATE",
                (str(reference).strip().upper(),),
            ).fetchone()
            if not row:
                return None
            record = _record(row[0])
            result = mutator(record)
            if result is NO_WRITE:
                return None
            updated_at = _now()
            record["updated_at"] = updated_at.isoformat()
            created_at = _timestamp(record.get("created_at"), updated_at) or updated_at
            with_expiry = _timestamp(record.get("expires_at"))
            connection.execute(
                """
                UPDATE payment_intents
                SET chat_id = %s, status = %s, method = %s, style = %s,
                    amount_usd = %s, created_at = %s, expires_at = %s,
                    updated_at = %s, record = %s
                WHERE reference = %s
                """,
                (
                    str(record.get("chat_id", "")),
                    str(record.get("status", "unknown")),
                    str(record.get("method", "")),
                    str(record.get("style", "")),
                    _decimal(record.get("amount_usd")) or Decimal("0"),
                    created_at,
                    with_expiry,
                    updated_at,
                    _jsonb(record),
                    str(reference).strip().upper(),
                ),
            )
            return result

    def list(
        self,
        status: Optional[str] = None,
        chat_id: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[str] = []
        if status is not None:
            clauses.append("status = %s")
            params.append(str(status))
        if chat_id is not None:
            clauses.append("chat_id = %s")
            params.append(str(chat_id))
        query = "SELECT record FROM payment_intents"
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY created_at DESC"
        with self.database.connection() as connection:
            rows = connection.execute(query, tuple(params)).fetchall()
        return [_record(row[0]) for row in rows]


class PostgresJournalRepository(_Repository):
    """Signal Passport and journal repository with terminal-state guards."""

    def upsert(self, entry: dict[str, Any]) -> None:
        record = _record(entry)
        record_id = str(record.get("record_id", ""))
        if not record_id:
            raise ValueError("journal record_id is required")
        closed_at = _timestamp(record.get("closed_at"), _now()) or _now()
        created_at = _timestamp(record.get("created_at"), closed_at) or closed_at
        key_level_evidence = record.get("key_level_evidence", {})
        if isinstance(key_level_evidence, str):
            try:
                key_level_evidence = json.loads(key_level_evidence or "{}")
            except (TypeError, ValueError):
                key_level_evidence = {}
        if not isinstance(key_level_evidence, dict):
            key_level_evidence = {}
        volume_profile_evidence = record.get("volume_profile_evidence", {})
        if isinstance(volume_profile_evidence, str):
            try:
                volume_profile_evidence = json.loads(volume_profile_evidence or "{}")
            except (TypeError, ValueError):
                volume_profile_evidence = {}
        if not isinstance(volume_profile_evidence, (dict, list)):
            volume_profile_evidence = {}
        with self.database.connection() as connection:
            connection.execute(
                """
                INSERT INTO journal_entries (
                    record_id, candidate_id, symbol, timeframe, profile,
                    strategy_name, direction, confidence_score,
                    confidence_breakdown, lot_size, entry_price, sl_price,
                    tp_price, exit_price, pnl, closed_at, exit_reason, tags,
                    trader_notes, signal_status, invalidation_reason,
                    final_outcome, created_at, outcome_at, atr_method, atr_period,
                    atr_value, parent_range, parent_atr_ratio, sweep_depth,
                    sweep_atr_ratio, atr_grade, atr_configuration_snapshot_id,
                    atr_evaluation_status, atr_explanation, key_level_status,
                    key_level_zone, key_level_match_count,
                    key_level_configuration_snapshot_id, key_level_evidence,
                    key_level_explanation, volume_profile_status,
                    volume_profile_period, volume_profile_source_timeframe,
                    volume_profile_volume_source, volume_profile_volume_quality,
                    volume_profile_poc, volume_profile_vah, volume_profile_val,
                    volume_profile_total_volume, volume_profile_source_bars,
                    volume_profile_configuration_snapshot_id,
                    volume_profile_evidence, volume_profile_unknown_reason
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s
                )
                ON CONFLICT (record_id) DO UPDATE SET
                    candidate_id = EXCLUDED.candidate_id,
                    symbol = EXCLUDED.symbol,
                    timeframe = EXCLUDED.timeframe,
                    profile = EXCLUDED.profile,
                    strategy_name = EXCLUDED.strategy_name,
                    direction = EXCLUDED.direction,
                    confidence_score = EXCLUDED.confidence_score,
                    confidence_breakdown = EXCLUDED.confidence_breakdown,
                    lot_size = EXCLUDED.lot_size,
                    entry_price = EXCLUDED.entry_price,
                    sl_price = EXCLUDED.sl_price,
                    tp_price = EXCLUDED.tp_price,
                    exit_price = EXCLUDED.exit_price,
                    pnl = EXCLUDED.pnl,
                    closed_at = EXCLUDED.closed_at,
                    exit_reason = EXCLUDED.exit_reason,
                    tags = EXCLUDED.tags,
                    trader_notes = EXCLUDED.trader_notes,
                    signal_status = EXCLUDED.signal_status,
                    invalidation_reason = EXCLUDED.invalidation_reason,
                    final_outcome = EXCLUDED.final_outcome,
                    created_at = EXCLUDED.created_at,
                    outcome_at = EXCLUDED.outcome_at,
                    atr_method = EXCLUDED.atr_method,
                    atr_period = EXCLUDED.atr_period,
                    atr_value = EXCLUDED.atr_value,
                    parent_range = EXCLUDED.parent_range,
                    parent_atr_ratio = EXCLUDED.parent_atr_ratio,
                    sweep_depth = EXCLUDED.sweep_depth,
                    sweep_atr_ratio = EXCLUDED.sweep_atr_ratio,
                    atr_grade = EXCLUDED.atr_grade,
                    atr_configuration_snapshot_id = EXCLUDED.atr_configuration_snapshot_id,
                    atr_evaluation_status = EXCLUDED.atr_evaluation_status,
                    atr_explanation = EXCLUDED.atr_explanation,
                    key_level_status = EXCLUDED.key_level_status,
                    key_level_zone = EXCLUDED.key_level_zone,
                    key_level_match_count = EXCLUDED.key_level_match_count,
                    key_level_configuration_snapshot_id = EXCLUDED.key_level_configuration_snapshot_id,
                    key_level_evidence = EXCLUDED.key_level_evidence,
                    key_level_explanation = EXCLUDED.key_level_explanation,
                    volume_profile_status = EXCLUDED.volume_profile_status,
                    volume_profile_period = EXCLUDED.volume_profile_period,
                    volume_profile_source_timeframe = EXCLUDED.volume_profile_source_timeframe,
                    volume_profile_volume_source = EXCLUDED.volume_profile_volume_source,
                    volume_profile_volume_quality = EXCLUDED.volume_profile_volume_quality,
                    volume_profile_poc = EXCLUDED.volume_profile_poc,
                    volume_profile_vah = EXCLUDED.volume_profile_vah,
                    volume_profile_val = EXCLUDED.volume_profile_val,
                    volume_profile_total_volume = EXCLUDED.volume_profile_total_volume,
                    volume_profile_source_bars = EXCLUDED.volume_profile_source_bars,
                    volume_profile_configuration_snapshot_id = EXCLUDED.volume_profile_configuration_snapshot_id,
                    volume_profile_evidence = EXCLUDED.volume_profile_evidence,
                    volume_profile_unknown_reason = EXCLUDED.volume_profile_unknown_reason
                WHERE journal_entries.signal_status NOT IN ('RESOLVED', 'EXPIRED')
                """,
                (
                    record_id,
                    str(record.get("candidate_id", "")),
                    str(record.get("symbol", "")),
                    str(record.get("timeframe", "")),
                    str(record.get("profile", "")),
                    str(record.get("strategy_name", "")),
                    str(record.get("direction", "")),
                    float(record.get("confidence_score") or 0),
                    _jsonb(record.get("confidence_breakdown") or {}),
                    _decimal(record.get("lot_size")),
                    _decimal(record.get("entry_price")),
                    _decimal(record.get("sl_price")),
                    _decimal(record.get("tp_price")),
                    _decimal(record.get("exit_price")),
                    _decimal(record.get("pnl")),
                    closed_at,
                    str(record.get("exit_reason", "")),
                    _jsonb(record.get("tags") or []),
                    str(record.get("trader_notes", "")),
                    str(record.get("signal_status", "UNKNOWN")),
                    str(record.get("invalidation_reason", "")),
                    str(record.get("final_outcome", "UNKNOWN")),
                    created_at,
                    _timestamp(record.get("outcome_at")),
                    str(record.get("atr_method", "")),
                    record.get("atr_period"),
                    _decimal(record.get("atr_value")),
                    _decimal(record.get("parent_range")),
                    _decimal(record.get("parent_atr_ratio")),
                    _decimal(record.get("sweep_depth")),
                    _decimal(record.get("sweep_atr_ratio")),
                    record.get("atr_grade"),
                    str(record.get("atr_configuration_snapshot_id", "")),
                    str(record.get("atr_evaluation_status", "NOT_APPLICABLE")),
                    str(record.get("atr_explanation", "")),
                    str(record.get("key_level_status", "NOT_APPLICABLE")),
                    record.get("key_level_zone"),
                    record.get("key_level_match_count"),
                    str(record.get("key_level_configuration_snapshot_id", "")),
                    _jsonb(key_level_evidence),
                    str(record.get("key_level_explanation", "")),
                    str(record.get("volume_profile_status", "NOT_APPLICABLE")),
                    record.get("volume_profile_period"),
                    record.get("volume_profile_source_timeframe"),
                    record.get("volume_profile_volume_source"),
                    record.get("volume_profile_volume_quality"),
                    record.get("volume_profile_poc"),
                    record.get("volume_profile_vah"),
                    record.get("volume_profile_val"),
                    record.get("volume_profile_total_volume"),
                    record.get("volume_profile_source_bars"),
                    str(record.get("volume_profile_configuration_snapshot_id", "")),
                    _jsonb(volume_profile_evidence),
                    record.get("volume_profile_unknown_reason"),
                ),
            )

    def list(self, profile: Optional[str] = None) -> list[dict[str, Any]]:
        query = """
            SELECT record_id, candidate_id, symbol, timeframe, profile,
                   strategy_name, direction, confidence_score,
                   confidence_breakdown, lot_size, entry_price, sl_price,
                   tp_price, exit_price, pnl, closed_at, exit_reason, tags,
                   trader_notes, signal_status, invalidation_reason,
                   final_outcome, created_at, outcome_at, atr_method, atr_period,
                   atr_value, parent_range, parent_atr_ratio, sweep_depth,
                   sweep_atr_ratio, atr_grade, atr_configuration_snapshot_id,
                   atr_evaluation_status, atr_explanation, key_level_status,
                   key_level_zone, key_level_match_count,
                   key_level_configuration_snapshot_id, key_level_evidence,
                   key_level_explanation, volume_profile_status,
                   volume_profile_period, volume_profile_source_timeframe,
                   volume_profile_volume_source, volume_profile_volume_quality,
                   volume_profile_poc, volume_profile_vah, volume_profile_val,
                   volume_profile_total_volume, volume_profile_source_bars,
                   volume_profile_configuration_snapshot_id,
                   volume_profile_evidence, volume_profile_unknown_reason
            FROM journal_entries
        """
        params: tuple[str, ...] = ()
        if profile:
            query += " WHERE profile = %s"
            params = (str(profile),)
        query += " ORDER BY created_at DESC, record_id"
        with self.database.connection() as connection:
            rows = connection.execute(query, params).fetchall()
        return [self._row_to_record(row) for row in rows]

    @staticmethod
    def _row_to_record(row: Any) -> dict[str, Any]:
        names = (
            "record_id", "candidate_id", "symbol", "timeframe", "profile",
            "strategy_name", "direction", "confidence_score",
            "confidence_breakdown", "lot_size", "entry_price", "sl_price",
            "tp_price", "exit_price", "pnl", "closed_at", "exit_reason",
            "tags", "trader_notes", "signal_status", "invalidation_reason",
            "final_outcome", "created_at", "outcome_at", "atr_method", "atr_period",
            "atr_value", "parent_range", "parent_atr_ratio", "sweep_depth",
            "sweep_atr_ratio", "atr_grade", "atr_configuration_snapshot_id",
            "atr_evaluation_status", "atr_explanation", "key_level_status",
            "key_level_zone", "key_level_match_count",
            "key_level_configuration_snapshot_id", "key_level_evidence",
            "key_level_explanation", "volume_profile_status",
            "volume_profile_period", "volume_profile_source_timeframe",
            "volume_profile_volume_source", "volume_profile_volume_quality",
            "volume_profile_poc", "volume_profile_vah", "volume_profile_val",
            "volume_profile_total_volume", "volume_profile_source_bars",
            "volume_profile_configuration_snapshot_id", "volume_profile_evidence",
            "volume_profile_unknown_reason",
        )
        result = dict(zip(names, row))
        for key in ("closed_at", "created_at", "outcome_at"):
            if isinstance(result.get(key), datetime):
                result[key] = result[key].isoformat()
        for key in (
            "lot_size", "entry_price", "sl_price", "tp_price", "exit_price", "pnl",
            "atr_value", "parent_range", "parent_atr_ratio", "sweep_depth",
            "sweep_atr_ratio",
        ):
            if result.get(key) is not None:
                result[key] = str(result[key])
        return result

    def update_signal_outcome(
        self,
        record_id: str,
        exit_reason: str,
        exit_price: Decimal,
        pnl: Decimal,
    ) -> bool:
        final_outcome = (
            "WIN" if exit_reason == "tp2_hit"
            else "LOSS" if exit_reason == "sl_hit"
            else "UNKNOWN"
        )
        invalidation_reason = "stop_loss_hit" if exit_reason == "sl_hit" else ""
        with self.database.connection() as connection:
            cursor = connection.execute(
                """
                UPDATE journal_entries
                SET exit_reason = %s, exit_price = %s, pnl = %s,
                    closed_at = %s, signal_status = 'RESOLVED',
                    invalidation_reason = %s, final_outcome = %s,
                    outcome_at = %s
                WHERE record_id = %s
                  AND signal_status NOT IN ('RESOLVED', 'EXPIRED')
                  AND final_outcome NOT IN ('WIN', 'LOSS')
                """,
                (
                    str(exit_reason), exit_price, pnl, _now(),
                    invalidation_reason, final_outcome, _now(), str(record_id),
                ),
            )
            return cursor.rowcount > 0

    def mark_signal_expired(self, record_id: str) -> bool:
        with self.database.connection() as connection:
            cursor = connection.execute(
                """
                UPDATE journal_entries
                SET signal_status = 'EXPIRED', invalidation_reason = '',
                    final_outcome = 'UNKNOWN', exit_reason = 'signal_expired',
                    outcome_at = %s
                WHERE record_id = %s
                  AND signal_status NOT IN ('RESOLVED', 'EXPIRED')
                  AND final_outcome NOT IN ('WIN', 'LOSS')
                """,
                (_now(), str(record_id)),
            )
            return cursor.rowcount > 0

    def append_note_for_symbol(self, symbol: str, note_text: str) -> bool:
        """Append one sanitized note to the latest journal row for a symbol."""
        with self.database.connection() as connection:
            row = connection.execute(
                """
                SELECT record_id, trader_notes FROM journal_entries
                WHERE UPPER(symbol) = UPPER(%s)
                ORDER BY closed_at DESC LIMIT 1
                FOR UPDATE
                """,
                (str(symbol).upper(),),
            ).fetchone()
            if not row:
                return False
            existing = str(row[1] or "")
            updated = (existing + "\n" + str(note_text)).strip()
            connection.execute(
                "UPDATE journal_entries SET trader_notes = %s WHERE record_id = %s",
                (updated, str(row[0])),
            )
            return True


class PostgresApprovalRepository(_Repository):
    """Storage boundary for pending execution approvals."""

    def upsert(self, item: dict[str, Any]) -> None:
        record = _record(item)
        candidate_id = str(record.get("candidate_id", ""))
        if not candidate_id:
            raise ValueError("approval candidate_id is required")
        created_at = _timestamp(record.get("created_at"), _now()) or _now()
        updated_at = _timestamp(record.get("updated_at"), created_at) or created_at
        with self.database.connection() as connection:
            connection.execute(
                """
                INSERT INTO approval_queue (
                    candidate_id, status, profile, expires_at, request,
                    created_at, updated_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (candidate_id) DO UPDATE SET
                    status = EXCLUDED.status,
                    profile = EXCLUDED.profile,
                    expires_at = EXCLUDED.expires_at,
                    request = EXCLUDED.request,
                    updated_at = EXCLUDED.updated_at
                WHERE EXCLUDED.updated_at >= approval_queue.updated_at
                """,
                (
                    candidate_id,
                    str(record.get("status", "PENDING")),
                    str(record.get("profile", "")),
                    _timestamp(record.get("expires_at")),
                    _jsonb(record.get("request") or {}),
                    created_at,
                    updated_at,
                ),
            )

    def get(self, candidate_id: str, status: Optional[str] = None) -> Optional[dict[str, Any]]:
        query = """
            SELECT candidate_id, status, profile, expires_at, request,
                   created_at, updated_at
            FROM approval_queue WHERE candidate_id = %s
        """
        params: list[str] = [str(candidate_id)]
        if status is not None:
            query += " AND status = %s"
            params.append(str(status))
        with self.database.connection() as connection:
            row = connection.execute(query, tuple(params)).fetchone()
        return self._row_to_record(row) if row else None

    def list_pending(self, profile: Optional[str] = None) -> list[dict[str, Any]]:
        return self.list_by_status("PENDING", profile)

    def list_by_status(
        self,
        status: str,
        profile: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        query = """
            SELECT candidate_id, status, profile, expires_at, request,
                   created_at, updated_at
            FROM approval_queue WHERE status = %s
        """
        params: tuple[str, ...] = (str(status),)
        if profile and profile != "Global View":
            query += " AND profile = %s"
            params += (str(profile),)
        query += " ORDER BY created_at DESC"
        with self.database.connection() as connection:
            rows = connection.execute(query, params).fetchall()
        return [self._row_to_record(row) for row in rows]

    def get_signal_outcome(self, record_id: str) -> dict[str, Any] | None:
        """Return the authoritative Passport outcome state for one signal."""
        with self.database.connection() as connection:
            row = connection.execute(
                """
                SELECT record_id, symbol, timeframe, signal_status,
                       final_outcome, exit_reason, invalidation_reason,
                       outcome_at
                FROM journal_entries
                WHERE record_id = %s
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

    def record_execution(self, record: ExecutionRecord) -> None:
        """Persist one sanitized execution attempt exactly once."""
        request_payload = (
            {
                "action": record.mt5_request.action,
                "symbol": record.mt5_request.symbol,
                "volume": record.mt5_request.volume,
                "order_type": record.mt5_request.order_type,
                "price": record.mt5_request.price,
                "sl": record.mt5_request.sl,
                "tp": record.mt5_request.tp,
                "deviation": record.mt5_request.deviation,
                "type_filling": record.mt5_request.type_filling,
                "magic": record.mt5_request.magic,
                "comment": record.mt5_request.comment,
            }
            if record.mt5_request is not None
            else None
        )
        status = getattr(record.status, "value", str(record.status))
        direction = getattr(record.direction, "value", record.direction)
        inserted = False
        with self.database.connection() as connection:
            cursor = connection.execute(
                """
                INSERT INTO execution_records (
                    execution_record_id, candidate_id, profile, symbol, timeframe,
                    direction, lot_size, risk_evaluation_id, mt5_request,
                    preflight_result, order_send_retcode, mt5_order_id,
                    mt5_deal_id, broker_comment, execution_status, error_reason,
                    recovery_suggestion, attempted_at, completed_at
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s, %s, %s, %s
                )
                ON CONFLICT (execution_record_id) DO NOTHING
                """,
                (
                    str(record.record_id),
                    str(record.candidate_id),
                    str(record.profile),
                    str(record.symbol),
                    str(record.timeframe),
                    str(direction),
                    _decimal(record.lot_size),
                    str(record.risk_evaluation_id),
                    _jsonb(request_payload) if request_payload is not None else None,
                    record.preflight_result,
                    record.order_send_retcode,
                    record.mt5_order_id,
                    record.mt5_deal_id,
                    record.broker_comment,
                    str(status),
                    record.error_reason,
                    record.recovery_suggestion,
                    _timestamp(record.attempted_at),
                    _timestamp(record.completed_at),
                ),
            )
            inserted = cursor.rowcount > 0
        if inserted:
            self.record_execution_quality_event(
                ExecutionQualityEvent.from_execution_record(record)
            )

    def record_execution_quality_event(self, event: ExecutionQualityEvent) -> None:
        """Persist one append-only execution-quality fact idempotently."""
        with self.database.connection() as connection:
            connection.execute(
                """
                INSERT INTO execution_quality_events (
                    event_id, event_type, execution_record_id, candidate_id,
                    profile, symbol, timeframe, direction, position_id,
                    requested_price, actual_price, requested_volume, actual_volume,
                    spread, slippage, stop_loss, take_profit, broker_retcode,
                    broker_comment, action, status, latency_ms, gross_pnl, costs,
                    net_pnl, reason, occurred_at, metadata
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s, %s, %s
                )
                ON CONFLICT (event_id) DO NOTHING
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
                    _decimal(event.requested_price),
                    _decimal(event.actual_price),
                    _decimal(event.requested_volume),
                    _decimal(event.actual_volume),
                    _decimal(event.spread),
                    _decimal(event.slippage),
                    _decimal(event.stop_loss),
                    _decimal(event.take_profit),
                    event.broker_retcode,
                    event.broker_comment,
                    event.action,
                    event.status,
                    event.latency_ms,
                    _decimal(event.gross_pnl),
                    _decimal(event.costs),
                    _decimal(event.net_pnl),
                    event.reason[:240],
                    _timestamp(event.occurred_at),
                    _jsonb(event.metadata),
                ),
            )

    def list_execution_quality_events(
        self,
        execution_record_id: Optional[str] = None,
        *,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Return bounded sanitized quality facts for operations analytics."""
        bounded_limit = max(1, min(int(limit), 1000))
        params: list[Any] = []
        where = ""
        if execution_record_id:
            where = "WHERE execution_record_id = %s"
            params.append(str(execution_record_id))
        params.append(bounded_limit)
        with self.database.connection() as connection:
            rows = connection.execute(
                f"""
                SELECT event_id, event_type, execution_record_id, candidate_id,
                       profile, symbol, timeframe, direction, position_id,
                       requested_price, actual_price, requested_volume, actual_volume,
                       spread, slippage, stop_loss, take_profit, broker_retcode,
                       broker_comment, action, status, latency_ms, gross_pnl, costs,
                       net_pnl, reason, occurred_at, metadata
                FROM execution_quality_events
                {where}
                ORDER BY occurred_at DESC, event_id DESC
                LIMIT %s
                """,
                tuple(params),
            ).fetchall()
        names = (
            "event_id", "event_type", "execution_record_id", "candidate_id",
            "profile", "symbol", "timeframe", "direction", "position_id",
            "requested_price", "actual_price", "requested_volume", "actual_volume",
            "spread", "slippage", "stop_loss", "take_profit", "broker_retcode",
            "broker_comment", "action", "status", "latency_ms", "gross_pnl",
            "costs", "net_pnl", "reason", "occurred_at", "metadata",
        )
        result = []
        for row in rows:
            item = dict(zip(names, row))
            if isinstance(item.get("occurred_at"), datetime):
                item["occurred_at"] = item["occurred_at"].isoformat()
            for key in (
                "requested_price", "actual_price", "requested_volume", "actual_volume",
                "spread", "slippage", "stop_loss", "take_profit", "gross_pnl",
                "costs", "net_pnl",
            ):
                if item.get(key) is not None:
                    item[key] = str(item[key])
            result.append(item)
        return result

    def get_execution_record(self, record_id: str) -> Optional[dict[str, Any]]:
        with self.database.connection() as connection:
            row = connection.execute(
                """
                SELECT execution_record_id, candidate_id, profile, symbol, timeframe,
                       direction, lot_size, risk_evaluation_id, mt5_request,
                       preflight_result, order_send_retcode, mt5_order_id,
                       mt5_deal_id, broker_comment, execution_status, error_reason,
                       recovery_suggestion, attempted_at, completed_at,
                       mt5_position_id, realized_status, realized_gross_pnl,
                       realized_costs, realized_net_pnl, realized_deals_count,
                       realized_reason, realized_at
                FROM execution_records
                WHERE execution_record_id = %s
                """,
                (str(record_id),),
            ).fetchone()
        if not row:
            return None
        names = (
            "execution_record_id", "candidate_id", "profile", "symbol", "timeframe",
            "direction", "lot_size", "risk_evaluation_id", "mt5_request",
            "preflight_result", "order_send_retcode", "mt5_order_id", "mt5_deal_id",
            "broker_comment", "execution_status", "error_reason",
            "recovery_suggestion", "attempted_at", "completed_at",
            "mt5_position_id", "realized_status", "realized_gross_pnl",
            "realized_costs", "realized_net_pnl", "realized_deals_count",
            "realized_reason", "realized_at",
        )
        result = dict(zip(names, row))
        if result.get("lot_size") is not None:
            result["lot_size"] = str(result["lot_size"])
        for key in ("attempted_at", "completed_at", "realized_at"):
            if isinstance(result.get(key), datetime):
                result[key] = result[key].isoformat()
        for key in ("realized_gross_pnl", "realized_costs", "realized_net_pnl"):
            if result.get(key) is not None:
                result[key] = str(result[key])
        return result

    def link_execution_position(self, record_id: str, position_id: int) -> bool:
        """Attach the broker position identifier discovered after a fill."""
        with self.database.connection() as connection:
            cursor = connection.execute(
                """
                UPDATE execution_records
                SET mt5_position_id = %s
                WHERE execution_record_id = %s AND mt5_position_id IS NULL
                """,
                (int(position_id), str(record_id)),
            )
            return cursor.rowcount > 0

    def list_unsettled_executions(self) -> list[dict[str, Any]]:
        """Return filled executions awaiting broker-close reconciliation."""
        with self.database.connection() as connection:
            rows = connection.execute(
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
        result = []
        for row in rows:
            item = dict(zip(names, row))
            if isinstance(item.get("attempted_at"), datetime):
                item["attempted_at"] = item["attempted_at"].isoformat()
            result.append(item)
        return result

    def record_execution_realization(
        self,
        record_id: str,
        reconciliation: Any,
        realized_at: Optional[datetime] = None,
    ) -> bool:
        """Persist one broker-close reconciliation without overwriting it."""
        status = str(getattr(reconciliation, "status", "UNKNOWN"))
        status = status if status in {"OK", "UNKNOWN"} else "UNKNOWN"
        occurred_at = _timestamp(realized_at, _now()) or _now()
        updated = False
        with self.database.connection() as connection:
            cursor = connection.execute(
                """
                UPDATE execution_records
                SET realized_status = %s, realized_gross_pnl = %s,
                    realized_costs = %s, realized_net_pnl = %s,
                    realized_deals_count = %s, realized_reason = %s,
                    realized_at = %s
                WHERE execution_record_id = %s AND realized_status = 'PENDING'
                """,
                (
                    status,
                    _decimal(getattr(reconciliation, "gross_pnl", None)),
                    _decimal(getattr(reconciliation, "costs", None)),
                    _decimal(getattr(reconciliation, "net_pnl", None)),
                    int(getattr(reconciliation, "deals_count", 0) or 0),
                    str(getattr(reconciliation, "reason", "") or ""),
                    occurred_at,
                    str(record_id),
                ),
            )
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
                        str(getattr(reconciliation, "gross_pnl"))
                        if getattr(reconciliation, "gross_pnl", None) is not None
                        else None
                    ),
                    costs=(
                        str(getattr(reconciliation, "costs"))
                        if getattr(reconciliation, "costs", None) is not None
                        else None
                    ),
                    net_pnl=(
                        str(getattr(reconciliation, "net_pnl"))
                        if getattr(reconciliation, "net_pnl", None) is not None
                        else None
                    ),
                    reason=str(getattr(reconciliation, "reason", "") or "")[:240],
                    occurred_at=occurred_at,
                )
            )
        return updated

    def set_status(self, candidate_id: str, status: str) -> bool:
        return self.compare_and_set_status(candidate_id, "PENDING", status)

    def compare_and_set_status(
        self,
        candidate_id: str,
        expected_status: str,
        new_status: str,
    ) -> bool:
        """Atomically transition one approval row from an expected state."""
        with self.database.connection() as connection:
            cursor = connection.execute(
                """
                UPDATE approval_queue
                SET status = %s, updated_at = %s
                WHERE candidate_id = %s AND status = %s
                """,
                (str(new_status), _now(), str(candidate_id), str(expected_status)),
            )
            return cursor.rowcount > 0

    def mark_execution_ready(self, candidate_id: str) -> bool:
        """Release a delayed request after its market-entry condition fires."""
        with self.database.connection() as connection:
            cursor = connection.execute(
                """
                UPDATE approval_queue
                SET request = request || %s, updated_at = %s
                WHERE candidate_id = %s AND status IN ('PENDING', 'APPROVED')
                """,
                (_jsonb({"execution_ready": True}), _now(), str(candidate_id)),
            )
            return cursor.rowcount > 0

    @staticmethod
    def _row_to_record(row: Any) -> dict[str, Any]:
        result = {
            "candidate_id": row[0],
            "status": row[1],
            "profile": row[2],
            "expires_at": row[3].isoformat() if isinstance(row[3], datetime) else row[3],
            "request": _record(row[4]),
            "created_at": row[5].isoformat() if isinstance(row[5], datetime) else row[5],
            "updated_at": row[6].isoformat() if isinstance(row[6], datetime) else row[6],
        }
        return result


class PostgresAuditRepository(_Repository):
    """Append-only audit event repository with deterministic deduplication."""

    def record(self, event: dict[str, Any]) -> str:
        # Match OperationalAuditLog's stable JSONL serialization so imported
        # events retain the same deduplication hash.
        payload = json.dumps(event, sort_keys=True)
        event_hash = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        with self.database.connection() as connection:
            connection.execute(
                """
                INSERT INTO audit_events (
                    event_hash, timestamp, actor_chat_id, action,
                    target_chat_id, result, metadata
                ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (event_hash) DO NOTHING
                """,
                (
                    event_hash,
                    _timestamp(event.get("timestamp"), _now()) or _now(),
                    str(event.get("actor_chat_id", "")),
                    str(event.get("action", "")),
                    str(event.get("target_chat_id", "")),
                    str(event.get("result", "success")),
                    _jsonb(event.get("metadata") or {}),
                ),
            )
        return event_hash

    def list(self, limit: int = 20) -> list[dict[str, Any]]:
        bounded = max(1, min(int(limit), 100))
        with self.database.connection() as connection:
            rows = connection.execute(
                """
                SELECT event_hash, timestamp, actor_chat_id, action,
                       target_chat_id, result, metadata
                FROM audit_events ORDER BY timestamp DESC LIMIT %s
                """,
                (bounded,),
            ).fetchall()
        return [
            {
                "event_hash": row[0],
                "timestamp": row[1].isoformat() if isinstance(row[1], datetime) else row[1],
                "actor_chat_id": row[2],
                "action": row[3],
                "target_chat_id": row[4],
                "result": row[5],
                "metadata": _record(row[6]),
            }
            for row in rows
        ]

    def list_all(self) -> list[dict[str, Any]]:
        """Return all audit rows for a controlled migration comparison."""
        with self.database.connection() as connection:
            rows = connection.execute(
                """
                SELECT event_hash, timestamp, actor_chat_id, action,
                       target_chat_id, result, metadata
                FROM audit_events ORDER BY timestamp DESC
                """
            ).fetchall()
        return [
            {
                "event_hash": row[0],
                "timestamp": row[1].isoformat() if isinstance(row[1], datetime) else row[1],
                "actor_chat_id": row[2],
                "action": row[3],
                "target_chat_id": row[4],
                "result": row[5],
                "metadata": _record(row[6]),
            }
            for row in rows
        ]


class PostgresRuntimeRepository(_Repository):
    """Small JSONB state records used by scanner services."""

    def get(self, state_key: str) -> Any:
        with self.database.connection() as connection:
            row = connection.execute(
                "SELECT state_value FROM runtime_state WHERE state_key = %s",
                (str(state_key),),
            ).fetchone()
        return row[0] if row else None

    def set(self, state_key: str, value: Any) -> None:
        with self.database.connection() as connection:
            connection.execute(
                """
                INSERT INTO runtime_state (state_key, state_value, updated_at)
                VALUES (%s, %s, %s)
                ON CONFLICT (state_key) DO UPDATE SET
                    state_value = EXCLUDED.state_value,
                    updated_at = EXCLUDED.updated_at
                """,
                (str(state_key), _jsonb(value), _now()),
            )

    def list(self) -> dict[str, Any]:
        with self.database.connection() as connection:
            rows = connection.execute(
                "SELECT state_key, state_value FROM runtime_state ORDER BY state_key"
            ).fetchall()
        return {str(row[0]): row[1] for row in rows}


class PostgresReplaySnapshotRepository(_Repository):
    """Replay snapshots with immutable formation and append-only outcomes."""

    _TERMINAL_STATUSES = {
        ReplayLifecycleStatus.RESOLVED.value,
        ReplayLifecycleStatus.EXPIRED.value,
    }

    @staticmethod
    def _json(value: Any) -> Any:
        return _jsonb(value)

    @staticmethod
    def _payload(row: Any) -> dict[str, Any]:
        names = (
            "snapshot_id", "signal_id", "snapshot_version", "symbol",
            "source_timeframe", "source", "created_at", "captured_at",
            "formation_cutoff_at", "context_bars_before_parent",
            "outcome_forward_bars", "formation_context",
            "higher_timeframe_context", "outcome_context", "rule_context",
            "snapshot_hash", "availability", "lifecycle_status",
            "final_outcome", "outcome_at", "invalidation_reason",
            "failure_reason",
        )
        result = dict(zip(names, row))
        for key in (
            "formation_context",
            "higher_timeframe_context",
            "outcome_context",
            "rule_context",
        ):
            if result.get(key) is None:
                result[key] = [] if key.endswith("context") and key != "rule_context" else {}
            elif isinstance(result[key], str):
                try:
                    result[key] = json.loads(result[key])
                except json.JSONDecodeError as exc:
                    raise ValueError(f"invalid stored replay JSON: {key}") from exc
        for key in ("created_at", "captured_at", "formation_cutoff_at", "outcome_at"):
            if isinstance(result.get(key), datetime):
                result[key] = result[key].isoformat()
        result["snapshot_hash"] = str(result.get("snapshot_hash", ""))
        return result

    @classmethod
    def _to_snapshot(cls, row: Any) -> ReplaySnapshot | None:
        return ReplaySnapshot.from_payload(cls._payload(row)) if row else None

    @staticmethod
    def _select_sql() -> str:
        return """
            SELECT snapshot_id, signal_id, snapshot_version, symbol,
                   source_timeframe, source, created_at, captured_at,
                   formation_cutoff_at, context_bars_before_parent,
                   outcome_forward_bars, formation_context,
                   higher_timeframe_context, outcome_context, rule_context,
                   snapshot_hash, availability, lifecycle_status,
                   final_outcome, outcome_at, invalidation_reason,
                   failure_reason
            FROM signal_replay_snapshots
        """

    def get(
        self,
        signal_id: str,
        snapshot_version: Optional[str] = None,
    ) -> Optional[ReplaySnapshot]:
        query = self._select_sql() + " WHERE signal_id = %s"
        params: list[str] = [str(signal_id)]
        if snapshot_version is not None:
            query += " AND snapshot_version = %s"
            params.append(str(snapshot_version))
        query += " ORDER BY updated_at DESC LIMIT 1"
        with self.database.connection() as connection:
            row = connection.execute(query, tuple(params)).fetchone()
        return self._to_snapshot(row)

    def list(self) -> list[ReplaySnapshot]:
        with self.database.connection() as connection:
            rows = connection.execute(
                self._select_sql() + " ORDER BY created_at DESC, signal_id"
            ).fetchall()
        return [snapshot for row in rows if (snapshot := self._to_snapshot(row))]

    def upsert(self, snapshot: ReplaySnapshot) -> ReplaySnapshot:
        if not isinstance(snapshot, ReplaySnapshot):
            raise TypeError("upsert requires a ReplaySnapshot")
        payload = snapshot.to_payload()
        with self.database.connection() as connection:
            existing = connection.execute(
                self._select_sql()
                + " WHERE signal_id = %s AND snapshot_version = %s",
                (snapshot.signal_id, snapshot.snapshot_version),
            ).fetchone()
            if existing is not None:
                current = self._to_snapshot(existing)
                if current is None or current.snapshot_hash != snapshot.snapshot_hash:
                    raise ValueError("immutable replay snapshot conflict")
                return current

            connection.execute(
                """
                INSERT INTO signal_replay_snapshots (
                    snapshot_id, signal_id, snapshot_version, symbol,
                    source_timeframe, source, created_at, captured_at,
                    formation_cutoff_at, context_bars_before_parent,
                    outcome_forward_bars, formation_context,
                    higher_timeframe_context, outcome_context, rule_context,
                    snapshot_hash, availability, lifecycle_status,
                    final_outcome, outcome_at, invalidation_reason,
                    failure_reason, updated_at
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                )
                ON CONFLICT (signal_id, snapshot_version) DO NOTHING
                """,
                (
                    payload["snapshot_id"], payload["signal_id"],
                    payload["snapshot_version"], payload["symbol"],
                    payload["source_timeframe"], payload["source"],
                    payload["created_at"], payload["captured_at"],
                    payload["formation_cutoff_at"],
                    payload["context_bars_before_parent"],
                    payload["outcome_forward_bars"],
                    self._json(payload["formation_candles"]),
                    self._json(payload["higher_timeframe_candles"]),
                    self._json(payload["outcome_candles"]),
                    self._json(payload["rule_context"]),
                    payload["snapshot_hash"], payload["availability"],
                    payload["lifecycle_status"], payload["final_outcome"],
                    payload["outcome_at"], payload["invalidation_reason"],
                    payload["failure_reason"], _now(),
                ),
            )
            stored = connection.execute(
                self._select_sql()
                + " WHERE signal_id = %s AND snapshot_version = %s",
                (snapshot.signal_id, snapshot.snapshot_version),
            ).fetchone()
        result = self._to_snapshot(stored)
        if result is None:
            raise ValueError("replay snapshot insert was not persisted")
        if result.snapshot_hash != snapshot.snapshot_hash:
            raise ValueError("immutable replay snapshot conflict")
        return result

    def update_outcome(self, snapshot: ReplaySnapshot) -> ReplaySnapshot:
        if not isinstance(snapshot, ReplaySnapshot):
            raise TypeError("update_outcome requires a ReplaySnapshot")
        current = self.get(snapshot.signal_id, snapshot.snapshot_version)
        if current is None:
            raise KeyError(f"replay snapshot not found: {snapshot.signal_id}")
        if current.snapshot_hash != snapshot.snapshot_hash:
            raise ValueError("formation evidence cannot be changed")
        current_outcomes = [item.to_payload() for item in current.outcome_candles]
        next_outcomes = [item.to_payload() for item in snapshot.outcome_candles]
        if next_outcomes[: len(current_outcomes)] != current_outcomes:
            raise ValueError("outcome candles are append-only")
        if current.lifecycle_status.value in self._TERMINAL_STATUSES:
            if current.to_payload()["outcome_candles"] != snapshot.to_payload()["outcome_candles"]:
                raise ValueError("terminal replay outcome cannot be changed")
            if (
                current.lifecycle_status != snapshot.lifecycle_status
                or current.final_outcome != snapshot.final_outcome
            ):
                raise ValueError("terminal replay outcome cannot be changed")
            return current

        with self.database.connection() as connection:
            connection.execute(
                """
                UPDATE signal_replay_snapshots
                SET outcome_context = %s, lifecycle_status = %s,
                    final_outcome = %s, outcome_at = %s,
                    invalidation_reason = %s, updated_at = %s
                WHERE signal_id = %s AND snapshot_version = %s
                """,
                (
                    self._json(next_outcomes), snapshot.lifecycle_status.value,
                    snapshot.final_outcome.value,
                    snapshot.outcome_at, snapshot.invalidation_reason, _now(),
                    snapshot.signal_id, snapshot.snapshot_version,
                ),
            )
        return snapshot


class PostgresRepositoryBundle:
    """All explicit PostgreSQL repositories used by the migration boundary."""

    def __init__(self, database: PostgresDatabase) -> None:
        self.auth = PostgresAuthRepository(database)
        self.subscribers = PostgresSubscriberRepository(database)
        self.invite_codes = PostgresInviteCodeRepository(database)
        self.payments = PostgresPaymentRepository(database)
        self.journal = PostgresJournalRepository(database)
        self.approvals = PostgresApprovalRepository(database)
        self.audit = PostgresAuditRepository(database)
        self.runtime = PostgresRuntimeRepository(database)
        self.replay = PostgresReplaySnapshotRepository(database)


def build_postgres_repositories(config: StorageConfig) -> PostgresRepositoryBundle:
    """Build explicit adapters without changing the application's backend."""
    if not config.uses_postgres:
        raise ValueError("PostgreSQL repositories require a postgres StorageConfig")
    database = PostgresDatabase(config)
    return PostgresRepositoryBundle(database)
