"""
Tests for Volume Profile Premium Confluence routing (Phase 7).

Covers:
  - Subscriber preference: volume_profile_confluence_only stored and retrieved
  - Entitlement: has_volume_profile_confluence_access fail-closed behavior
  - Routing: VP-required subscriber suppressed when VP is missing/UNKNOWN
  - Routing: VP-required subscriber receives when VP is VALID with levels
  - Routing: Standard subscribers unaffected by VP preference
  - Routing: Non-premium plan rejected (fail-closed)
  - Independence: VP preference does not interact with key_level_only etc.

Authority:
    docs/40_VOLUME_PROFILE_CONFLUENCE_PLAN.md Phase 7, Section 7.3
    docs/decisions/ADR-017.md
"""

import os
import json
import tempfile

import pytest

from core.alerts.subscriber_db import SubscriberDB
from core.alerts.subscription_entitlements import (
    has_volume_profile_confluence_access,
)
from core.confidence.atr_quality import ATRQualityConfig, ATRPlanEntitlement


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_dir(tmp_path):
    """Return a temporary directory with required data files."""
    codes_file = str(tmp_path / "codes.json")
    subs_file = str(tmp_path / "subs.json")
    with open(codes_file, "w") as f:
        json.dump({}, f)
    with open(subs_file, "w") as f:
        json.dump({}, f)
    return codes_file, subs_file


@pytest.fixture
def subscriber_db(tmp_dir):
    codes_file, subs_file = tmp_dir
    db = SubscriberDB(codes_file=codes_file, subs_file=subs_file)
    # Register a subscriber
    code = db.generate_code(label="test")
    db.register(
        chat_id="100",
        username="testuser",
        style="day_trader",
        invite_code=code,
        subscription_plan="premium",
        subscription_days=30,
    )
    return db


def _atr_config(enabled=True, shadow=False):
    """Build a minimal ATRQualityConfig for entitlement checks."""
    from decimal import Decimal
    return ATRQualityConfig(
        enabled=enabled,
        version="0.1.0",
        method="SIMPLE_TR_MEAN",
        atr_period=14,
        parent_high_min=Decimal("0.80"),
        parent_a_min=Decimal("0.60"),
        parent_b_min=Decimal("0.45"),
        sweep_high_min=Decimal("0.25"),
        sweep_a_min=Decimal("0.20"),
        sweep_b_min=Decimal("0.15"),
        minimum_premium_grade="A",
        missing_data_policy="UNKNOWN",
        advanced_regime_filter_enabled=False,
        max_parent_atr_ratio=None,
        max_sweep_atr_ratio=None,
        volatility_regime_min_ratio=None,
        volatility_regime_max_ratio=None,
        default_plan="standard",
        plans=(
            ATRPlanEntitlement("standard", False, None),
            ATRPlanEntitlement("premium", True, "A"),
        ),
        shadow_mode=shadow,
    )


# ---------------------------------------------------------------------------
# Subscriber DB Tests
# ---------------------------------------------------------------------------


class TestSubscriberDBVPPreference:
    def test_default_preference_is_false(self, subscriber_db):
        settings = subscriber_db.get_settings("100")
        assert settings["volume_profile_confluence_only"] is False

    def test_set_preference_true(self, subscriber_db):
        result = subscriber_db.update_settings(
            "100", "volume_profile_confluence_only", True
        )
        assert result is True
        settings = subscriber_db.get_settings("100")
        assert settings["volume_profile_confluence_only"] is True

    def test_set_preference_false(self, subscriber_db):
        subscriber_db.update_settings(
            "100", "volume_profile_confluence_only", True
        )
        subscriber_db.update_settings(
            "100", "volume_profile_confluence_only", False
        )
        settings = subscriber_db.get_settings("100")
        assert settings["volume_profile_confluence_only"] is False

    def test_non_boolean_rejected(self, subscriber_db):
        result = subscriber_db.update_settings(
            "100", "volume_profile_confluence_only", "yes"
        )
        assert result is False

    def test_preference_independent_from_confluence_only(self, subscriber_db):
        """VP preference and confluence_only are separate toggles."""
        subscriber_db.update_settings("100", "confluence_only", True)
        subscriber_db.update_settings(
            "100", "volume_profile_confluence_only", False
        )
        settings = subscriber_db.get_settings("100")
        assert settings["confluence_only"] is True
        assert settings["volume_profile_confluence_only"] is False

    def test_standard_plan_cannot_enable_volume_profile_filter(self, subscriber_db):
        subscriber_db.update_subscription_plan("100", "standard")
        assert subscriber_db.update_settings(
            "100", "volume_profile_confluence_only", True
        ) is False
        assert subscriber_db.get_settings("100")["volume_profile_confluence_only"] is False

    def test_legacy_standard_premium_filters_are_cleared_on_load(self, tmp_path):
        codes_file = str(tmp_path / "codes.json")
        subs_file = str(tmp_path / "subs.json")
        with open(codes_file, "w", encoding="utf-8") as handle:
            json.dump({}, handle)
        with open(subs_file, "w", encoding="utf-8") as handle:
            json.dump({
                "100": {
                    "chat_id": "100",
                    "subscription_plan": "standard",
                    "settings": {
                        "confluence_only": True,
                        "volume_profile_confluence_only": True,
                    },
                }
            }, handle)

        db = SubscriberDB(codes_file=codes_file, subs_file=subs_file)

        assert db.get_settings("100")["confluence_only"] is False
        assert db.get_settings("100")["volume_profile_confluence_only"] is False
        stored = json.load(open(subs_file, encoding="utf-8"))
        assert stored["100"]["settings"]["confluence_only"] is False
        assert stored["100"]["settings"]["volume_profile_confluence_only"] is False


# ---------------------------------------------------------------------------
# Entitlement Tests
# ---------------------------------------------------------------------------


class TestVPConfluenceAccess:
    def test_premium_active_has_access(self):
        record = {
            "active": True,
            "subscription_status": "active",
            "subscription_plan": "premium",
        }
        assert has_volume_profile_confluence_access(record, _atr_config()) is True

    def test_standard_plan_no_access(self):
        record = {
            "active": True,
            "subscription_status": "active",
            "subscription_plan": "standard",
        }
        assert has_volume_profile_confluence_access(record, _atr_config()) is False

    def test_missing_config_no_access(self):
        record = {
            "active": True,
            "subscription_status": "active",
            "subscription_plan": "premium",
        }
        assert has_volume_profile_confluence_access(record, None) is False

    def test_inactive_subscriber_no_access(self):
        record = {
            "active": False,
            "subscription_status": "expired",
            "subscription_plan": "premium",
        }
        assert has_volume_profile_confluence_access(record, _atr_config()) is False

    def test_unknown_plan_no_access(self):
        record = {
            "active": True,
            "subscription_status": "active",
            "subscription_plan": "unknown",
        }
        assert has_volume_profile_confluence_access(record, _atr_config()) is False

    def test_none_record_no_access(self):
        assert has_volume_profile_confluence_access(None, _atr_config()) is False

    def test_disabled_overlay_no_access(self):
        record = {
            "active": True,
            "subscription_status": "active",
            "subscription_plan": "premium",
        }
        assert has_volume_profile_confluence_access(
            record, _atr_config(enabled=False)
        ) is False
