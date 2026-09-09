"""Taprivo command line interface."""

from __future__ import annotations

import json
from collections.abc import Callable, Coroutine, Sequence
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, NoReturn

import typer

from taprivo import __version__, paths
from taprivo.adapters import make_claude_adapter, make_cursor_adapter
from taprivo.adapters.base import AgentAdapter, SetupError, SetupOptions, SetupPlan
from taprivo.config import Config, ConfigError, load_config
from taprivo.core.stats_sink import DailyRow
from taprivo.core.stats_store import StatsStore
from taprivo.doctor import CAMERA_SETTINGS_HINT, overall_ok, run_checks
from taprivo.mcp.client import (
    ClientError,
    LocalClient,
    NotRunningError,
    TokenMissingError,
    UnauthorizedError,
    for_config,
    run_sync,
)
from taprivo.vision.camera import default_device, list_devices

app = typer.Typer(
    add_completion=False,
    invoke_without_command=True,
    help="Taprivo: turn finger taps into a Motion Energy budget for AI coding agents.",
)

JSON_OPTION = typer.Option(False, "--json", help="Machine-readable JSON output.")
# En dash: "nothing measured yet", as opposed to a measured zero.
NO_VALUE = "–"  # noqa: RUF001
NOT_RUNNING_HINT = "Taprivo is not running. Start it with 'taprivo' or 'taprivo simulate'."
NO_STATS_HINT = "No statistics file yet. Run Taprivo once with stats.enabled: true to create it."
HISTORY_HEADER = "Day         Sessions  Taps  Generated  Spent  Active"


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
    """Open the HUD with keyboard mode running (no camera needed)."""
    _launch(True)


camera_app = typer.Typer(help="Camera devices.")
app.add_typer(camera_app, name="camera")

list_devices_fn = list_devices


@camera_app.command("list")
def camera_list(json_output: bool = JSON_OPTION) -> None:
    """List camera devices with name, kind, resolution and signal state."""
    devices = list_devices_fn()
    if json_output:
        emit_json({"ok": bool(devices), "devices": [asdict(d) for d in devices]})
        if not devices:
            raise typer.Exit(1)
        return
    if not devices:
        fail(f"No camera device found. Check {CAMERA_SETTINGS_HINT}.", False)
    config = load_config_or_exit(False)
    default = default_device(devices, prefer_builtin=config.camera.prefer_builtin)
    for d in devices:
        marker = " (default)" if default is not None and d.index == default.index else ""
        state = "signal" if d.has_signal else "no signal"
        if not d.probed:
            state = "not probed"
        name = d.name or f"Camera {d.index}"
        typer.echo(f"[{d.index}] {name} — {d.kind}, {d.width}x{d.height}, {state}{marker}")


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


def _daily_json(row: DailyRow) -> dict[str, Any]:
    return asdict(row)


def _hhmm(seconds: int) -> str:
    return f"{seconds // 3600:02d}:{seconds % 3600 // 60:02d}"


def open_store_or_exit(config: Config, json_output: bool) -> StatsStore:
    """Open the statistics file for reading; the running app is not needed."""
    path = paths.stats_path(config.stats.path)
    if not path.exists():
        fail(NO_STATS_HINT, json_output)
    return StatsStore(path)


def _today_row(config: Config) -> DailyRow | None:
    """Today's totals, or None when the file is missing or has no row yet."""
    path = paths.stats_path(config.stats.path)
    if not config.stats.enabled or not path.exists():
        return None
    store = StatsStore(path)
    try:
        return store.today(datetime.now(UTC).date().isoformat())
    finally:
        store.close()


def _print_today(row: DailyRow) -> None:
    typer.echo(
        f"Today:     {row.sessions} sessions, {row.taps_total} taps, "
        f"+{row.generated} / -{row.spent}"
    )


def _stats_today(config: Config, json_output: bool) -> None:
    store = open_store_or_exit(config, json_output)
    try:
        row = store.today(datetime.now(UTC).date().isoformat())
    finally:
        store.close()
    if json_output:
        emit_json({"ok": True, "today": _daily_json(row) if row else None})
        return
    if row is None:
        typer.echo("No statistics for today yet.")
        return
    _print_today(row)
    typer.echo(f"Active:    {_hhmm(row.active_seconds)}")


