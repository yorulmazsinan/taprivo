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
