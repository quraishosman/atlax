"""Append-only operational audit records for ATLAX administrative actions."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("atlax.audit")


class OperationalAuditLog:
    """Persist redacted operational events as one JSON object per line."""

    _SENSITIVE_KEYS = {
        "token",
        "secret",
        "password",
        "wallet",
        "wallet_address",
        "webhook",
        "webhook_secret",
        "jwt",
        "api_key",
    }

    def __init__(self, path: str = "data/admin_audit.jsonl") -> None:
        self._path = Path(path)

    def _sanitize(self, value: Any, key: str = "") -> Any:
        normalized_key = key.lower()
        if any(marker in normalized_key for marker in self._SENSITIVE_KEYS):
            return "[REDACTED]"
        if isinstance(value, dict):
            return {str(k): self._sanitize(v, str(k)) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [self._sanitize(item) for item in value]
        if isinstance(value, (str, int, float, bool)) or value is None:
            return value
        return str(value)

    def record(
        self,
        actor_chat_id: str,
        action: str,
        *,
        target_chat_id: str = "",
        result: str = "success",
        metadata: Optional[dict[str, Any]] = None,
    ) -> None:
        entry = {
            "event": "operational_audit",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "actor_chat_id": str(actor_chat_id),
            "action": str(action),
            "target_chat_id": str(target_chat_id) if target_chat_id else "",
            "result": str(result),
            "metadata": self._sanitize(metadata or {}),
        }
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with self._path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(entry, sort_keys=True) + "\n")
        except Exception as exc:  # noqa: BLE001
            logger.error(
                '{"event":"audit_write_failed","action":"%s","error":"%s"}',
                str(action)[:80],
                str(exc)[:160],
            )
        logger.info(json.dumps(entry, sort_keys=True))

    def recent(self, limit: int = 20) -> list[dict[str, Any]]:
        try:
            lines = self._path.read_text(encoding="utf-8").splitlines()
        except FileNotFoundError:
            return []
        except Exception as exc:  # noqa: BLE001
            logger.error('{"event":"audit_read_failed","error":"%s"}', str(exc)[:160])
            return []

        results = []
        for line in lines[-max(1, min(int(limit), 100)):]:
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(item, dict):
                results.append(item)
        return list(reversed(results))
