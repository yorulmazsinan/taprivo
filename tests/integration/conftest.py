from __future__ import annotations

import socket
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest

from taprivo import paths
from taprivo.config import Config, ServerConfig
from taprivo.core.energy import EnergyEngine
from taprivo.mcp.server import McpServerThread
from taprivo.simulator import Simulator


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@dataclass
class RunningServer:
    config: Config
    engine: EnergyEngine
    simulator: Simulator
    token: str
    thread: McpServerThread

    @property
    def url(self) -> str:
        return self.config.endpoint_url

    @property
    def origin(self) -> str:
        return f"http://127.0.0.1:{self.config.server.port}"

    def headers(self, token: str | None = None) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {token or self.token}",
            "Origin": self.origin,
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }


@pytest.fixture
def running_server(taprivo_home: Path) -> Iterator[RunningServer]:
    config = Config(server=ServerConfig(port=free_port(), rate_limit_per_second=1000))
    engine = EnergyEngine(config)
    simulator = Simulator(engine, config)
    simulator.start()
    token = paths.read_or_create_token()
    thread = McpServerThread(engine, config, token)
    thread.start()
    assert thread.wait_ready(10.0), "server did not start"
    assert engine.snapshot().mcp == "ready", engine.snapshot().mcp_error
    yield RunningServer(config, engine, simulator, token, thread)
    thread.stop()
