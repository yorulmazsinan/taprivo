from __future__ import annotations

import json

import httpx2

from tests.integration.conftest import RunningServer

INIT = json.dumps(
    {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-11-25",
            "capabilities": {},
            "clientInfo": {"name": "test", "version": "0"},
        },
    }
)


def post(server: RunningServer, **header_overrides: str | None) -> httpx2.Response:
    headers = server.headers()
    for key, value in header_overrides.items():
        if value is None:
            headers.pop(key, None)
        else:
            headers[key] = value
    return httpx2.post(server.url, content=INIT, headers=headers, timeout=5)


def test_valid_request_passes(running_server: RunningServer) -> None:
    assert post(running_server).status_code == 200


def test_missing_token_is_401(running_server: RunningServer) -> None:
    response = post(running_server, Authorization=None)
    assert response.status_code == 401
    assert response.headers["www-authenticate"].startswith("Bearer")


def test_wrong_token_is_401(running_server: RunningServer) -> None:
    assert post(running_server, Authorization="Bearer nope").status_code == 401


def test_non_ascii_token_is_401(running_server: RunningServer) -> None:
    # httpx2 rejects a non-ASCII `str` header value outright, so the bytes go
    # in directly; the server must decode and reject them without raising.
    headers = running_server.headers()
    headers["Authorization"] = b"Bearer \xc3\xa9token"  # type: ignore[assignment]
    response = httpx2.post(running_server.url, content=INIT, headers=headers, timeout=5)
    assert response.status_code == 401


def test_bad_origin_is_403(running_server: RunningServer) -> None:
    assert post(running_server, Origin="http://evil.example").status_code == 403


def test_missing_origin_is_allowed(running_server: RunningServer) -> None:
    assert post(running_server, Origin=None).status_code == 200


def test_bad_host_is_421(running_server: RunningServer) -> None:
    assert post(running_server, Host="evil.example").status_code == 421


def test_oversized_body_is_413(running_server: RunningServer) -> None:
    big = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "ping", "pad": "x" * 70_000})
    response = httpx2.post(
        running_server.url, content=big, headers=running_server.headers(), timeout=5
    )
    assert response.status_code == 413


def test_no_cors_headers(running_server: RunningServer) -> None:
    response = post(running_server)
    assert "access-control-allow-origin" not in response.headers


def test_rate_limit_is_429(taprivo_home: object) -> None:
    from taprivo import paths
    from taprivo.config import Config, ServerConfig
    from taprivo.core.energy import EnergyEngine
    from taprivo.mcp.server import McpServerThread
    from taprivo.simulator import Simulator
    from tests.integration.conftest import free_port

    config = Config(server=ServerConfig(port=free_port(), rate_limit_per_second=1))
    engine = EnergyEngine(config)
    token = paths.read_or_create_token()
    thread = McpServerThread(engine, config, token)
    thread.start()
    assert thread.wait_ready(10.0)
    server = RunningServer(config, engine, Simulator(engine, config), token, thread)
    try:
        statuses = [post(server).status_code for _ in range(45)]
    finally:
        thread.stop()
    assert statuses[:40] == [200] * 40
    assert 429 in statuses[40:]
