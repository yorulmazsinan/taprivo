from pathlib import Path

from typer.testing import CliRunner

from taprivo import paths
from taprivo.cli import app

runner = CliRunner()


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
