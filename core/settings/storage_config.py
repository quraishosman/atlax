"""Storage backend configuration for ATLAX."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping

_VALID_BACKENDS = {"local", "postgres"}
_VALID_SSLMODES = {"disable", "allow", "prefer", "require", "verify-ca", "verify-full"}
_TRUE_VALUES = {"1", "true", "yes", "on"}
_FALSE_VALUES = {"0", "false", "no", "off"}


def _parse_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    normalized = str(value).strip().lower()
    if normalized in _TRUE_VALUES:
        return True
    if normalized in _FALSE_VALUES:
        return False
    raise ValueError("ATLAX_STORAGE_CUTOVER_ENABLED must be a boolean")


@dataclass(frozen=True)
class StorageConfig:
    """Validated configuration for the shared application data store."""

    backend: str = "local"
    database_url: str = ""
    sslmode: str = "require"
    connect_timeout_seconds: int = 10
    cutover_enabled: bool = False

    def __post_init__(self) -> None:
        backend = str(self.backend).strip().lower()
        if backend not in _VALID_BACKENDS:
            raise ValueError(
                f"ATLAX_STORAGE_BACKEND must be one of {sorted(_VALID_BACKENDS)}"
            )
        object.__setattr__(self, "backend", backend)

        sslmode = str(self.sslmode).strip().lower()
        if sslmode not in _VALID_SSLMODES:
            raise ValueError(
                f"ATLAX_DATABASE_SSLMODE must be one of {sorted(_VALID_SSLMODES)}"
            )
        object.__setattr__(self, "sslmode", sslmode)

        timeout = int(self.connect_timeout_seconds)
        if timeout <= 0:
            raise ValueError("ATLAX_DATABASE_CONNECT_TIMEOUT_SECONDS must be positive")
        object.__setattr__(self, "connect_timeout_seconds", timeout)

        database_url = str(self.database_url or "").strip()
        if backend == "postgres" and not database_url:
            raise ValueError(
                "ATLAX_DATABASE_URL is required when ATLAX_STORAGE_BACKEND=postgres"
            )
        object.__setattr__(self, "database_url", database_url)
        object.__setattr__(self, "cutover_enabled", _parse_bool(self.cutover_enabled))

    @property
    def uses_postgres(self) -> bool:
        return self.backend == "postgres"


def load_storage_config(environ: Mapping[str, str] | None = None) -> StorageConfig:
    """Load storage settings without logging the database URL."""
    values = environ or os.environ
    return StorageConfig(
        backend=values.get("ATLAX_STORAGE_BACKEND", "local"),
        database_url=values.get("ATLAX_DATABASE_URL", ""),
        sslmode=values.get("ATLAX_DATABASE_SSLMODE", "require"),
        connect_timeout_seconds=int(
            values.get("ATLAX_DATABASE_CONNECT_TIMEOUT_SECONDS", "10")
        ),
        cutover_enabled=values.get("ATLAX_STORAGE_CUTOVER_ENABLED", "false"),
    )
