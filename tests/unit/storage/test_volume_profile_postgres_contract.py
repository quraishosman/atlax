"""Ensure the PostgreSQL Passport insert stays aligned with its parameters."""

from decimal import Decimal

from core.storage.repositories import PostgresJournalRepository


class _Connection:
    def __init__(self):
        self.query = ""
        self.params = ()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

    def execute(self, query, params=()):
        self.query = query
        self.params = tuple(params)


class _Database:
    def __init__(self):
        self.connection_instance = _Connection()

    def connection(self):
        return self.connection_instance


def test_postgres_journal_volume_profile_insert_has_matching_placeholders(monkeypatch):
    # psycopg is intentionally optional in the local test environment; the
    # contract under test is the SQL/parameter alignment, not JSON adaptation.
    monkeypatch.setattr("core.storage.repositories._jsonb", lambda value: value)
    database = _Database()
    repository = PostgresJournalRepository(database)

    repository.upsert(
        {
            "record_id": "signal-1",
            "candidate_id": "candidate-1",
            "symbol": "EURUSD",
            "timeframe": "H1",
            "profile": "day_trader",
            "direction": "BUY",
            "confidence_score": 70,
            "entry_price": Decimal("1.1000"),
            "sl_price": Decimal("1.0980"),
            "tp_price": Decimal("1.1040"),
            "volume_profile_status": "VALID",
            "volume_profile_period": "PREVIOUS_DAY",
        }
    )

    insert_section = database.connection_instance.query.split("ON CONFLICT", 1)[0]
    assert insert_section.count("%s") == len(database.connection_instance.params)
