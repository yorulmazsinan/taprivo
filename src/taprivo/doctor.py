"""Diagnostics shared by the `taprivo doctor` command and the Setup window.

Nothing here depends on Typer or on a terminal: `run_checks` composes the same
list of checks for both callers, and `overall_ok` decides the verdict.
"""

from __future__ import annotations

import time
from typing import Any

from taprivo import paths
from taprivo.adapters import make_claude_adapter, make_cursor_adapter
from taprivo.adapters.base import Check
from taprivo.config import Config
from taprivo.mcp.client import (
    ClientError,
    NotRunningError,
    TokenMissingError,
    UnauthorizedError,
    for_config,
    run_sync,
)
from taprivo.mcp.server import TOOL_NAMES, port_is_free
from taprivo.vision import tracker as vision_tracker
from taprivo.vision.camera import CameraError, CameraSource, default_device, list_devices

CAMERA_SETTINGS_HINT = "System Settings > Privacy & Security > Camera"

# Indirections so tests (and the Setup window) can stand in for real hardware.
list_devices_fn = list_devices


def camera_probe_fn(config: Config) -> tuple[bool, str]:
    devices = list_devices_fn()
    device = default_device(devices, prefer_builtin=config.camera.prefer_builtin)
    if device is None:
        return False, "no camera device"
    source = CameraSource(device.index, width=config.camera.width, height=config.camera.height)
    try:
        source.open()
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            if source.read() is not None:
                return True, f"read a frame from camera {device.index}"
        return False, f"camera {device.index} opened but delivered no frame within 3 s"
    except CameraError as exc:
        return False, str(exc)
    finally:
        source.close()


def fps_probe_fn(config: Config) -> tuple[float, str]:
    """Read from the default camera for 5 s and run every frame through the hand
    tracker, returning (processed fps, ""), or (-1.0, error text) if anything in
    the probe -- opening the camera, loading mediapipe, reading a frame -- fails.
    Never raises: a probe failure must surface as a failed check, not a crash."""
    device = default_device(list_devices_fn(), prefer_builtin=config.camera.prefer_builtin)
    if device is None:
        return 0.0, "no camera device"
    source = CameraSource(device.index, width=config.camera.width, height=config.camera.height)
    tracker: Any = None
    try:
        source.open()
        tracker = vision_tracker.MediaPipeHandTracker()
        processed = 0
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            frame = source.read()
            if frame is None:
                continue
            tracker.process(frame)
            processed += 1
        return processed / 5.0, ""
    except Exception as exc:  # any probe failure must become a fail check, not a crash
        return -1.0, str(exc)
    finally:
        source.close()
        if tracker is not None:
            tracker.close()


def endpoint_checks(config: Config) -> list[Check]:
    checks: list[Check] = []
    try:
        names = run_sync(for_config(config).tool_names())
    except TokenMissingError:
        checks.append(
            Check(
                "endpoint",
                "fail",
                "no token file yet",
                "start Taprivo once with 'taprivo simulate'",
            )
        )
        return checks
    except NotRunningError:
        if not port_is_free(config.server.host, config.server.port):
            checks.append(
                Check(
                    "endpoint",
                    "fail",
                    f"port {config.server.port} is bound by another process",
                    "set server.port in config.yaml, then run 'taprivo setup claude' again",
                )
            )
        else:
            checks.append(
                Check(
                    "endpoint", "fail", "Taprivo is not running", "start it with 'taprivo simulate'"
                )
            )
        return checks
    except UnauthorizedError:
        checks.append(Check("endpoint", "ok", f"app reachable at {config.endpoint_url}"))
        checks.append(
            Check(
                "auth",
                "fail",
                "stored token rejected",
                "quit Taprivo, then run 'taprivo setup claude'",
            )
        )
        return checks
    except ClientError as exc:
        checks.append(Check("endpoint", "fail", str(exc), "run 'taprivo simulate' and retry"))
        return checks
    checks.append(Check("endpoint", "ok", f"app reachable at {config.endpoint_url}"))
    checks.append(Check("auth", "ok", "token accepted"))
    if set(names) == set(TOOL_NAMES):
        checks.append(Check("tools", "ok", f"{len(names)} tools listed"))
    else:
        checks.append(
            Check("tools", "fail", f"unexpected tools: {sorted(names)}", "reinstall Taprivo")
        )
    return checks


