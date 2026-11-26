"""
Tests for VolumeSource and VolumeQuality enumerations.

Authority: docs/40_VOLUME_PROFILE_CONFLUENCE_PLAN.md §4, docs/decisions/ADR-017.md
"""

from core.models.volume_source import VolumeQuality, VolumeSource


class TestVolumeSource:
    def test_approved_values_exist(self):
        assert VolumeSource.REAL_VOLUME.value == "REAL_VOLUME"
        assert VolumeSource.MT5_TICK_VOLUME.value == "MT5_TICK_VOLUME"
        assert VolumeSource.UNKNOWN.value == "UNKNOWN"

    def test_str_enum_equality(self):
        assert VolumeSource.MT5_TICK_VOLUME == "MT5_TICK_VOLUME"
        assert VolumeSource.REAL_VOLUME == "REAL_VOLUME"
        assert VolumeSource.UNKNOWN == "UNKNOWN"

    def test_all_three_members(self):
        members = {m.value for m in VolumeSource}
        assert members == {"REAL_VOLUME", "MT5_TICK_VOLUME", "UNKNOWN"}


class TestVolumeQuality:
    def test_approved_values_exist(self):
        assert VolumeQuality.REAL.value == "REAL"
        assert VolumeQuality.BROKER_TICK_PROXY.value == "BROKER_TICK_PROXY"
        assert VolumeQuality.UNKNOWN.value == "UNKNOWN"

    def test_str_enum_equality(self):
        assert VolumeQuality.REAL == "REAL"
        assert VolumeQuality.BROKER_TICK_PROXY == "BROKER_TICK_PROXY"
        assert VolumeQuality.UNKNOWN == "UNKNOWN"

    def test_all_three_members(self):
        members = {m.value for m in VolumeQuality}
        assert members == {"REAL", "BROKER_TICK_PROXY", "UNKNOWN"}
