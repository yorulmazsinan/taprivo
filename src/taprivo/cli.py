"""Taprivo command line interface."""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Coroutine
from dataclasses import asdict
from pathlib import Path
from typing import Any, NoReturn

import typer

from taprivo import __version__, paths
from taprivo.adapters.base import Check, SetupError, SetupOptions, SetupPlan
from taprivo.adapters.claude import ClaudeAdapter
from taprivo.config import Config, ConfigError, load_config
from taprivo.mcp.client import (
    ClientError,
    LocalClient,
    NotRunningError,
    TokenMissingError,
    UnauthorizedError,
    for_config,
    run_sync,
)
from taprivo.mcp.server import TOOL_NAMES, port_is_free
from taprivo.vision import tracker as vision_tracker
from taprivo.vision.camera import CameraError, CameraSource, default_device, list_devices

app = typer.Typer(
    add_completion=False,
    invoke_without_command=True,
    help="Taprivo: turn finger taps into a Motion Energy budget for AI coding agents.",
)

JSON_OPTION = typer.Option(False, "--json", help="Machine-readable JSON output.")
NOT_RUNNING_HINT = "Taprivo is not running. Start it with 'taprivo' or 'taprivo simulate'."
CAMERA_SETTINGS_HINT = "System Settings > Privacy & Security > Camera"


def emit_json(payload: dict[str, Any]) -> None:
    typer.echo(json.dumps({"schema_version": 1, **payload}, indent=2, sort_keys=True))


def fail(message: str, json_output: bool, code: int = 1) -> NoReturn:
    if json_output:
        emit_json({"ok": False, "error": message})
    else:
        typer.echo(f"Error: {message}", err=True)
    raise typer.Exit(code)


def load_config_or_exit(json_output: bool) -> Config:
    try:
        return load_config()
    except ConfigError as exc:
        fail(str(exc), json_output, code=2)


def query(
    config: Config,
    json_output: bool,
    fetch: Callable[[LocalClient], Coroutine[Any, Any, dict[str, Any]]],
) -> dict[str, Any]:
    try:
        client = for_config(config)
        return run_sync(fetch(client))
    except TokenMissingError:
        fail("No token file yet. Start Taprivo once with 'taprivo simulate'.", json_output)
    except NotRunningError:
        fail(NOT_RUNNING_HINT, json_output)
    except UnauthorizedError:
        fail("The stored token was rejected. Quit Taprivo and run 'taprivo doctor'.", json_output)
    except ClientError as exc:
        fail(str(exc), json_output)


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"taprivo {__version__}")
        raise typer.Exit()


def _launch(start_simulator: bool, open_camera: bool = False) -> None:
    config = load_config_or_exit(False)
    from taprivo.ui.app import run_app  # imported lazily so CLI-only commands stay light

    raise typer.Exit(run_app(config, start_simulator=start_simulator, open_camera=open_camera))


@app.callback()
def root(
    ctx: typer.Context,
    version: bool = typer.Option(
        False, "--version", callback=_version_callback, is_eager=True, help="Show version."
    ),
) -> None:
    """Taprivo command line. Run without a subcommand to open the HUD."""
    if ctx.invoked_subcommand is None:
        _launch(False)


@app.command()
def simulate() -> None:
    """Open the HUD with the keyboard simulator running (no camera needed)."""
    _launch(True)


camera_app = typer.Typer(help="Camera devices.")
app.add_typer(camera_app, name="camera")

list_devices_fn = list_devices


def camera_probe_fn(config: Config) -> tuple[bool, str]:
    devices = list_devices_fn()
    device = default_device(devices)
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
    device = default_device(list_devices_fn())
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


@camera_app.command("list")
def camera_list(json_output: bool = JSON_OPTION) -> None:
    """List camera devices with resolution and signal state."""
    devices = list_devices_fn()
    if json_output:
        emit_json({"ok": bool(devices), "devices": [asdict(d) for d in devices]})
        if not devices:
            raise typer.Exit(1)
        return
    if not devices:
        fail(f"No camera device found. Check {CAMERA_SETTINGS_HINT}.", False)
    default = default_device(devices)
    for d in devices:
        marker = " (default)" if default is not None and d.index == default.index else ""
        typer.echo(f"[{d.index}] {d.label}{marker}")


