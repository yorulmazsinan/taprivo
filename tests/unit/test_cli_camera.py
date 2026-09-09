from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from taprivo import cli, paths
from taprivo.adapters.base import Check
from taprivo.config import Config
from taprivo.vision.camera import CameraDevice, CameraError

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


class _NoOpAdapter:
    """Stands in for ClaudeAdapter when a test wants doctor's Claude checks to
    contribute nothing (neither ok nor fail) to the overall result."""

    def verify(self) -> list[Check]:
        return []


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
    monkeypatch.setattr(cli, "fps_probe_fn", lambda config: (22.5, ""))
    paths.user_config_path().parent.mkdir(parents=True, exist_ok=True)
    paths.user_config_path().write_text("server:\n  port: 1\n")
    result = runner.invoke(cli.app, ["doctor", "--json", "--camera-probe"])
    payload = json.loads(result.output)
    statuses = {c["name"]: c["status"] for c in payload["checks"]}
    assert statuses["model"] == "ok"
    assert statuses["mediapipe"] in ("ok", "fail")
    checks = {c["name"]: c for c in payload["checks"]}
    assert statuses["camera_devices"] == "ok"
    assert "built-in camera: FaceTime HD Kamera" in checks["camera_devices"]["detail"]
    assert statuses["camera_permission"] == "ok"
    assert statuses["camera_fps"] == "ok"


def test_doctor_without_devices_warns_camera_check(
    taprivo_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No camera at all is a supported, simulator-only configuration: it must
    warn, not fail, and must not sink an otherwise-healthy doctor run."""
    monkeypatch.setattr(cli, "list_devices_fn", lambda: [])
    monkeypatch.setattr(
        cli,
        "_endpoint_checks",
        lambda config: [
            Check("endpoint", "ok", "app reachable"),
            Check("auth", "ok", "token accepted"),
            Check("tools", "ok", "4 tools listed"),
        ],
    )
    monkeypatch.setattr(cli, "make_claude_adapter", lambda config: _NoOpAdapter())
    paths.user_config_path().parent.mkdir(parents=True, exist_ok=True)
    paths.user_config_path().write_text("server:\n  port: 1\n")
    result = runner.invoke(cli.app, ["doctor", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    statuses = {c["name"]: c["status"] for c in payload["checks"]}
    assert statuses["camera_devices"] == "warn"
    assert "camera_permission" not in statuses
    assert "camera_fps" not in statuses
    assert payload["ok"] is True


def test_doctor_without_probe_flag_skips_permission_and_fps_probes(
    taprivo_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without --camera-probe, doctor still enumerates camera devices (which
    briefly opens each index to detect a signal); only camera_permission and
    camera_fps -- the permission/fps probes -- are skipped."""

    def _must_not_run(config: Config) -> tuple[bool, str]:
        raise AssertionError("camera_probe_fn must not run without --camera-probe")

    monkeypatch.setattr(cli, "list_devices_fn", lambda: DEVICES)
    monkeypatch.setattr(cli, "camera_probe_fn", _must_not_run)
    paths.user_config_path().parent.mkdir(parents=True, exist_ok=True)
    paths.user_config_path().write_text("server:\n  port: 1\n")
    result = runner.invoke(cli.app, ["doctor", "--json"])
    payload = json.loads(result.output)
    statuses = {c["name"]: c["status"] for c in payload["checks"]}
    assert statuses["camera_devices"] == "ok"
    assert "camera_permission" not in statuses
    assert "camera_fps" not in statuses


def test_doctor_camera_probe_skipped_while_taprivo_running(
    taprivo_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """--camera-probe must not contend with a live Taprivo instance for the
    camera: when the instance lock is held it warns and skips instead."""

    def _must_not_run(config: Config) -> tuple[bool, str]:
        raise AssertionError("camera must not be opened while Taprivo is running")

    monkeypatch.setattr(cli, "list_devices_fn", lambda: DEVICES)
    monkeypatch.setattr(cli, "camera_probe_fn", _must_not_run)
    paths.user_config_path().parent.mkdir(parents=True, exist_ok=True)
    paths.user_config_path().write_text("server:\n  port: 1\n")
    lock = paths.InstanceLock()
    assert lock.acquire()
    try:
        result = runner.invoke(cli.app, ["doctor", "--json", "--camera-probe"])
        payload = json.loads(result.output)
        statuses = {c["name"]: c["status"] for c in payload["checks"]}
        assert statuses["camera_permission"] == "warn"
        assert "camera_fps" not in statuses
    finally:
        lock.release()


def test_doctor_camera_probe_no_frame_hints_at_closed_lid(
    taprivo_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A camera that opens but never delivers a frame is the classic closed-lid
    MacBook symptom: the hint must point people at the lid, not just Settings."""
    monkeypatch.setattr(cli, "list_devices_fn", lambda: DEVICES)
    monkeypatch.setattr(
        cli,
        "camera_probe_fn",
        lambda config: (False, "camera 1 opened but delivered no frame within 3 s"),
    )
    paths.user_config_path().parent.mkdir(parents=True, exist_ok=True)
    paths.user_config_path().write_text("server:\n  port: 1\n")
    result = runner.invoke(cli.app, ["doctor", "--json", "--camera-probe"])
    payload = json.loads(result.output)
    checks = {c["name"]: c for c in payload["checks"]}
    assert checks["camera_permission"]["status"] == "fail"
    assert checks["camera_permission"]["detail"] == (
        "camera 1 opened but delivered no frame within 3 s"
    )
    assert "lid" in checks["camera_permission"]["hint"]


def test_fps_probe_fn_catches_camera_error(
    taprivo_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """fps_probe_fn must never raise: a camera failure during the probe itself
    (not just device listing) must come back as an error tuple."""

    class ExplodingSource:
        def __init__(self, index: int, width: int = 640, height: int = 480) -> None:
            self.index = index

        def open(self) -> None:
            raise CameraError(f"camera {self.index} could not be opened")

        def read(self) -> None:
            return None

        def close(self) -> None:
            pass

    monkeypatch.setattr(cli, "list_devices_fn", lambda: DEVICES)
    monkeypatch.setattr(cli, "CameraSource", ExplodingSource)
    fps, error = cli.fps_probe_fn(Config())
    assert fps < 0
    assert "could not be opened" in error
