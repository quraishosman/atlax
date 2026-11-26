import json
from datetime import datetime, timezone
from decimal import Decimal

from core.settings.prop_firm_profiles import load_prop_firm_profiles


def test_missing_profile_file_is_empty(tmp_path):
    assert load_prop_firm_profiles(tmp_path / "missing.json") == ()


def test_source_backed_profile_file_is_typed(tmp_path):
    path = tmp_path / "profiles.json"
    path.write_text(
        json.dumps(
            {
                "profiles": [
                    {
                        "profile_id": "firm-funded-usd",
                        "firm_name": "Firm",
                        "program_name": "Funded",
                        "phase": "FUNDED",
                        "account_type": "STANDARD",
                        "currency": "USD",
                        "overall_loss": {
                            "mode": "STATIC",
                            "basis": "INITIAL_BALANCE",
                            "percentage": "8",
                        },
                        "daily_loss": None,
                        "source_urls": ["https://firm.example/rules"],
                        "source_retrieved_at": "2026-09-19T10:00:00+00:00",
                        "version": "1.0",
                        "content_hash": "hash",
                        "nominal_balance": "2000",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    profiles = load_prop_firm_profiles(path)

    assert len(profiles) == 1
    assert profiles[0].overall_loss.percentage == Decimal("8")
    assert profiles[0].source_retrieved_at == datetime(2026, 9, 19, 10, tzinfo=timezone.utc)


def test_yaml_profile_file_is_typed(tmp_path):
    path = tmp_path / "profiles.yaml"
    path.write_text(
        """
profiles:
  - profile_id: yaml-firm
    firm_name: YAML Firm
    program_name: Funded
    phase: funded
    account_type: standard
    currency: USD
    overall_loss:
      mode: STATIC
      basis: INITIAL_BALANCE
      percentage: 8
    daily_loss: null
    source_urls:
      - https://firm.example/rules
    source_retrieved_at: 2026-09-19T10:00:00+00:00
    version: 1.0
    content_hash: hash
""",
        encoding="utf-8",
    )

    profiles = load_prop_firm_profiles(path)

    assert len(profiles) == 1
    assert profiles[0].profile_id == "yaml-firm"


def test_default_catalog_prefers_yaml_when_json_is_absent(monkeypatch):
    monkeypatch.delenv("ATLAX_PROP_FIRM_PROFILE_FILE", raising=False)

    profiles = load_prop_firm_profiles()

    assert profiles
    assert profiles[0].profile_id == "ftmo_standard_challenge"


def test_sha256_profile_hash_rejects_tampered_record(tmp_path):
    path = tmp_path / "profiles.json"
    path.write_text(
        json.dumps(
            {
                "profiles": [
                    {
                        "profile_id": "tampered",
                        "firm_name": "Firm",
                        "program_name": "Funded",
                        "phase": "funded",
                        "account_type": "standard",
                        "currency": "USD",
                        "overall_loss": {
                            "mode": "STATIC",
                            "basis": "INITIAL_BALANCE",
                            "percentage": 8,
                        },
                        "daily_loss": None,
                        "source_urls": ["https://firm.example/rules"],
                        "source_retrieved_at": "2026-09-19T10:00:00+00:00",
                        "version": "1.0",
                        "content_hash": "sha256:not-the-record-hash",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    try:
        load_prop_firm_profiles(path)
    except ValueError as exc:
        assert "content_hash" in str(exc)
    else:
        raise AssertionError("tampered profile was accepted")
