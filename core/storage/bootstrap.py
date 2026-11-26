"""Command-line PostgreSQL schema bootstrap for ATLAX."""

from __future__ import annotations

from core.storage.postgres import initialize_storage


def main() -> None:
    database = initialize_storage(require_cutover=False)
    if database is None:
        raise SystemExit(
            "ATLAX_STORAGE_BACKEND is local. Set it to postgres before bootstrapping."
        )
    if not database.healthcheck():
        raise SystemExit("PostgreSQL healthcheck failed.")
    print("ATLAX PostgreSQL schema is ready.")


if __name__ == "__main__":
    main()