def _stats_history(config: Config, json_output: bool, days: int) -> None:
    store = open_store_or_exit(config, json_output)
    try:
        rows = store.history(days)
    finally:
        store.close()
    if json_output:
        emit_json({"ok": True, "days": days, "history": [_daily_json(row) for row in rows]})
        return
    if not rows:
        typer.echo("No statistics recorded yet.")
        return
    typer.echo(HISTORY_HEADER)
    for row in rows:
        typer.echo(
            f"{row.day:<10}  {row.sessions:>8}  {row.taps_total:>4}  {row.generated:>9}  "
            f"{row.spent:>5}  {_hhmm(row.active_seconds)}"
        )


def _rhythm_text(data: dict[str, Any]) -> str:
    bpm = float(data.get("bpm", 0.0))
    if bpm <= 0:
        return NO_VALUE
    if data.get("rhythm_steady"):
        steady_multiplier = float(data.get("rhythm_multiplier", 1.0))
        return f"{round(bpm)} BPM (steady, {steady_multiplier:g}×)"
    return f"{round(bpm)} BPM"


@app.command()
def stats(
    json_output: bool = JSON_OPTION,
    today: bool = typer.Option(False, "--today", help="Totals for today, read from disk."),
    history: bool = typer.Option(False, "--history", help="Daily totals, read from disk."),
    days: int = typer.Option(30, "--days", help="How many recorded days --history shows."),
) -> None:
    """Show tap and spend statistics for the current session.

    --today and --history read the local statistics file instead, so they work
    while Taprivo is not running.
    """
    config = load_config_or_exit(json_output)
    if today and history:
        fail("Use either --today or --history, not both.", json_output, code=2)
    if today:
        _stats_today(config, json_output)
        return
    if history:
        _stats_history(config, json_output, days)
        return
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
    typer.echo(f"Rhythm:    {_rhythm_text(data)}")
    last = data["last_spend"]
    last_text = f"{last['amount']} for '{last['reason']}' at {last['at_utc']}" if last else "none"
    typer.echo(f"Spends:    {data['spend_count']} (last: {last_text})")
    row = _today_row(config)
    if row is not None:
        _print_today(row)


setup_app = typer.Typer(help="Connect an AI coding agent to Taprivo.")
remove_app = typer.Typer(help="Disconnect an AI coding agent from Taprivo.")
app.add_typer(setup_app, name="setup")
app.add_typer(remove_app, name="remove")


def _print_plan(plan: SetupPlan, dry_run: bool) -> None:
    title = "Dry run: planned actions" if dry_run else "Planned actions"
    typer.echo(f"{title}:")
    for index, action in enumerate(plan.actions, 1):
        typer.echo(f"  {index}. {action.description}")
    for note in plan.notes:
        typer.echo(f"  note: {note}")


def _apply_plan(plan: SetupPlan, json_output: bool = False) -> list[str]:
    summaries: list[str] = []
    for action in plan.actions:
        try:
            summary = action.apply()
        except SetupError as exc:
            fail(str(exc), json_output)
        summaries.append(summary)
        if json_output:
            continue
        typer.echo(f"OK   {action.description}")
        for line in summary.splitlines():
            typer.echo(f"     {line}")
    return summaries


def _plan_payload(
    agent: str, plan: SetupPlan, dry_run: bool, summaries: list[str]
) -> dict[str, Any]:
    return {
        "ok": True,
        "agent": agent,
        "dry_run": dry_run,
        "actions": [
            {
                "kind": action.kind,
                "description": action.description,
                "summary": summaries[index] if index < len(summaries) else None,
            }
            for index, action in enumerate(plan.actions)
        ],
        "notes": list(plan.notes),
    }


def _run_setup(
    adapter: AgentAdapter,
    options: SetupOptions,
    *,
    dry_run: bool,
    json_output: bool,
    follow_up: Sequence[str] = (),
) -> None:
    plan = adapter.plan_setup(options)
    if not json_output:
        _print_plan(plan, dry_run)
    summaries = [] if dry_run else _apply_plan(plan, json_output)
    if json_output:
        emit_json(_plan_payload(adapter.name, plan, dry_run, summaries))
        return
    if dry_run:
        return
    for line in follow_up:
        typer.echo(line)


