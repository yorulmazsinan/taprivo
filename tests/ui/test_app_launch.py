from pathlib import Path

import pytest
from typer.testing import CliRunner

from taprivo import paths
from taprivo.cli import app
from taprivo.config import Config
from taprivo.ui import app as app_module

runner = CliRunner()


class _RaisingHudWindow:
    def __init__(self, *args: object, **kwargs: object) -> None:
        raise RuntimeError("boom")


class _FakeMcpServerThread:
    def __init__(self, *args: object, **kwargs: object) -> None:
        self.stopped = False

    def start(self) -> None:
        pass

    def stop(self, timeout: float = 5.0) -> None:
        self.stopped = True


def test_second_instance_exits_1(taprivo_home: Path) -> None:
    lock = paths.InstanceLock()
    assert lock.acquire()
    try:
        result = runner.invoke(app, ["simulate"])
        assert result.exit_code == 1
        assert "already running" in result.output
        bare = runner.invoke(app, [])
        assert bare.exit_code == 1
    finally:
        lock.release()


def test_help_still_works() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0 and "simulate" in result.output


def test_server_stopped_when_window_construction_fails(
    taprivo_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_server = _FakeMcpServerThread()
    monkeypatch.setattr(app_module, "McpServerThread", lambda *a, **k: fake_server)
    monkeypatch.setattr(app_module, "HudWindow", _RaisingHudWindow)

    with pytest.raises(RuntimeError, match="boom"):
        app_module.run_app(Config(), start_simulator=False)

    assert fake_server.stopped is True
    lock = paths.InstanceLock()
    assert lock.acquire()
    lock.release()


def test_run_app_accepts_open_camera_flag_and_cli_forwards_it(
    taprivo_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import inspect

    import typer

    import taprivo.ui.app as app_module
    from taprivo import cli

    assert "open_camera" in inspect.signature(app_module.run_app).parameters
    seen: dict[str, object] = {}

    def fake_run_app(config: object, *, start_simulator: bool, open_camera: bool = False) -> int:
        seen.update(start_simulator=start_simulator, open_camera=open_camera)
        return 0

    monkeypatch.setattr(app_module, "run_app", fake_run_app)
    with pytest.raises(typer.Exit):
        cli._launch(False, open_camera=True)
    assert seen == {"start_simulator": False, "open_camera": True}


def _read_history(days: int = 5) -> list[object]:
    from taprivo.core.stats_store import StatsStore

    store = StatsStore(paths.stats_path())
    try:
        return list(store.history(days))
    finally:
        store.close()


def test_a_session_row_is_written_and_ended_on_shutdown(
    taprivo_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(app_module, "McpServerThread", lambda *a, **k: _FakeMcpServerThread())
    monkeypatch.setattr(app_module, "HudWindow", _RaisingHudWindow)

    with pytest.raises(RuntimeError, match="boom"):
        app_module.run_app(Config(), start_simulator=False)

    assert paths.stats_path().exists()
    history = _read_history()
    assert len(history) == 1


def test_no_statistics_file_when_stats_are_disabled(
    taprivo_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from taprivo.config import StatsConfig

    monkeypatch.setattr(app_module, "McpServerThread", lambda *a, **k: _FakeMcpServerThread())
    monkeypatch.setattr(app_module, "HudWindow", _RaisingHudWindow)

    with pytest.raises(RuntimeError, match="boom"):
        app_module.run_app(Config(stats=StatsConfig(enabled=False)), start_simulator=False)

    assert not paths.stats_path().exists()
