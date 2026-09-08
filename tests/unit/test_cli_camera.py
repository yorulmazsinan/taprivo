from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from taprivo import cli, paths
from taprivo.vision.camera import CameraDevice

runner = CliRunner()
DEVICES = [
    CameraDevice(0, "Camera 0 (1920x1080) — no signal", 1920, 1080, False),
    CameraDevice(1, "Camera 1 (640x480)", 640, 480, True),
]


def test_camera_list_json(taprivo_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "list_devices_fn", lambda: DEVICES)
    result = runner.invoke(cli.app, ["camera", "list", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["schema_version"] == 1
    assert payload["devices"][1] == {
        "index": 1,
        "label": "Camera 1 (640x480)",
        "width": 640,
        "height": 480,
        "has_signal": True,
    }


def test_camera_list_human_and_empty(taprivo_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "list_devices_fn", lambda: DEVICES)
    result = runner.invoke(cli.app, ["camera", "list"])
    assert result.exit_code == 0 and "no signal" in result.output and "Camera 1" in result.output
    monkeypatch.setattr(cli, "list_devices_fn", lambda: [])
    empty = runner.invoke(cli.app, ["camera", "list"])
    assert empty.exit_code == 1 and "No camera" in empty.output


def test_calibrate_when_running(taprivo_home: Path) -> None:
    lock = paths.InstanceLock()
    assert lock.acquire()
    try:
        result = runner.invoke(cli.app, ["calibrate"])
        assert result.exit_code == 1
        assert "open the Camera window" in result.output
    finally:
        lock.release()


def test_calibrate_launches_with_camera(
    taprivo_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: dict[str, object] = {}

    def fake_run_app(config: object, *, start_simulator: bool, open_camera: bool = False) -> int:
        seen.update(start_simulator=start_simulator, open_camera=open_camera)
        return 0

    monkeypatch.setattr("taprivo.ui.app.run_app", fake_run_app)
    result = runner.invoke(cli.app, ["calibrate"])
    assert result.exit_code == 0
    assert seen == {"start_simulator": False, "open_camera": True}


def test_doctor_camera_checks(taprivo_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from taprivo.adapters.claude import ClaudeAdapter
    from tests.unit.test_claude_adapter import FakeClaude

    monkeypatch.setattr(
        cli,
        "make_claude_adapter",
        lambda config: ClaudeAdapter(
            config, home=taprivo_home / "h", runner=FakeClaude(), claude_bin="/nonexistent/claude"
        ),
    )
    monkeypatch.setattr(cli, "list_devices_fn", lambda: DEVICES)
    monkeypatch.setattr(cli, "camera_probe_fn", lambda config: (True, "read a frame from camera 1"))
    monkeypatch.setattr(cli, "fps_probe_fn", lambda config: 22.5)
    paths.user_config_path().parent.mkdir(parents=True, exist_ok=True)
    paths.user_config_path().write_text("server:\n  port: 1\n")
    result = runner.invoke(cli.app, ["doctor", "--json", "--camera-probe"])
    payload = json.loads(result.output)
    statuses = {c["name"]: c["status"] for c in payload["checks"]}
    assert statuses["model"] == "ok"
    assert statuses["mediapipe"] in ("ok", "fail")
    assert statuses["camera_devices"] == "ok"
    assert statuses["camera_permission"] == "ok"
    assert statuses["camera_fps"] == "ok"


def test_doctor_without_devices_fails_camera_check(
    taprivo_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli, "list_devices_fn", lambda: [])
    paths.user_config_path().parent.mkdir(parents=True, exist_ok=True)
    paths.user_config_path().write_text("server:\n  port: 1\n")
    result = runner.invoke(cli.app, ["doctor", "--json"])
    payload = json.loads(result.output)
    statuses = {c["name"]: c["status"] for c in payload["checks"]}
    assert statuses["camera_devices"] == "fail"
    assert "camera_permission" not in statuses  # no probe without devices
