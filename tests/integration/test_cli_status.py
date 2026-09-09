import json

from typer.testing import CliRunner

from taprivo.cli import app
from taprivo.core.events import Finger, Hand
from tests.integration.conftest import RunningServer

runner = CliRunner()


def _write_port(server: RunningServer) -> None:
    from taprivo import paths

    paths.user_config_path().write_text(f"server:\n  port: {server.config.server.port}\n")


def test_status_json_reports_balance_without_token(running_server: RunningServer) -> None:
    _write_port(running_server)
    for _ in range(3):
        running_server.simulator.tap(Hand.LEFT, Finger.INDEX)
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


def test_status_shows_camera_stats(running_server: RunningServer) -> None:
    _write_port(running_server)
    running_server.engine.set_camera_stats(19.5, 0.8)
    result = runner.invoke(app, ["status", "--json"])
    payload = json.loads(result.output)
    assert payload["camera_fps"] == 19.5 and payload["detection_ratio"] == 0.8
    human = runner.invoke(app, ["status"])
    assert "Camera:" in human.output and "19" in human.output


def test_stats_json(running_server: RunningServer) -> None:
    _write_port(running_server)
    running_server.simulator.tap(Hand.RIGHT, Finger.PINKY)
    result = runner.invoke(app, ["stats", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["taps_per_finger"]["pinky"] == 1
    assert payload["taps_per_hand"] == {"left": 0, "right": 1}
    assert payload["combo_multiplier"] == 1.0
    assert payload["rhythm_multiplier"] == 1.0
    assert payload["rhythm_steady"] is False
    assert payload["bpm"] == 0.0


def test_stats_human_output_lists_hands_and_combo(running_server: RunningServer) -> None:
    _write_port(running_server)
    running_server.simulator.tap(Hand.LEFT, Finger.MIDDLE)
    running_server.simulator.tap(Hand.RIGHT, Finger.MIDDLE)
    result = runner.invoke(app, ["stats"])
    assert result.exit_code == 0, result.output
    assert "Hands:     left 1, right 1" in result.output
    assert "taps/min" in result.output
    assert "Combo:     x" in result.output and "×)" in result.output
    assert "Rhythm:    " in result.output


def test_stats_appends_todays_totals_when_the_store_has_a_row(
    running_server: RunningServer,
) -> None:
    from datetime import UTC, datetime

    from taprivo import paths
    from taprivo.core.stats_sink import SessionRow
    from taprivo.core.stats_store import StatsStore

    _write_port(running_server)
    store = StatsStore(paths.stats_path())
    store.upsert_session(
        SessionRow(
            session_id="earlier",
            started_utc=datetime.now(UTC).replace(microsecond=0).isoformat(),
            ended_utc=None,
            duration_s=600,
            mode="simulator",
            taps_total=75,
            taps_left=40,
            taps_right=35,
            squeezes=0,
            generated=750,
            spent=200,
            overflow=0,
            max_combo=9,
        )
    )
    store.close()

    result = runner.invoke(app, ["stats"])
    assert result.exit_code == 0, result.output
    assert "Today:     1 sessions, 75 taps, +750 / -200" in result.output


def test_stats_omits_today_without_a_store(running_server: RunningServer) -> None:
    _write_port(running_server)
    result = runner.invoke(app, ["stats"])
    assert result.exit_code == 0, result.output
    assert "Today:" not in result.output
