from core.risk.prop_firm_controls import (
    PropFirmControlState,
    load_control_state,
    select_profile,
    set_enabled,
    set_paused,
)


class MemoryStore:
    def __init__(self):
        self.values = {}

    def get(self, key, default=None):
        return self.values.get(key, default)

    def set(self, key, value):
        self.values[key] = value


def test_control_state_defaults_disabled_and_survives_restart():
    store = MemoryStore()

    assert load_control_state(store) == PropFirmControlState()
    selected = select_profile(
        store,
        profile_id="example-funded-usd",
        profile_version="2026-09-19.1",
        actor="999",
    )
    enabled = set_enabled(
        store,
        enabled=True,
        actor="999",
        profile_available=True,
    )
    paused = set_paused(store, paused=True, actor="999")

    restored = load_control_state(store)
    assert selected.selected_profile_id == "example-funded-usd"
    assert enabled.enabled is True
    assert paused.paused is True
    assert restored == paused


def test_enable_requires_an_allow_listed_profile():
    store = MemoryStore()

    try:
        set_enabled(
            store,
            enabled=True,
            actor="999",
            profile_available=False,
        )
    except ValueError as exc:
        assert "verified selected profile" in str(exc)
    else:
        raise AssertionError("Prop-Firm Mode enabled without a verified profile")


def test_disabling_clears_pause_but_keeps_selected_profile():
    store = MemoryStore()
    select_profile(
        store,
        profile_id="example-funded-usd",
        profile_version="2026-09-19.1",
        actor="999",
    )
    set_enabled(store, enabled=True, actor="999", profile_available=True)
    set_paused(store, paused=True, actor="999")

    state = set_enabled(
        store,
        enabled=False,
        actor="999",
        profile_available=True,
    )

    assert state.enabled is False
    assert state.paused is False
    assert state.selected_profile_id == "example-funded-usd"
