from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from taprivo import cli, paths
from taprivo.vision.camera import CameraDevice

runner = CliRunner()
DEVICES = [
    CameraDevice(
        0,
        "Sinan's iPhone (iPhone camera; select to use)",
        0,
        0,
        False,
        "Sinan's iPhone",
        "continuity",
        False,
    ),
    CameraDevice(
        1, "FaceTime HD Kamera (640x480)", 640, 480, True, "FaceTime HD Kamera", "builtin", True
    ),
]


def test_camera_list_json(taprivo_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "list_devices_fn", lambda: DEVICES)
    result = runner.invoke(cli.app, ["camera", "list", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["schema_version"] == 1
    assert payload["devices"][1] == {
        "index": 1,
        "label": "FaceTime HD Kamera (640x480)",
        "width": 640,
        "height": 480,
        "has_signal": True,
        "name": "FaceTime HD Kamera",
        "kind": "builtin",
        "probed": True,
    }
    # The Continuity Camera is listed without ever having been opened.
    assert payload["devices"][0]["kind"] == "continuity"
    assert payload["devices"][0]["probed"] is False


def test_camera_list_human_and_empty(taprivo_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "list_devices_fn", lambda: DEVICES)
    result = runner.invoke(cli.app, ["camera", "list"])
    assert result.exit_code == 0
    assert "[0] Sinan's iPhone — continuity, 0x0, not probed" in result.output
    assert "[1] FaceTime HD Kamera — builtin, 640x480, signal (default)" in result.output
    assert result.output.count("(default)") == 1
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
