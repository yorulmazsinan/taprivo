from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from taprivo import cli, paths
from taprivo.adapters.claude import ClaudeAdapter
from taprivo.vision.camera import CameraDevice
from tests.integration.conftest import RunningServer, free_port
from tests.unit.test_claude_adapter import FakeClaude

runner = CliRunner()


@pytest.fixture(autouse=True)
def _stub_camera(monkeypatch: pytest.MonkeyPatch) -> None:
    """These tests exercise the endpoint/auth/Claude checks, not real camera
    hardware; stub the camera hooks so results don't depend on the machine."""
    monkeypatch.setattr(
        cli, "list_devices_fn", lambda: [CameraDevice(0, "Camera 0 (640x480)", 640, 480, True)]
    )
    monkeypatch.setattr(cli, "camera_probe_fn", lambda config: (True, "read a frame from camera 0"))


@pytest.fixture
def fake_claude(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FakeClaude:
    fake_runner = FakeClaude()
    script = tmp_path / "claude"
    script.write_text("#!/bin/sh\nexit 0\n")
    script.chmod(0o755)
    monkeypatch.setattr(
        cli,
        "make_claude_adapter",
        lambda config: ClaudeAdapter(
            config, home=tmp_path / "home", runner=fake_runner, claude_bin=str(script)
        ),
    )
    return fake_runner


def test_doctor_with_running_server(running_server: RunningServer, fake_claude: FakeClaude) -> None:
    paths.user_config_path().write_text(f"server:\n  port: {running_server.config.server.port}\n")
    runner.invoke(cli.app, ["setup", "claude", "--install-instructions"])
    result = runner.invoke(cli.app, ["doctor", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    statuses = {c["name"]: c["status"] for c in payload["checks"]}
    assert statuses["endpoint"] == "ok"
    assert statuses["auth"] == "ok"
    assert statuses["tools"] == "ok"
    assert statuses["claude_registration"] == "ok"
    assert payload["ok"] is True
    assert running_server.token not in result.output


def test_doctor_when_stopped(taprivo_home: Path, fake_claude: FakeClaude) -> None:
    taprivo_home.mkdir(parents=True)
    paths.user_config_path().write_text(f"server:\n  port: {free_port()}\n")
    paths.read_or_create_token()
    result = runner.invoke(cli.app, ["doctor"])
    assert result.exit_code == 1
    assert "FAIL endpoint" in result.output
    assert "taprivo simulate" in result.output
