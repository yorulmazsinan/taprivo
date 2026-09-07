"""Taprivo command line interface."""

from __future__ import annotations

import json
from collections.abc import Callable, Coroutine
from typing import Any

import typer

from taprivo import __version__
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

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="Taprivo: turn finger taps into a Motion Energy budget for AI coding agents.",
)

JSON_OPTION = typer.Option(False, "--json", help="Machine-readable JSON output.")
NOT_RUNNING_HINT = "Taprivo is not running. Start it with 'taprivo' or 'taprivo simulate'."


def emit_json(payload: dict[str, Any]) -> None:
    typer.echo(json.dumps({"schema_version": 1, **payload}, indent=2, sort_keys=True))


def fail(message: str, json_output: bool, code: int = 1) -> None:
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
        raise AssertionError("unreachable") from exc


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
    raise AssertionError("unreachable")


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"taprivo {__version__}")
        raise typer.Exit()


@app.callback()
def root(
    version: bool = typer.Option(
        False, "--version", callback=_version_callback, is_eager=True, help="Show version."
    ),
) -> None:
    """Taprivo command line."""


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
    typer.echo(f"Rate:      {data['taps_per_minute']} taps/min, combo x{data['combo']}")
    last = data["last_spend"]
    last_text = f"{last['amount']} for '{last['reason']}' at {last['at_utc']}" if last else "none"
    typer.echo(f"Spends:    {data['spend_count']} (last: {last_text})")


def main() -> None:
    app()
