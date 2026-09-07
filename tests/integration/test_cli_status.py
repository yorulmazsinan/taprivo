import json

from typer.testing import CliRunner

from taprivo.cli import app
from taprivo.core.events import Finger
from tests.integration.conftest import RunningServer

runner = CliRunner()


def _write_port(server: RunningServer) -> None:
    from taprivo import paths

    paths.user_config_path().write_text(f"server:\n  port: {server.config.server.port}\n")


def test_status_json_reports_balance_without_token(running_server: RunningServer) -> None:
    _write_port(running_server)
    for _ in range(3):
        running_server.simulator.tap(Finger.INDEX)
    result = runner.invoke(app, ["status", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["schema_version"] == 1
    assert payload["available"] == 30
    assert payload["mcp"] == "ready"
    assert payload["endpoint"] == running_server.url
    assert running_server.token not in result.output


def test_status_human_output(running_server: RunningServer) -> None:
    _write_port(running_server)
    result = runner.invoke(app, ["status"])
    assert result.exit_code == 0, result.output
    assert "Energy:" in result.output and "Endpoint:" in result.output
    assert running_server.token not in result.output


def test_stats_json(running_server: RunningServer) -> None:
    _write_port(running_server)
    running_server.simulator.tap(Finger.PINKY)
    result = runner.invoke(app, ["stats", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["taps_per_finger"]["pinky"] == 1
