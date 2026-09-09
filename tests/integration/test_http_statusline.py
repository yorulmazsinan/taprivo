"""The /statusline endpoint: what Claude Code pipes in, what the HUD learns."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import httpx2
import pytest

from taprivo.mcp.statusline import parse_payload
from tests.integration.conftest import RunningServer

PAYLOAD = {
    "hook_event_name": "Status",
    "session_id": "abc-123",
    "model": {"id": "claude-opus-4", "display_name": "Opus"},
    "context_window": {"used_percentage": 63.4},
    "cost": {"total_cost_usd": 1.4237, "total_duration_ms": 2_880_000},
    "rate_limits": {
        "five_hour": {"used_percentage": 42.0, "resets_at": 1_757_000_000},
        "seven_day": {"used_percentage": 71.5, "resets_at": 1_757_400_000},
    },
    "version": "2.1.0",
    "an_unknown_field": {"nested": [1, 2, 3]},
}


def post(server: RunningServer, payload: object) -> httpx2.Response:
    return httpx2.post(
        f"{server.origin}/statusline",
        content=json.dumps(payload),
        headers=server.headers(),
        timeout=5,
    )


def test_payload_updates_the_engine_and_answers_with_energy(
    running_server: RunningServer,
) -> None:
    response = post(running_server, PAYLOAD)
    assert response.status_code == 200
    assert response.headers["content-type"] == "text/plain; charset=utf-8"
    snapshot = running_server.engine.snapshot()
    assert response.text == f"⚡ {snapshot.available} · x{snapshot.combo} · 5h 42%"
    agent = snapshot.agent
    assert agent is not None
    assert agent.model == "Opus"
    assert agent.context_used == pytest.approx(63.4)
    assert agent.cost_usd == pytest.approx(1.4237)
    assert agent.duration_s == 2880
    assert agent.five_hour_used == pytest.approx(42.0)
    assert agent.five_hour_resets_at == 1_757_000_000
    assert agent.seven_day_used == pytest.approx(71.5)
    assert agent.seven_day_resets_at == 1_757_400_000
    assert agent.version == "2.1.0"
    assert agent.received_at_ms > 0


def test_missing_rate_limits_is_accepted(running_server: RunningServer) -> None:
    response = post(running_server, {"model": {"display_name": "Sonnet"}})
    assert response.status_code == 200
    assert "5h" not in response.text
    agent = running_server.engine.snapshot().agent
    assert agent is not None
    assert agent.model == "Sonnet" and agent.five_hour_used is None


def test_a_junk_body_is_rejected_without_touching_the_engine(
    running_server: RunningServer,
) -> None:
    response = httpx2.post(
        f"{running_server.origin}/statusline",
        content="not json",
        headers=running_server.headers(),
        timeout=5,
    )
    assert response.status_code == 400
    assert running_server.engine.snapshot().agent is None


def test_a_payload_of_wrong_shapes_leaves_every_field_empty(
    running_server: RunningServer,
) -> None:
    response = post(running_server, {"model": "Opus", "cost": [], "rate_limits": 7})
    assert response.status_code == 200
    agent = running_server.engine.snapshot().agent
    assert agent is not None
    assert (agent.model, agent.cost_usd, agent.five_hour_used) == (None, None, None)


def test_the_combo_multiplier_reaches_the_status_line(running_server: RunningServer) -> None:
    for _ in range(10):
        running_server.simulator.tap_key("3")
    assert " 1.5×" in post(running_server, PAYLOAD).text


def test_parse_payload_ignores_booleans_and_blank_text() -> None:
    status = parse_payload(
        {"model": {"display_name": "  "}, "cost": {"total_cost_usd": True}, "version": None}
    )
    assert (status.model, status.cost_usd, status.version) == (None, None, None)


# -- the installed script ---------------------------------------------------


@pytest.mark.skipif(shutil.which("curl") is None, reason="curl is not installed")
def test_the_installed_script_talks_to_the_running_server(
    running_server: RunningServer, tmp_path: Path
) -> None:
    """Run the real POSIX sh script Taprivo installs against the live server."""
    from taprivo.adapters.claude import ClaudeAdapter

    adapter = ClaudeAdapter(running_server.config, home=tmp_path / "home")
    adapter.write_statusline_script()
    proc = subprocess.run(
        ["sh", str(adapter.statusline_script_path)],
        input=json.dumps(PAYLOAD),
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == f"⚡ {running_server.engine.snapshot().available} · x0 · 5h 42%"


@pytest.mark.skipif(shutil.which("curl") is None, reason="curl is not installed")
def test_the_script_chains_a_previous_status_line(
    running_server: RunningServer, tmp_path: Path
) -> None:
    from taprivo.adapters.claude import ClaudeAdapter

    adapter = ClaudeAdapter(running_server.config, home=tmp_path / "home")
    adapter.write_statusline_script()
    adapter.statusline_chain_path.write_text(
        json.dumps({"type": "command", "command": "echo mine"}), encoding="utf-8"
    )
    proc = subprocess.run(
        ["sh", str(adapter.statusline_script_path)],
        input=json.dumps(PAYLOAD),
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip().startswith("mine · ⚡")


@pytest.mark.skipif(shutil.which("curl") is None, reason="curl is not installed")
def test_the_script_is_quiet_when_taprivo_is_not_running(
    taprivo_home: Path, tmp_path: Path
) -> None:
    """The usual state of the world: Claude Code runs, Taprivo does not."""
    from taprivo.adapters.claude import ClaudeAdapter
    from taprivo.config import Config, ServerConfig

    adapter = ClaudeAdapter(Config(server=ServerConfig(port=1)), home=tmp_path / "home")
    adapter.write_statusline_script()
    proc = subprocess.run(
        ["sh", str(adapter.statusline_script_path)],
        input=json.dumps({"model": {"display_name": "Opus"}}),
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert proc.returncode == 0
    assert proc.stdout == ""
