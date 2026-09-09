from __future__ import annotations

from pathlib import Path

import pytest

from taprivo import doctor, paths
from taprivo.adapters.base import Check
from taprivo.config import Config
from taprivo.vision.camera import CameraDevice, CameraError

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
HEALTHY_ENDPOINT = [
    Check("endpoint", "ok", "app reachable"),
    Check("auth", "ok", "token accepted"),
    Check("tools", "ok", "4 tools listed"),
]


class _StubAdapter:
    """Stands in for an adapter: contributes the checks it was given, which is
    nothing at all when a test wants the agents out of the overall result."""

    name = "stub"

    def __init__(self, *checks: Check) -> None:
        self._checks = list(checks)

    def verify(self) -> list[Check]:
        return list(self._checks)


@pytest.fixture
def quiet_agents(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the agent adapters out of the way: these tests are about
    composition and the camera checks, not the machine's Claude Code install."""
    monkeypatch.setattr(doctor, "make_claude_adapter", lambda config: _StubAdapter())
    monkeypatch.setattr(doctor, "make_cursor_adapter", lambda config: _StubAdapter())


def statuses(checks: list[Check]) -> dict[str, str]:
    return {check.name: check.status for check in checks}


def test_run_checks_composes_endpoint_camera_and_agent_checks(
    taprivo_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(doctor, "endpoint_checks", lambda config: HEALTHY_ENDPOINT)
    monkeypatch.setattr(doctor, "list_devices_fn", lambda: DEVICES)
    monkeypatch.setattr(
        doctor,
        "make_claude_adapter",
        lambda config: _StubAdapter(Check("claude_registration", "ok", "registered")),
    )
    monkeypatch.setattr(
        doctor,
        "make_cursor_adapter",
        lambda config: _StubAdapter(Check("cursor_registration", "ok", "registered")),
    )
    checks = doctor.run_checks(Config())
    names = [check.name for check in checks]
    assert names[:3] == ["endpoint", "auth", "tools"]
    assert names[-2:] == ["claude_registration", "cursor_registration"]
    assert "camera_devices" in names


def test_overall_ok_ignores_warnings_but_not_failures() -> None:
    assert doctor.overall_ok([Check("a", "ok", ""), Check("b", "warn", "")]) is True
    assert doctor.overall_ok([Check("a", "ok", ""), Check("b", "fail", "")]) is False


def test_camera_checks_with_probe(
    taprivo_home: Path, quiet_agents: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(doctor, "endpoint_checks", lambda config: HEALTHY_ENDPOINT)
    monkeypatch.setattr(doctor, "list_devices_fn", lambda: DEVICES)
    monkeypatch.setattr(
        doctor, "camera_probe_fn", lambda config: (True, "read a frame from camera 1")
    )
    monkeypatch.setattr(doctor, "fps_probe_fn", lambda config: (22.5, ""))
    checks = doctor.run_checks(Config(), camera_probe=True)
    found = statuses(checks)
    detail = {check.name: check.detail for check in checks}
    assert found["model"] == "ok"
    assert found["mediapipe"] in ("ok", "fail")
    assert found["camera_devices"] == "ok"
    assert "built-in camera: FaceTime HD Kamera" in detail["camera_devices"]
    assert found["camera_permission"] == "ok"
    assert found["camera_fps"] == "ok"


def test_without_devices_the_camera_check_warns(
    taprivo_home: Path, quiet_agents: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No camera at all is a supported, simulator-only configuration: it must
    warn, not fail, and must not sink an otherwise-healthy doctor run."""
    monkeypatch.setattr(doctor, "endpoint_checks", lambda config: HEALTHY_ENDPOINT)
    monkeypatch.setattr(doctor, "list_devices_fn", lambda: [])
    checks = doctor.run_checks(Config())
    found = statuses(checks)
    assert found["camera_devices"] == "warn"
    assert "camera_permission" not in found
    assert "camera_fps" not in found
    assert doctor.overall_ok(checks) is True


def test_without_the_probe_flag_permission_and_fps_are_skipped(
    taprivo_home: Path, quiet_agents: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without --camera-probe, doctor still enumerates camera devices (which
    briefly opens each index to detect a signal); only camera_permission and
    camera_fps -- the permission/fps probes -- are skipped."""

    def _must_not_run(config: Config) -> tuple[bool, str]:
        raise AssertionError("camera_probe_fn must not run without a probe request")

    monkeypatch.setattr(doctor, "endpoint_checks", lambda config: HEALTHY_ENDPOINT)
    monkeypatch.setattr(doctor, "list_devices_fn", lambda: DEVICES)
    monkeypatch.setattr(doctor, "camera_probe_fn", _must_not_run)
    found = statuses(doctor.run_checks(Config()))
    assert found["camera_devices"] == "ok"
    assert "camera_permission" not in found
    assert "camera_fps" not in found


def test_probe_is_skipped_while_taprivo_runs(
    taprivo_home: Path, quiet_agents: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A probe must not contend with a live Taprivo instance for the camera:
    when the instance lock is held it warns and skips instead."""

    def _must_not_run(config: Config) -> tuple[bool, str]:
        raise AssertionError("camera must not be opened while Taprivo is running")

    monkeypatch.setattr(doctor, "endpoint_checks", lambda config: HEALTHY_ENDPOINT)
    monkeypatch.setattr(doctor, "list_devices_fn", lambda: DEVICES)
    monkeypatch.setattr(doctor, "camera_probe_fn", _must_not_run)
    lock = paths.InstanceLock()
    assert lock.acquire()
    try:
        found = statuses(doctor.run_checks(Config(), camera_probe=True))
    finally:
        lock.release()
    assert found["camera_permission"] == "warn"
    assert "camera_fps" not in found


def test_lock_held_lets_the_running_app_skip_the_probe_itself(
    taprivo_home: Path, quiet_agents: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The app holds its own instance lock, so it says so instead of probing
    the lock (which it would always fail) or fighting itself for the camera."""

    def _must_not_run(config: Config) -> tuple[bool, str]:
        raise AssertionError("camera must not be opened while Taprivo is running")

    monkeypatch.setattr(doctor, "endpoint_checks", lambda config: HEALTHY_ENDPOINT)
    monkeypatch.setattr(doctor, "list_devices_fn", lambda: DEVICES)
    monkeypatch.setattr(doctor, "camera_probe_fn", _must_not_run)
    found = statuses(doctor.run_checks(Config(), camera_probe=True, lock_held=True))
    assert found["camera_permission"] == "warn"
    assert "Taprivo is running" in doctor.camera_checks(Config(), True, True)[-1].detail


def test_camera_probe_without_a_frame_hints_at_a_closed_lid(
    taprivo_home: Path, quiet_agents: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A camera that opens but never delivers a frame is the classic closed-lid
    MacBook symptom: the hint must point people at the lid, not just Settings."""
    monkeypatch.setattr(doctor, "endpoint_checks", lambda config: HEALTHY_ENDPOINT)
    monkeypatch.setattr(doctor, "list_devices_fn", lambda: DEVICES)
    monkeypatch.setattr(
        doctor,
        "camera_probe_fn",
        lambda config: (False, "camera 1 opened but delivered no frame within 3 s"),
    )
    checks = {c.name: c for c in doctor.run_checks(Config(), camera_probe=True, lock_held=False)}
    assert checks["camera_permission"].status == "fail"
    assert checks["camera_permission"].detail == (
        "camera 1 opened but delivered no frame within 3 s"
    )
    assert "lid" in checks["camera_permission"].hint


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

    monkeypatch.setattr(doctor, "list_devices_fn", lambda: DEVICES)
    monkeypatch.setattr(doctor, "CameraSource", ExplodingSource)
    fps, error = doctor.fps_probe_fn(Config())
    assert fps < 0
    assert "could not be opened" in error
