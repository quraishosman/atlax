"""PostgreSQL connectivity and schema bootstrap for ATLAX.

This module owns connection setup and schema initialization only. Business
modules must use repositories and must not expose raw SQL to AI tools.
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from core.settings.storage_config import StorageConfig, load_storage_config

logger = logging.getLogger("atlax.storage.postgres")


class PostgresUnavailable(RuntimeError):
    """Raised when PostgreSQL storage is selected but cannot be used."""


def _load_psycopg():
    try:
        import psycopg
        from psycopg.conninfo import conninfo_to_dict
    except ImportError as exc:
        raise PostgresUnavailable(
            "PostgreSQL storage requires psycopg[binary]. "
            "Install it before selecting ATLAX_STORAGE_BACKEND=postgres."
        ) from exc
    return psycopg, conninfo_to_dict


class PostgresDatabase:
    """Small transaction-safe PostgreSQL connection boundary."""

    def __init__(self, config: StorageConfig) -> None:
        if not config.uses_postgres:
            raise ValueError("PostgresDatabase requires a postgres StorageConfig")
        self.config = config

    def _connection_kwargs(self) -> dict[str, Any]:
        _, conninfo_to_dict = _load_psycopg()
        params = conninfo_to_dict(self.config.database_url)
        params.setdefault("sslmode", self.config.sslmode)
        params["connect_timeout"] = self.config.connect_timeout_seconds
        return params

    @contextmanager
    def connection(self) -> Iterator[Any]:
        psycopg, _ = _load_psycopg()
        connection = None
        try:
            connection = psycopg.connect(**self._connection_kwargs())
            yield connection
            connection.commit()
        except Exception:
            if connection is not None:
                connection.rollback()
            raise
        finally:
            if connection is not None:
                connection.close()

    def initialize_schema(self) -> None:
        schema_path = (
            Path(__file__).resolve().parents[2]
            / "db"
            / "postgres"
            / "schema.sql"
        )
        if not schema_path.exists():
            raise PostgresUnavailable(f"ATLAX PostgreSQL schema is missing: {schema_path}")

        statements = [
            statement.strip()
            for statement in schema_path.read_text(encoding="utf-8").split(";")
            if statement.strip()
        ]
        with self.connection() as connection:
            for statement in statements:
                connection.execute(statement)
        logger.info('{"event":"postgres_schema_ready"}')

    def healthcheck(self) -> bool:
        try:
            with self.connection() as connection:
                connection.execute("SELECT 1").fetchone()
            return True
        except Exception as exc:  # noqa: BLE001
            logger.error(
                '{"event":"postgres_healthcheck_failed","error":"%s"}',
                str(exc)[:200],
            )
            return False


def initialize_storage(*, require_cutover: bool = True) -> PostgresDatabase | None:
    """Initialize the selected shared backend; local mode is a no-op.

    Schema/bootstrap commands may opt out of the application cutover gate.
    Runtime services must keep the gate enabled so a partial deployment cannot
    silently mix PostgreSQL and local stores.
    """
    config = load_storage_config()
    if not config.uses_postgres:
        return None
    if require_cutover and not config.cutover_enabled:
        raise PostgresUnavailable(
            "PostgreSQL is configured but application cutover is disabled. "
            "Set ATLAX_STORAGE_CUTOVER_ENABLED=true only after migration verification."
        )
    database = PostgresDatabase(config)
    database.initialize_schema()
    return database
