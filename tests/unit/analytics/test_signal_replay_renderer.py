import struct

from core.analytics.signal_replay_capture import ReplayCaptureService
from core.analytics.signal_replay_renderer import (
    ReplayRenderConfig,
    ReplayRenderError,
    ReplayRenderer,
    build_replay_caption,
)
from core.storage.replay_repository import SQLiteReplaySnapshotRepository

from tests.unit.analytics.test_signal_replay_capture import _fixture


def _snapshot(tmp_path):
    repository = SQLiteReplaySnapshotRepository(str(tmp_path / "journal.sqlite"))
    alert, detector, sequence = _fixture()
    snapshot = ReplayCaptureService(
        repository,
        context_bars_before_parent=3,
    ).capture(alert, detector, sequence)
    assert snapshot is not None
    return snapshot


def test_renderer_marks_three_candle_formation_and_reuses_cache(tmp_path):
    snapshot = _snapshot(tmp_path)
    renderer = ReplayRenderer(
        ReplayRenderConfig(
            width=800,
            height=450,
            cache_dir=str(tmp_path / "replay_cache"),
            timeout_seconds=10,
        )
    )

    first = renderer.render(snapshot)
    second = renderer.render(snapshot)

    assert first.image_bytes.startswith(b"\x89PNG\r\n\x1a\n")
    assert first.image_bytes == second.image_bytes
    assert first.cache_key == second.cache_key
    assert struct.unpack(">II", first.image_bytes[16:24]) == (800, 450)
    assert "Data as of" in build_replay_caption(snapshot)


def test_renderer_rejects_missing_matplotlib_with_typed_error(monkeypatch, tmp_path):
    snapshot = _snapshot(tmp_path)
    renderer = ReplayRenderer(
        ReplayRenderConfig(
            width=800,
            height=450,
            cache_dir=str(tmp_path / "replay_cache"),
            timeout_seconds=10,
        )
    )
    monkeypatch.setattr(renderer, "_render_png", lambda _snapshot: (_ for _ in ()).throw(ImportError()))

    try:
        renderer.render(snapshot)
    except ReplayRenderError as exc:
        assert exc.reason == "renderer_dependency_missing"
    else:
        raise AssertionError("renderer should expose a typed dependency failure")
