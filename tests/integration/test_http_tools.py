from __future__ import annotations

import socket
import time
from pathlib import Path

import pytest

from taprivo import paths
from taprivo.config import Config, ServerConfig
from taprivo.core.energy import EnergyEngine
from taprivo.core.events import Finger
from taprivo.mcp.client import LocalClient, NotRunningError, UnauthorizedError, for_config, run_sync
from taprivo.mcp.server import TOOL_NAMES, McpServerThread
from tests.integration.conftest import RunningServer, free_port


def client_for(server: RunningServer, token: str | None = None) -> LocalClient:
    return LocalClient(server.url, token or server.token, server.origin)


def test_list_and_call_over_http(running_server: RunningServer) -> None:
    running_server.simulator.tap(Finger.INDEX)
    client = client_for(running_server)
    assert run_sync(client.tool_names()) == list(TOOL_NAMES)
    energy = run_sync(client.call("get_energy", {}))
    assert energy["available"] == 10


def test_spend_over_http_is_idempotent(running_server: RunningServer) -> None:
    for _ in range(5):
        running_server.simulator.tap(Finger.RING)
    client = client_for(running_server)
    args = {
        "amount": 30,
        "reason": "http test",
        "request_id": "http-1",
        "session_id": running_server.engine.session_id,
    }
    first = run_sync(client.call("spend_energy", args))
    second = run_sync(client.call("spend_energy", args))
    assert first["success"] is True and first == second
    assert running_server.engine.snapshot().spent == 30


def test_for_config_reads_token_file(running_server: RunningServer) -> None:
    client = for_config(running_server.config)
    assert (
        run_sync(client.call("get_session", {}))["session_id"] == running_server.engine.session_id
    )


def test_wrong_token_raises_unauthorized(running_server: RunningServer) -> None:
    with pytest.raises(UnauthorizedError):
        run_sync(client_for(running_server, token="bad").call("get_energy", {}))


def test_not_running_raises(taprivo_home: Path) -> None:
    port = free_port()
    client = LocalClient(f"http://127.0.0.1:{port}/mcp", "t", f"http://127.0.0.1:{port}")
    with pytest.raises(NotRunningError):
        run_sync(client.call("get_energy", {}))


def test_port_in_use_reports_error(taprivo_home: Path) -> None:
    blocker = socket.socket()
    blocker.bind(("127.0.0.1", 0))
    blocker.listen(1)
    port = blocker.getsockname()[1]
    config = Config(server=ServerConfig(port=port))
    engine = EnergyEngine(config)
    thread = McpServerThread(engine, config, paths.read_or_create_token())
    try:
        thread.start()
        assert thread.wait_ready(10.0)
        snap = engine.snapshot()
        assert snap.mcp == "error"
        assert snap.mcp_error is not None and "already in use" in snap.mcp_error
    finally:
        thread.stop()
        blocker.close()


def test_stop_before_ready_returns_promptly(taprivo_home: Path) -> None:
    config = Config(server=ServerConfig(port=free_port()))
    engine = EnergyEngine(config)
    thread = McpServerThread(engine, config, paths.read_or_create_token())
    thread.start()
    started = time.monotonic()
    thread.stop(timeout=5.0)
    elapsed = time.monotonic() - started
    assert not thread.is_alive()
    assert elapsed < 10.0
