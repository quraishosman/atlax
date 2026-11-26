import pytest

from core.settings.fvg_config import load_fvg_config


def test_fvg_settings_load_from_project_config():
    config = load_fvg_config("config/atlax.yaml")

    assert config.enabled is True
    assert config.execution_gate_enabled is False
    assert config.min_gap_pips == 0.5
    assert config.ltf_candle_count == 30
    assert config.ltf_atr_period == 14
    assert config.watch_max_age_seconds == 14400


def test_missing_fvg_section_fails_closed(tmp_path):
    path = tmp_path / "atlax.yaml"
    path.write_text("crt: {}\n", encoding="utf-8")

    with pytest.raises(ValueError, match="fvg"):
        load_fvg_config(path)


def test_invalid_fvg_threshold_fails_closed(tmp_path):
    path = tmp_path / "atlax.yaml"
    path.write_text(
        "fvg:\n"
        "  enabled: true\n"
        "  execution_gate_enabled: false\n"
        "  min_gap_pips: 0\n"
        "  ltf_candle_count: 30\n"
        "  ltf_atr_period: 14\n"
        "  watch_max_age_seconds: 14400\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="min_gap_pips"):
        load_fvg_config(path)
