from datetime import datetime, timezone

import pytest

from core.operations.trading_account_registry import (
    TRADING_ACCOUNT_REGISTRY_KEY,
    add_trading_account,
    delete_trading_account,
    load_trading_account_registry,
    select_trading_account,
)


class MemoryStore:
    def __init__(self):
        self.values = {}

    def get(self, key, default=None):
        return self.values.get(key, default)

    def set(self, key, value):
        self.values[key] = value


def test_add_and_select_saved_account_keeps_only_secret_free_identity_data():
    store = MemoryStore()

    first = add_trading_account(
        store,
        label="Maven 2K",
        login=10422092,
        server="MavenTrade-Server",
        actor="telegram:100",
    )
    second = add_trading_account(
        store,
        label="Demo",
        login=20422092,
        server="MavenTrade-Demo",
        actor="telegram:100",
    )

    assert first.active_account.label == "Maven 2K"
    assert second.active_account.label == "Maven 2K"

    selected = select_trading_account(
        store,
        profile_id=second.accounts[1].profile_id,
        actor="telegram:100",
    )

    assert selected.active_account.label == "Demo"
    serialized = store.get(TRADING_ACCOUNT_REGISTRY_KEY)
    assert serialized["accounts"][1]["login"] == 20422092
    assert "password" not in str(serialized).lower()
    assert load_trading_account_registry(store).active_account.server == "MavenTrade-Demo"


def test_duplicate_identity_and_invalid_active_reference_are_rejected():
    store = MemoryStore()
    add_trading_account(
        store,
        label="Primary",
        login=123456,
        server="Broker-Live",
        actor="telegram:100",
    )

    with pytest.raises(ValueError, match="already saved"):
        add_trading_account(
            store,
            label="Duplicate",
            login=123456,
            server="Broker-Live",
            actor="telegram:100",
        )

    with pytest.raises(ValueError, match="not found"):
        select_trading_account(store, profile_id="missing", actor="telegram:100")


def test_delete_removes_unselected_account_but_protects_active_target():
    store = MemoryStore()
    first = add_trading_account(
        store,
        label="Primary",
        login=123456,
        server="Broker-Live",
        actor="telegram:100",
    )
    second = add_trading_account(
        store,
        label="Old Demo",
        login=654321,
        server="Broker-Demo",
        actor="telegram:100",
    )

    with pytest.raises(ValueError, match="selected account"):
        delete_trading_account(
            store,
            profile_id=first.accounts[0].profile_id,
            actor="telegram:100",
        )

    updated = delete_trading_account(
        store,
        profile_id=second.accounts[1].profile_id,
        actor="telegram:100",
    )
    assert [account.label for account in updated.accounts] == ["Primary"]
    assert updated.active_profile_id == first.accounts[0].profile_id


def test_invalid_persisted_registry_fails_closed_instead_of_dropping_selection():
    store = MemoryStore()
    store.set(
        TRADING_ACCOUNT_REGISTRY_KEY,
        {
            "revision": 1,
            "accounts": [],
            "active_profile_id": "missing",
            "updated_at": datetime.now(timezone.utc).isoformat(),
        },
    )

    with pytest.raises(ValueError, match="active_profile_id"):
        load_trading_account_registry(store)
