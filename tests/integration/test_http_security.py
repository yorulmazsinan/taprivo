from __future__ import annotations

import json

import httpx2
import pytest

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
STATUSLINE = json.dumps({"model": {"display_name": "Opus"}})

#: Every route behind the guard, with a body that route accepts. The guard must
#: answer identically on all of them, so each security test runs over the pair.
ROUTES = [("/mcp", INIT), ("/statusline", STATUSLINE)]
ROUTE_IDS = [path for path, _ in ROUTES]
route = pytest.mark.parametrize(("path", "body"), ROUTES, ids=ROUTE_IDS)


def post(
    server: RunningServer,
    path: str = "/mcp",
    body: str = INIT,
    **header_overrides: str | None,
) -> httpx2.Response:
    headers = server.headers()
    for key, value in header_overrides.items():
        if value is None:
            headers.pop(key, None)
        else:
            headers[key] = value
    return httpx2.post(server.origin + path, content=body, headers=headers, timeout=5)


@route
def test_valid_request_passes(running_server: RunningServer, path: str, body: str) -> None:
    assert post(running_server, path, body).status_code == 200


@route
def test_missing_token_is_401(running_server: RunningServer, path: str, body: str) -> None:
    response = post(running_server, path, body, Authorization=None)
    assert response.status_code == 401
    assert response.headers["www-authenticate"].startswith("Bearer")


@route
def test_wrong_token_is_401(running_server: RunningServer, path: str, body: str) -> None:
    assert post(running_server, path, body, Authorization="Bearer nope").status_code == 401


@route
def test_non_ascii_token_is_401(running_server: RunningServer, path: str, body: str) -> None:
    # httpx2 rejects a non-ASCII `str` header value outright, so the bytes go
    # in directly; the server must decode and reject them without raising.
    headers = running_server.headers()
    headers["Authorization"] = b"Bearer \xc3\xa9token"  # type: ignore[assignment]
    url = running_server.origin + path
    assert httpx2.post(url, content=body, headers=headers, timeout=5).status_code == 401


@route
def test_bad_origin_is_403(running_server: RunningServer, path: str, body: str) -> None:
    assert post(running_server, path, body, Origin="http://evil.example").status_code == 403


@route
def test_missing_origin_is_allowed(running_server: RunningServer, path: str, body: str) -> None:
    assert post(running_server, path, body, Origin=None).status_code == 200


@route
def test_bad_host_is_421(running_server: RunningServer, path: str, body: str) -> None:
    assert post(running_server, path, body, Host="evil.example").status_code == 421


@route
def test_oversized_body_is_413(running_server: RunningServer, path: str, body: str) -> None:
    big = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "ping", "pad": "x" * 70_000})
    response = httpx2.post(
        running_server.origin + path, content=big, headers=running_server.headers(), timeout=5
    )
    assert response.status_code == 413


@route
def test_no_cors_headers(running_server: RunningServer, path: str, body: str) -> None:
    assert "access-control-allow-origin" not in post(running_server, path, body).headers


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
