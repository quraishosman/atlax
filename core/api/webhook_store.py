"""Durable, idempotent storage for authenticated webhook envelopes.

The store is a transport boundary only.  It never detects patterns, creates a
trade candidate, scores confidence, or executes an order.  A downstream owner
must claim an event and explicitly acknowledge it after processing.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from core.models.webhook import NormalizedWebhookEvent, TradingViewPayload

logger = logging.getLogger("atlax.webhooks.store")


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _model_dict(value: Any) -> dict[str, Any]:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    return value.dict()


@dataclass(frozen=True)
class WebhookQueueItem:
    message_id: str
    correlation_id: str
    fingerprint: str
    status: str
    attempts: int
    normalized: dict[str, Any]


@dataclass(frozen=True)
class EnqueueResult:
    item: WebhookQueueItem
    duplicate: bool


@dataclass(frozen=True)
class WebhookProcessingResult:
    message_id: str
    correlation_id: str
    status: str
    reason: str
    detector_event: dict[str, Any]
    recorded_at: str


class WebhookIntakeStore:
    """SQLite-backed queue used until the shared PostgreSQL cutover."""

    def __init__(
        self,
        path: str | None = None,
        *,
        max_attempts: int | None = None,
        retry_delay_seconds: int | None = None,
    ) -> None:
        self.path = str(
            path
            or os.environ.get("ATLAX_WEBHOOK_STORE_PATH", "data/webhook_intake.sqlite")
        ).strip()
        if not self.path:
            raise ValueError("Webhook intake store path must not be empty")
        self.max_attempts = int(
            max_attempts
            if max_attempts is not None
            else os.environ.get("ATLAX_WEBHOOK_MAX_ATTEMPTS", "5")
        )
        self.retry_delay_seconds = int(
            retry_delay_seconds
            if retry_delay_seconds is not None
            else os.environ.get("ATLAX_WEBHOOK_RETRY_DELAY_SECONDS", "30")
        )
        if self.max_attempts < 1:
            raise ValueError("Webhook max attempts must be >= 1")
        if self.retry_delay_seconds < 0:
            raise ValueError("Webhook retry delay must be >= 0")
        self._init_db()

    @staticmethod
    def fingerprint(payload: TradingViewPayload) -> str:
        """Return a stable replay key for the validated event body."""
        canonical = json.dumps(
            _model_dict(payload),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def _connect(self) -> sqlite3.Connection:
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        return connection

    def _init_db(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS webhook_events (
                    fingerprint TEXT PRIMARY KEY,
                    message_id TEXT NOT NULL UNIQUE,
                    correlation_id TEXT NOT NULL,
                    received_at TEXT NOT NULL,
                    event_time TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    timeframe TEXT NOT NULL,
                    detector TEXT NOT NULL,
                    normalized_event TEXT NOT NULL,
                    status TEXT NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    available_at TEXT NOT NULL,
                    processed_at TEXT,
                    last_error TEXT NOT NULL DEFAULT ''
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_webhook_events_queue "
                "ON webhook_events(status, available_at, received_at)"
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS webhook_processing_results (
                    message_id TEXT PRIMARY KEY,
                    correlation_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    detector_event TEXT NOT NULL,
                    recorded_at TEXT NOT NULL,
                    FOREIGN KEY(message_id) REFERENCES webhook_events(message_id)
                )
                """
            )

    def enqueue(
        self,
        event: NormalizedWebhookEvent,
        fingerprint: str,
    ) -> EnqueueResult:
        """Persist a validated event once and return the canonical queue item."""
        normalized = _model_dict(event)
        payload = normalized["payload"]
        received_at = str(normalized["receivedAt"])
        available_at = received_at
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO webhook_events (
                    fingerprint, message_id, correlation_id, received_at,
                    event_time, symbol, timeframe, detector, normalized_event,
                    status, attempts, available_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'RECEIVED', 0, ?)
                ON CONFLICT(fingerprint) DO NOTHING
                """,
                (
                    str(fingerprint),
                    str(normalized["messageId"]),
                    str(normalized["correlationId"]),
                    received_at,
                    str(payload["eventTime"]),
                    str(payload["symbol"]),
                    str(payload["timeframe"]),
                    str(payload["detector"]),
                    json.dumps(normalized, sort_keys=True, ensure_ascii=True),
                    available_at,
                ),
            )
            row = connection.execute(
                "SELECT * FROM webhook_events WHERE fingerprint = ?",
                (str(fingerprint),),
            ).fetchone()
        if row is None:
            raise RuntimeError("Webhook event was not persisted")
        duplicate = str(row["message_id"]) != str(normalized["messageId"])
        item = self._row_to_item(row)
        logger.info(
            '{"event":"webhook_%s","message_id":"%s",'
            '"correlation_id":"%s","symbol":"%s","timeframe":"%s"}',
            "duplicate" if duplicate else "queued",
            item.message_id,
            item.correlation_id,
            payload["symbol"],
            payload["timeframe"],
        )
        return EnqueueResult(item=item, duplicate=duplicate)

    def claim_pending(self, limit: int = 10) -> list[WebhookQueueItem]:
        """Atomically claim ready events for one downstream consumer."""
        bounded_limit = min(max(int(limit), 1), 100)
        now = _utc_now().isoformat()
        claimed: list[WebhookQueueItem] = []
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            rows = connection.execute(
                """
                SELECT * FROM webhook_events
                WHERE status = 'RECEIVED' AND available_at <= ?
                ORDER BY received_at, message_id
                LIMIT ?
                """,
                (now, bounded_limit),
            ).fetchall()
            for row in rows:
                attempts = int(row["attempts"]) + 1
                connection.execute(
                    """
                    UPDATE webhook_events
                    SET status = 'PROCESSING', attempts = ?
                    WHERE fingerprint = ? AND status = 'RECEIVED'
                    """,
                    (attempts, row["fingerprint"]),
                )
                claimed.append(
                    self._row_to_item(
                        dict(row, status="PROCESSING", attempts=attempts)
                    )
                )
        return claimed

    def mark_processed(self, message_id: str) -> bool:
        """Acknowledge one claimed event exactly once."""
        with self._connect() as connection:
            result = connection.execute(
                """
                UPDATE webhook_events
                SET status = 'PROCESSED', processed_at = ?, last_error = ''
                WHERE message_id = ? AND status = 'PROCESSING'
                """,
                (_utc_now().isoformat(), str(message_id)),
            )
        return result.rowcount == 1

    def mark_failed(self, message_id: str, error: str) -> str:
        """Return a failed event to the queue or move it to a dead letter."""
        safe_error = str(error or "unknown failure")[:500]
        with self._connect() as connection:
            row = connection.execute(
                "SELECT attempts FROM webhook_events WHERE message_id = ?",
                (str(message_id),),
            ).fetchone()
            if row is None:
                return "UNKNOWN"
            attempts = int(row["attempts"])
            if attempts >= self.max_attempts:
                status = "DEAD_LETTER"
                available_at = _utc_now().isoformat()
            else:
                status = "RECEIVED"
                available_at = (
                    _utc_now() + timedelta(seconds=self.retry_delay_seconds)
                ).isoformat()
            connection.execute(
                """
                UPDATE webhook_events
                SET status = ?, available_at = ?, last_error = ?
                WHERE message_id = ? AND status = 'PROCESSING'
                """,
                (status, available_at, safe_error, str(message_id)),
            )
        logger.warning(
            '{"event":"webhook_processing_failed","message_id":"%s",'
            '"status":"%s","error":"%s"}',
            str(message_id), status, safe_error.replace('"', "'")[:160],
        )
        return status

    def record_processing_result(
        self,
        *,
        message_id: str,
        correlation_id: str,
        status: str,
        reason: str,
        detector_event: dict[str, Any],
    ) -> WebhookProcessingResult:
        """Persist one immutable shadow result before queue acknowledgement."""
        normalized_status = str(status).upper().strip()
        if normalized_status not in {"OK", "UNKNOWN"}:
            raise ValueError("Webhook processing status must be OK or UNKNOWN")
        safe_reason = str(reason or "unknown")[:500]
        recorded_at = _utc_now().isoformat()
        serialized_event = json.dumps(
            detector_event,
            sort_keys=True,
            ensure_ascii=True,
            separators=(",", ":"),
        )
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO webhook_processing_results (
                    message_id, correlation_id, status, reason,
                    detector_event, recorded_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(message_id) DO NOTHING
                """,
                (
                    str(message_id),
                    str(correlation_id),
                    normalized_status,
                    safe_reason,
                    serialized_event,
                    recorded_at,
                ),
            )
            row = connection.execute(
                """
                SELECT message_id, correlation_id, status, reason,
                       detector_event, recorded_at
                FROM webhook_processing_results
                WHERE message_id = ?
                """,
                (str(message_id),),
            ).fetchone()
        if row is None:
            raise RuntimeError("Webhook processing result was not persisted")
        return WebhookProcessingResult(
            message_id=str(row["message_id"]),
            correlation_id=str(row["correlation_id"]),
            status=str(row["status"]),
            reason=str(row["reason"]),
            detector_event=json.loads(str(row["detector_event"])),
            recorded_at=str(row["recorded_at"]),
        )

    def get_processing_result(
        self,
        message_id: str,
    ) -> WebhookProcessingResult | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT message_id, correlation_id, status, reason,
                       detector_event, recorded_at
                FROM webhook_processing_results
                WHERE message_id = ?
                """,
                (str(message_id),),
            ).fetchone()
        if row is None:
            return None
        return WebhookProcessingResult(
            message_id=str(row["message_id"]),
            correlation_id=str(row["correlation_id"]),
            status=str(row["status"]),
            reason=str(row["reason"]),
            detector_event=json.loads(str(row["detector_event"])),
            recorded_at=str(row["recorded_at"]),
        )

    def consume_pending(
        self,
        handler: Callable[[dict[str, Any]], None],
        *,
        limit: int = 10,
    ) -> dict[str, int]:
        """Run an explicit downstream handler with at-most-once acknowledgement."""
        summary = {"claimed": 0, "processed": 0, "retryable": 0, "dead_letter": 0}
        for item in self.claim_pending(limit=limit):
            summary["claimed"] += 1
            try:
                handler(item.normalized)
            except Exception as exc:  # noqa: BLE001
                status = self.mark_failed(item.message_id, str(exc))
                summary["dead_letter" if status == "DEAD_LETTER" else "retryable"] += 1
                continue
            if self.mark_processed(item.message_id):
                summary["processed"] += 1
        return summary

    @staticmethod
    def _row_to_item(row: sqlite3.Row | dict[str, Any]) -> WebhookQueueItem:
        normalized = json.loads(str(row["normalized_event"]))
        return WebhookQueueItem(
            message_id=str(row["message_id"]),
            correlation_id=str(row["correlation_id"]),
            fingerprint=str(row["fingerprint"]),
            status=str(row["status"]),
            attempts=int(row["attempts"]),
            normalized=normalized,
        )
