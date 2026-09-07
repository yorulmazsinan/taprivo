"""Loopback MCP client used by the CLI and tests."""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from typing import Any

import httpx2
from mcp.client import Client
from mcp.client.streamable_http import streamable_http_client

from taprivo import paths
from taprivo.config import Config


class ClientError(Exception):
    """Base error for local client failures."""


class NotRunningError(ClientError):
    """The Taprivo app is not listening on the configured endpoint."""


class UnauthorizedError(ClientError):
    """The stored token was rejected."""


class TokenMissingError(ClientError):
    """No token file exists yet."""


class LocalClient:
    def __init__(self, url: str, token: str, origin: str) -> None:
        self._url = url
        self._headers = {"Authorization": f"Bearer {token}", "Origin": origin}

    async def tool_names(self) -> list[str]:
        async with self._session() as client:
            listed = await client.list_tools()
            return [tool.name for tool in listed.tools]

    async def call(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        async with self._session() as client:
            result = await client.call_tool(name, args)
        if result.is_error:
            raise ClientError(f"tool {name} failed")
        return dict(result.structured_content or {})

    def _session(self) -> _Session:
        return _Session(self._url, self._headers)


class _Session:
    def __init__(self, url: str, headers: dict[str, str]) -> None:
        self._url = url
        self._headers = headers
        self._http: httpx2.AsyncClient | None = None
        self._client: Client | None = None

    async def __aenter__(self) -> Client:
        self._http = httpx2.AsyncClient(
            headers=self._headers, timeout=httpx2.Timeout(5.0, read=30.0)
        )
        try:
            await self._http.__aenter__()
            transport = streamable_http_client(self._url, http_client=self._http)
            self._client = Client(transport)
            await self._client.__aenter__()
        except Exception as exc:
            status = await _probe_status(self._http, self._url)
            await self._http.__aexit__(None, None, None)
            raise _translate(exc, status) from exc
        return self._client

    async def __aexit__(self, *exc_info: object) -> None:
        try:
            if self._client is not None:
                await self._client.__aexit__(None, None, None)
        finally:
            if self._http is not None:
                await self._http.__aexit__(None, None, None)


# Total budget for the diagnostic probe below, independent of the
# happy-path client's longer read timeout -- a failing call must not block
# for up to 30s just to classify the failure.
_PROBE_TIMEOUT = httpx2.Timeout(2.0)


async def _probe_status(http: httpx2.AsyncClient, url: str) -> int | None:
    """Best-effort raw HTTP probe to recover the status code an ambiguous
    transport failure hid.

    Runs only after ``Client.__aenter__`` has already failed, on the
    existing ``http`` connection but with its own short, dedicated
    ``_PROBE_TIMEOUT`` (2s total) so a failing ``LocalClient.call()`` cannot
    inherit the happy-path ``Timeout(5.0, read=30.0)`` and block for up to
    ~60s. Its result reflects server state at probe time, a moment after
    the original request -- it is not a replay of that request, does not
    confirm anything about the original request's body (it sends a minimal
    placeholder body, so it cannot confirm a 413), and any exception raised
    while probing is swallowed and reported as ``None``. Callers must treat
    the result as an inference, not a fact: only a 401 is acted on directly
    (see ``_translate``); every other status, including ``None``, falls
    through to the existing text/type heuristics. The probe never logs or
    includes the bearer token in any returned value or error message -- it
    only returns a bare status code.

    The streamable-HTTP client collapses every HTTP-level 4xx rejection whose
    body is not a JSON-RPC error (our security middleware replies with plain
    text) into the same generic ``MCPError(-32603, "Server returned an error
    response")`` -- see ``mcp.client.streamable_http._handle_post_request``
    and the ``mcp.client._probe`` module docstring. That collapses 401, 403,
    413, 421 and 429 into one indistinguishable exception, so the real status
    can only be recovered by asking the server again directly.
    """
    try:
        response = await http.post(url, content=b"{}", timeout=_PROBE_TIMEOUT)
    except Exception:
        return None
    return response.status_code


def _translate(exc: BaseException, status: int | None = None) -> ClientError:
    if status == 401:
        return UnauthorizedError("the stored token was rejected by the running app")
    text = str(exc)
    if (
        isinstance(exc, httpx2.ConnectError)
        or "Connection refused" in text
        or "connect" in text.lower()
    ):
        return NotRunningError("Taprivo is not running on the configured endpoint")
    if "401" in text or "Unauthorized" in text:
        return UnauthorizedError("the stored token was rejected by the running app")
    if isinstance(exc, ExceptionGroup):
        for inner in exc.exceptions:
            translated = _translate(inner, status)
            if type(translated) is not ClientError:
                return translated
    return ClientError(text)


def for_config(config: Config) -> LocalClient:
    token_file = paths.token_path()
    if not token_file.exists():
        raise TokenMissingError("no token file; run 'taprivo' once or 'taprivo setup claude'")
    token = token_file.read_text("utf-8").strip()
    return LocalClient(config.endpoint_url, token, f"http://127.0.0.1:{config.server.port}")


def run_sync[T](coro: Coroutine[Any, Any, T]) -> T:
    return asyncio.run(coro)