@app.command()
def calibrate() -> None:
    """Open the Camera window for device selection and calibration."""
    lock = paths.InstanceLock()
    if not lock.acquire():
        fail("Taprivo is running: open the Camera window from the HUD.", False)
    lock.release()
    _launch(False, open_camera=True)


async def _status_payload(client: LocalClient, config: Config) -> dict[str, Any]:
    energy = await client.call("get_energy", {})
    session = await client.call("get_session", {})
    return {
        "ok": True,
        "endpoint": config.endpoint_url,
        "mcp": "ready",
        "session_id": session["session_id"],
        "started_at_utc": session["started_at_utc"],
        "mode": session["mode"],
        "tracking": session["tracking"],
        "available": energy["available"],
        "generated": energy["generated"],
        "overflow": energy["overflow"],
        "spent": energy["spent"],
        "max_energy": energy["max_energy"],
        "last_tool_call_utc": session["last_tool_call_utc"],
        "camera_fps": session["camera_fps"],
        "detection_ratio": session["detection_ratio"],
    }


@app.command()
def status(json_output: bool = JSON_OPTION) -> None:
    """Show balance, tracking state and MCP endpoint of the running app."""
    config = load_config_or_exit(json_output)
    data = query(config, json_output, lambda client: _status_payload(client, config))
    if json_output:
        emit_json(data)
        return
    typer.echo(f"Taprivo {__version__}")
    typer.echo(f"Endpoint:  {data['endpoint']} ({data['mcp']})")
    typer.echo(
        f"Session:   {data['session_id']} ({data['mode']}, started {data['started_at_utc']})"
    )
    typer.echo(f"Tracking:  {data['tracking']}")
    typer.echo(
        f"Energy:    {data['available']} / {data['max_energy']} available "
        f"(generated {data['generated']}, overflow {data['overflow']}, spent {data['spent']})"
    )
    ratio_pct = data["detection_ratio"] * 100
    typer.echo(
        f"Camera:    {data['tracking']}, {data['camera_fps']:.1f} fps, hand {ratio_pct:.0f}%"
    )


async def _stats_payload(client: LocalClient) -> dict[str, Any]:
    stats = await client.call("get_stats", {"scope": "session"})
    return {"ok": True, **stats}


@app.command()
def stats(json_output: bool = JSON_OPTION) -> None:
    """Show tap and spend statistics for the current session."""
    config = load_config_or_exit(json_output)
    data = query(config, json_output, _stats_payload)
    if json_output:
        emit_json(data)
        return
    typer.echo(f"Session {data['session_id']} ({data['session_duration_seconds']} s)")
    fingers = "  ".join(
        f"{name.title()} {count}" for name, count in data["taps_per_finger"].items()
    )
    typer.echo(f"Taps:      {data['taps_total']} ({fingers})")
    hands = data.get("taps_per_hand") or {}
    typer.echo(f"Hands:     left {hands.get('left', 0)}, right {hands.get('right', 0)}")
    typer.echo(f"Rate:      {data['taps_per_minute']} taps/min")
    multiplier = float(data.get("combo_multiplier", 1.0))
    typer.echo(f"Combo:     x{data['combo']} ({multiplier:.1f}×)")
    last = data["last_spend"]
    last_text = f"{last['amount']} for '{last['reason']}' at {last['at_utc']}" if last else "none"
    typer.echo(f"Spends:    {data['spend_count']} (last: {last_text})")


setup_app = typer.Typer(help="Connect an AI coding agent to Taprivo.")
remove_app = typer.Typer(help="Disconnect an AI coding agent from Taprivo.")
app.add_typer(setup_app, name="setup")
app.add_typer(remove_app, name="remove")


def make_claude_adapter(config: Config) -> ClaudeAdapter:
    return ClaudeAdapter(config)


def _print_plan(plan: SetupPlan, dry_run: bool) -> None:
    title = "Dry run: planned actions" if dry_run else "Planned actions"
    typer.echo(f"{title}:")
    for index, action in enumerate(plan.actions, 1):
        typer.echo(f"  {index}. {action.description}")
    for note in plan.notes:
        typer.echo(f"  note: {note}")