def _instance_running(lock_held: bool | None) -> bool:
    """Is a Taprivo instance holding the camera? `lock_held` lets an in-process
    caller (the app itself) say so without fighting its own instance lock."""
    if lock_held is not None:
        return lock_held
    lock = paths.InstanceLock()
    if not lock.acquire():
        return True
    lock.release()
    return False


def camera_checks(config: Config, probe: bool, lock_held: bool | None = None) -> list[Check]:
    """model/mediapipe checks are metadata-only. Device enumeration (camera_devices)
    briefly opens each camera index except an iPhone Continuity Camera (opening
    one wakes the phone) to detect a signal, and runs unconditionally.
    The permission and fps probes (camera_probe_fn/fps_probe_fn) only run when
    `probe` is set (the CLI's --camera-probe flag), and never while Taprivo itself
    is running."""
    checks: list[Check] = []
    try:
        vision_tracker.verify_model()
        checks.append(Check("model", "ok", "hand landmark model present and verified"))
    except vision_tracker.ModelError as exc:
        checks.append(Check("model", "fail", str(exc), "reinstall Taprivo"))
    if vision_tracker.available():
        checks.append(Check("mediapipe", "ok", "mediapipe importable"))
    else:
        checks.append(
            Check(
                "mediapipe",
                "fail",
                "mediapipe not installed",
                "run 'uv sync' in the Taprivo checkout",
            )
        )
    devices = list_devices_fn()
    if not devices:
        checks.append(
            Check(
                "camera_devices",
                "warn",
                "no camera devices found; the keyboard simulator still works",
                "connect a camera if you want hand tracking",
            )
        )
        return checks
    signal = [d for d in devices if d.has_signal]
    builtin = next((d for d in devices if d.kind == "builtin"), None)
    detail = f"{len(devices)} device(s), {len(signal)} with signal"
    if builtin is not None:
        detail += "; built-in camera: " + (builtin.name or f"camera {builtin.index}")
    checks.append(
        Check(
            "camera_devices",
            "ok" if signal else "warn",
            detail,
            "" if signal else "select a camera that shows an image in the Camera window",
        )
    )
    if not probe:
        return checks
    if _instance_running(lock_held):
        checks.append(
            Check(
                "camera_permission",
                "warn",
                "Taprivo is running; camera probe skipped",
                "quit Taprivo and rerun doctor --camera-probe",
            )
        )
        return checks
    ok, detail = camera_probe_fn(config)
    checks.append(
        Check(
            "camera_permission",
            "ok" if ok else "fail",
            detail,
            ""
            if ok
            else (
                f"allow camera access for your terminal or Taprivo in {CAMERA_SETTINGS_HINT}; "
                "on a MacBook with the lid closed the built-in camera stays dark — "
                "open the lid or use another camera"
            ),
        )
    )
    if not ok:
        return checks
    fps, error = fps_probe_fn(config)
    if fps < 0:
        checks.append(
            Check(
                "camera_fps",
                "fail",
                f"probe failed: {error}",
                "check camera permission and that no other app holds the camera",
            )
        )
    else:
        checks.append(
            Check(
                "camera_fps",
                "ok" if fps >= 20 else "warn",
                f"{fps:.1f} processed fps over 5 s",
                "" if fps >= 20 else "close other camera apps or lower camera.width/height",
            )
        )
    return checks


def run_checks(
    config: Config, *, camera_probe: bool = False, lock_held: bool | None = None
) -> list[Check]:
    """Every check the doctor runs, in the order both front ends print them."""
    return (
        endpoint_checks(config)
        + camera_checks(config, camera_probe, lock_held)
        + make_claude_adapter(config).verify()
        + make_cursor_adapter(config).verify()
    )


def overall_ok(checks: list[Check]) -> bool:
    """A warn is a hint, not a failure: only a failed check sinks the run."""
    return all(check.status != "fail" for check in checks)