def _run_remove(adapter: AgentAdapter, options: SetupOptions, *, json_output: bool) -> None:
    plan = adapter.plan_remove(options)
    if not json_output:
        _print_plan(plan, False)
    summaries = _apply_plan(plan, json_output)
    if json_output:
        emit_json(_plan_payload(adapter.name, plan, False, summaries))


@setup_app.command("claude")
def setup_claude(
    project: bool = typer.Option(False, "--project", help="Also add Taprivo to ./.mcp.json."),
    install_instructions: bool = typer.Option(
        False,
        "--install-instructions",
        help="Import the instruction file from ~/.claude/CLAUDE.md.",
    ),
    dry_run: bool = typer.Option(False, "--dry-run", help="Show planned changes without writing."),
    json_output: bool = JSON_OPTION,
) -> None:
    """Register Taprivo with Claude Code (user scope) and write agent instructions."""
    config = load_config_or_exit(json_output)
    adapter = make_claude_adapter(config)
    if not adapter.detect().found:
        fail(
            "Claude Code CLI ('claude') not found on PATH. Install Claude Code first.", json_output
        )
    follow_up = ["", "Next: start Taprivo with 'taprivo simulate', then run 'taprivo doctor'."]
    if not install_instructions:
        follow_up.append(
            "Optional: 'taprivo setup claude --install-instructions' adds the budget rules to "
            "~/.claude/CLAUDE.md."
        )
    _run_setup(
        adapter,
        SetupOptions(
            project=project, install_instructions=install_instructions, project_dir=Path.cwd()
        ),
        dry_run=dry_run,
        json_output=json_output,
        follow_up=follow_up,
    )


@remove_app.command("claude")
def remove_claude(
    project: bool = typer.Option(False, "--project", help="Also remove Taprivo from ./.mcp.json."),
    json_output: bool = JSON_OPTION,
) -> None:
    """Remove Taprivo's Claude Code registration and instruction files."""
    config = load_config_or_exit(json_output)
    _run_remove(
        make_claude_adapter(config),
        SetupOptions(project=project, project_dir=Path.cwd()),
        json_output=json_output,
    )


@setup_app.command("cursor")
def setup_cursor(
    project: bool = typer.Option(
        False, "--project", help="Also add Taprivo to ./.cursor/mcp.json."
    ),
    install_instructions: bool = typer.Option(
        False,
        "--install-instructions",
        help="Write ~/.cursor/taprivo.md and, with --project, a .cursor/rules/taprivo.mdc rule.",
    ),
    dry_run: bool = typer.Option(False, "--dry-run", help="Show planned changes without writing."),
    json_output: bool = JSON_OPTION,
) -> None:
    """Register Taprivo with Cursor (user scope) and write agent instructions."""
    config = load_config_or_exit(json_output)
    follow_up = ["", "Next: start Taprivo with 'taprivo simulate', then run 'taprivo doctor'."]
    if not install_instructions:
        follow_up.append(
            "Optional: 'taprivo setup cursor --install-instructions' writes the budget rules to "
            "~/.cursor/taprivo.md."
        )
    _run_setup(
        make_cursor_adapter(config),
        SetupOptions(
            project=project, install_instructions=install_instructions, project_dir=Path.cwd()
        ),
        dry_run=dry_run,
        json_output=json_output,
        follow_up=follow_up,
    )


@remove_app.command("cursor")
def remove_cursor(
    project: bool = typer.Option(
        False, "--project", help="Also remove Taprivo from ./.cursor/mcp.json."
    ),
    json_output: bool = JSON_OPTION,
) -> None:
    """Remove Taprivo's Cursor registration and instruction files."""
    config = load_config_or_exit(json_output)
    _run_remove(
        make_cursor_adapter(config),
        SetupOptions(project=project, project_dir=Path.cwd()),
        json_output=json_output,
    )


@app.command()
def doctor(
    json_output: bool = JSON_OPTION,
    camera_probe: bool = typer.Option(
        False,
        "--camera-probe",
        help="Open the camera to check permission and measure processed fps for 5 s.",
    ),
) -> None:
    """Diagnose the local endpoint, token and agent integrations.

    Lists camera devices (briefly opens each index, bar an iPhone Continuity
    Camera, to detect a signal); --camera-probe additionally checks permission
    and measures fps.
    """
    config = load_config_or_exit(json_output)
    checks = run_checks(config, camera_probe=camera_probe)
    ok = overall_ok(checks)
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
