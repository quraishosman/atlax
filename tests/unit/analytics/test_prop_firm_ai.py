from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

from core.analytics import ai_advisory
from core.analytics.mcp_server import (
    get_prop_firm_risk_budget,
    get_prop_firm_rule_profile,
)
from core.models.prop_firm import (
    AccountReferenceBasis,
    LossLimitMode,
    LossLimitSpec,
    PropFirmRuleProfile,
)


def _profile() -> PropFirmRuleProfile:
    return PropFirmRuleProfile(
        profile_id="example-funded-usd",
        firm_name="Example Firm",
        program_name="Example Program",
        phase="FUNDED",
        account_type="STANDARD",
        currency="USD",
        overall_loss=LossLimitSpec(
            mode=LossLimitMode.STATIC,
            basis=AccountReferenceBasis.INITIAL_BALANCE,
            percentage=Decimal("8"),
        ),
        daily_loss=LossLimitSpec(
            mode=LossLimitMode.END_OF_DAY_TRAILING,
            basis=AccountReferenceBasis.OPENING_EQUITY,
            percentage=Decimal("5"),
            reset_timezone="UTC",
        ),
        source_urls=("https://example.test/rules",),
        source_retrieved_at=datetime(2026, 9, 19, tzinfo=timezone.utc),
        version="2026-09-19.1",
        content_hash="sha256:example",
        nominal_balance=Decimal("2000"),
        minimum_trading_days=5,
    )


def test_prop_firm_rule_profile_tool_returns_verified_public_fields_only():
    result = get_prop_firm_rule_profile(_profile())

    assert result["status"] == "OK"
    public = result["profile"]
    assert public["firm_name"] == "Example Firm"
    assert public["source_urls"] == ["https://example.test/rules"]
    assert public["overall_loss"]["percentage"] == "8"
    assert "password" not in str(result).lower()
    assert "api_key" not in str(result).lower()


def test_prop_firm_rule_profile_tool_fails_closed_for_unverified_mapping():
    result = get_prop_firm_rule_profile({
        "profile_id": "unverified",
        "version": "1",
        "firm_name": "Unknown",
        "program_name": "Unknown",
        "phase": "FUNDED",
        "account_type": "STANDARD",
        "currency": "USD",
    })

    assert result["status"] == "UNKNOWN"
    assert "source-verified" in result["reason"]


def test_prop_firm_budget_tool_whitelists_runtime_snapshot_fields():
    state = SimpleNamespace(
        enabled=True,
        paused=False,
        selected_profile_id="example-funded-usd",
        selected_profile_version="2026-09-19.1",
    )
    result = get_prop_firm_risk_budget(
        state,
        snapshot={
            "status": "VALID",
            "blocked": False,
            "reason": "within_budget",
            "remaining_overall_buffer": Decimal("160"),
            "remaining_daily_buffer": Decimal("100"),
            "recovery_mode": "NORMAL",
            "risk_multiplier": Decimal("1"),
            "evaluated_at": datetime(2026, 9, 19, tzinfo=timezone.utc),
            "balance": Decimal("2000"),
            "account_password": "must-not-leave",
        },
    )

    assert result["status"] == "OK"
    assert result["budget"]["remaining_overall_buffer"] == "160"
    assert "balance" not in result["budget"]
    assert "account_password" not in str(result)


def test_admin_ai_loads_prop_firm_context_only_for_related_questions(monkeypatch):
    prompts = []
    config = SimpleNamespace(
        enabled=True,
        ai_advisory_enabled=True,
        db_path=":memory:",
        ai_model="test-model",
        ai_fallback_model="",
    )
    monkeypatch.setattr(ai_advisory, "get_daily_performance", lambda path: {"status": "OK"})
    monkeypatch.setattr(
        ai_advisory,
        "get_historical_performance",
        lambda path, days: {"status": "OK", "days": days},
    )
    monkeypatch.setattr(
        ai_advisory,
        "get_all_trades",
        lambda path, days, limit: {"status": "OK", "items": []},
    )
    monkeypatch.setattr(ai_advisory, "_build_client", lambda config: object())
    monkeypatch.setattr(ai_advisory, "_build_fallback_client", lambda config: None)
    monkeypatch.setattr(
        ai_advisory,
        "_call_ai",
        lambda client, fallback, model, fallback_model, prompt: prompts.append(prompt) or "<b>Answer</b>",
    )
    provider_calls = []
    engine = ai_advisory.AdvisoryEngine(
        config,
        prop_firm_context_provider=lambda: provider_calls.append(True) or {
            "status": "OK",
            "rule_profile": {
                "source_urls": ["https://example.test/rules"],
                "source_retrieved_at": "2026-09-19T00:00:00+00:00",
            },
            "risk_budget": {"status": "OK", "budget": {"remaining_overall_buffer": "160"}},
        },
    )

    engine.answer_question("What is my Prop-Firm daily loss rule?")
    assert provider_calls == [True]
    assert "PROP-FIRM CONTEXT" in prompts[-1]
    assert "https://example.test/rules" in prompts[-1]

    engine.answer_question("What is my win rate?")
    assert provider_calls == [True]
    assert "PROP-FIRM CONTEXT" not in prompts[-1]


def test_admin_prop_firm_context_is_rate_limited_before_provider_call(monkeypatch):
    provider_calls = []
    config = SimpleNamespace(
        enabled=True,
        ai_advisory_enabled=True,
        ai_prop_firm_daily_limit=1,
        ai_prop_firm_cooldown_seconds=0,
        db_path=":memory:",
        ai_model="test-model",
        ai_fallback_model="",
    )
    monkeypatch.setattr(ai_advisory, "get_daily_performance", lambda path: {"status": "OK"})
    monkeypatch.setattr(
        ai_advisory,
        "get_historical_performance",
        lambda path, days: {"status": "OK"},
    )
    monkeypatch.setattr(
        ai_advisory,
        "get_all_trades",
        lambda path, days, limit: {"status": "OK", "items": []},
    )
    monkeypatch.setattr(ai_advisory, "_build_client", lambda config: object())
    monkeypatch.setattr(ai_advisory, "_build_fallback_client", lambda config: None)
    monkeypatch.setattr(
        ai_advisory,
        "_call_ai",
        lambda *args: "<b>Answer</b>",
    )
    engine = ai_advisory.AdvisoryEngine(
        config,
        prop_firm_context_provider=lambda: provider_calls.append(True) or {"status": "OK"},
    )

    engine.answer_question("Explain the Prop-Firm daily loss rule")
    limited = engine.answer_question("Explain the Prop-Firm recovery state")

    assert provider_calls == [True]
    assert "rate-limited" in limited.lower()
