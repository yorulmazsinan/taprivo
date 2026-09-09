import json
from pathlib import Path

from typer.testing import CliRunner

from taprivo import __version__
from taprivo.cli import NO_VALUE, _rhythm_text, app

runner = CliRunner()


def test_version() -> None:
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert result.output.strip() == f"taprivo {__version__}"


def test_status_when_not_running(taprivo_home: Path) -> None:
    taprivo_home.mkdir(parents=True)
    (taprivo_home / "config.yaml").write_text("server:\n  port: 1\n")
    (taprivo_home / "token").write_text("t\n")
    result = runner.invoke(app, ["status"])
    assert result.exit_code == 1
    assert "not running" in result.output


def test_status_json_when_not_running(taprivo_home: Path) -> None:
    taprivo_home.mkdir(parents=True)
    (taprivo_home / "config.yaml").write_text("server:\n  port: 1\n")
    (taprivo_home / "token").write_text("t\n")
    result = runner.invoke(app, ["status", "--json"])
    assert result.exit_code == 1
    payload = json.loads(result.output)
    assert payload["schema_version"] == 1
    assert payload["ok"] is False
    assert "not running" in payload["error"]


def test_status_without_token(taprivo_home: Path) -> None:
    result = runner.invoke(app, ["status"])
    assert result.exit_code == 1
    assert "token" in result.output.lower()


def test_invalid_config_exits_2(taprivo_home: Path) -> None:
    taprivo_home.mkdir(parents=True)
    (taprivo_home / "config.yaml").write_text("server:\n  host: 0.0.0.0\n")
    result = runner.invoke(app, ["status"])
    assert result.exit_code == 2
    assert "loopback" in result.output


def test_rhythm_line_without_a_beat() -> None:
    data = {"bpm": 0.0, "rhythm_steady": False, "rhythm_multiplier": 1.0}
    assert _rhythm_text(data) == NO_VALUE
    assert _rhythm_text({}) == NO_VALUE


def test_rhythm_line_with_an_unsteady_beat() -> None:
    data = {"bpm": 96.4, "rhythm_steady": False, "rhythm_multiplier": 1.0}
    assert _rhythm_text(data) == "96 BPM"


def test_rhythm_line_with_a_steady_beat() -> None:
    data = {"bpm": 96.0, "rhythm_steady": True, "rhythm_multiplier": 1.25}
    assert _rhythm_text(data) == "96 BPM (steady, 1.25×)"