def _apply_plan(plan: SetupPlan) -> None:
    for action in plan.actions:
        try:
            summary = action.apply()
        except SetupError as exc:
            fail(str(exc), False)
        typer.echo(f"OK   {action.description}")
        for line in summary.splitlines():
            typer.echo(f"     {line}")


def _setup_claude(project: bool, install_instructions: bool, dry_run: bool) -> None:
    config = load_config_or_exit(False)
    adapter = make_claude_adapter(config)
    if not adapter.detect().found:
        fail("Claude Code CLI ('claude') not found on PATH. Install Claude Code first.", False)
    options = SetupOptions(
        project=project, install_instructions=install_instructions, project_dir=Path.cwd()
    )
    plan = adapter.plan_setup(options)
    _print_plan(plan, dry_run)
    if dry_run:
        return
    _apply_plan(plan)
    typer.echo("")
    typer.echo("Next: start Taprivo with 'taprivo simulate', then run 'taprivo doctor'.")
    if not install_instructions:
        typer.echo(
            "Optional: 'taprivo setup claude --install-instructions' adds the budget rules to "
            "~/.claude/CLAUDE.md."
        )


@setup_app.command("claude")
def setup_claude(
    project: bool = typer.Option(False, "--project", help="Also add Taprivo to ./.mcp.json."),
    install_instructions: bool = typer.Option(
        False,
        "--install-instructions",
        help="Import the instruction file from ~/.claude/CLAUDE.md.",
    ),
    dry_run: bool = typer.Option(False, "--dry-run", help="Show planned changes without writing."),
) -> None:
    """Register Taprivo with Claude Code (user scope) and write agent instructions."""
    _setup_claude(project, install_instructions, dry_run)


@remove_app.command("claude")
def remove_claude(
    project: bool = typer.Option(False, "--project", help="Also remove Taprivo from ./.mcp.json."),
) -> None:
    """Remove Taprivo's Claude Code registration and instruction files."""
    config = load_config_or_exit(False)
    adapter = make_claude_adapter(config)
    plan = adapter.plan_remove(SetupOptions(project=project, project_dir=Path.cwd()))
    _print_plan(plan, False)
    _apply_plan(plan)


def _endpoint_checks(config: Config) -> list[Check]:
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


def _camera_checks(config: Config, probe: bool) -> list[Check]:
    """model/mediapipe checks are metadata-only. Device enumeration (camera_devices)
    briefly opens each camera index to detect a signal, and runs unconditionally.
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
    checks.append(
        Check(
            "camera_devices",
            "ok" if signal else "warn",
            f"{len(devices)} device(s), {len(signal)} with signal",
            "" if signal else "select a camera that shows an image in the Camera window",
        )
    )
    if not probe:
        return checks
    lock = paths.InstanceLock()
    if not lock.acquire():
        checks.append(
            Check(
                "camera_permission",
                "warn",
                "Taprivo is running; camera probe skipped",
                "quit Taprivo and rerun doctor --camera-probe",
            )
        )
        return checks
    lock.release()
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


@app.command()
def doctor(
    json_output: bool = JSON_OPTION,
    camera_probe: bool = typer.Option(
        False,
        "--camera-probe",
        help="Open the camera to check permission and measure processed fps for 5 s.",
    ),
) -> None:
    """Diagnose the local endpoint, token and Claude Code integration.

    Lists camera devices (briefly opens each index to detect a signal);
    --camera-probe additionally checks permission and measures fps.
    """
    config = load_config_or_exit(json_output)
    checks = (
        _endpoint_checks(config)
        + _camera_checks(config, camera_probe)
        + make_claude_adapter(config).verify()
    )
    ok = all(check.status != "fail" for check in checks)
    if json_output:
        emit_json(
            {
                "ok": ok,
                "checks": [
                    {"name": c.name, "status": c.status, "detail": c.detail, "hint": c.hint}
                    for c in checks
                ],
            }
        )
    else:
        for check in checks:
            line = f"{check.status.upper():4} {check.name}: {check.detail}"
            if check.hint and check.status != "ok":
                line += f" -> {check.hint}"
            typer.echo(line)
        typer.echo("All checks passed." if ok else "Some checks failed.")
    raise typer.Exit(0 if ok else 1)


def main() -> None:
    app()
