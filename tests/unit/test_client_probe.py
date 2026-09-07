from __future__ import annotations

from typing import Any

import httpx2

from taprivo.mcp.client import _PROBE_TIMEOUT, _probe_status


class _FakeResponse:
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code


class _RecordingHttp:
    """Stand-in for httpx2.AsyncClient that records the kwargs it was called with."""

    def __init__(
        self, response: _FakeResponse | None = None, error: Exception | None = None
    ) -> None:
        self.calls: list[dict[str, Any]] = []
        self._response = response
        self._error = error

    async def post(self, url: str, **kwargs: Any) -> _FakeResponse:
        self.calls.append({"url": url, **kwargs})
        if self._error is not None:
            raise self._error
        assert self._response is not None
        return self._response


async def test_probe_uses_short_timeout() -> None:
    http = _RecordingHttp(response=_FakeResponse(401))
    status = await _probe_status(http, "http://127.0.0.1:1/mcp")  # type: ignore[arg-type]
    assert status == 401
    assert len(http.calls) == 1
    timeout = http.calls[0]["timeout"]
    assert timeout is _PROBE_TIMEOUT
    assert isinstance(timeout, httpx2.Timeout)
    # Bounded independently of the happy-path client's 30s read timeout.
    assert timeout.connect == 2.0
    assert timeout.read == 2.0


async def test_probe_returns_none_on_error() -> None:
    http = _RecordingHttp(error=RuntimeError("boom"))
    status = await _probe_status(http, "http://127.0.0.1:1/mcp")  # type: ignore[arg-type]
    assert status is None
