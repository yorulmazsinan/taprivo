"""Loopback guard: host/origin allowlist, bearer auth, body limit, rate limit."""

from __future__ import annotations

import hmac
import time
from collections.abc import Awaitable, Callable, MutableMapping
from typing import Any

Scope = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[MutableMapping[str, Any]]]
Send = Callable[[MutableMapping[str, Any]], Awaitable[None]]
ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]


class TokenBucket:
    def __init__(
        self, rate_per_second: float, burst: int, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self._rate = float(rate_per_second)
        self._burst = float(burst)
        self._clock = clock
        self._tokens = self._burst
        self._last = clock()

    def take(self) -> bool:
        now = self._clock()
        self._tokens = min(self._burst, self._tokens + (now - self._last) * self._rate)
        self._last = now
        if self._tokens >= 1.0:
            self._tokens -= 1.0
            return True
        return False


def _header(scope: Scope, name: bytes) -> str | None:
    headers: list[tuple[bytes, bytes]] = scope.get("headers", [])
    for key, value in headers:
        if key.lower() == name:
            return value.decode("latin-1")
    return None


async def _reply(
    send: Send, status: int, body: str, extra: list[tuple[bytes, bytes]] | None = None
) -> None:
    payload = body.encode("utf-8")
    headers = [
        (b"content-type", b"text/plain; charset=utf-8"),
        (b"content-length", str(len(payload)).encode()),
    ]
    await send(
        {"type": "http.response.start", "status": status, "headers": headers + (extra or [])}
    )
    await send({"type": "http.response.body", "body": payload})


class LocalGuardMiddleware:
    def __init__(
        self,
        app: ASGIApp,
        *,
        token: str,
        allowed_hosts: set[str],
        allowed_origins: set[str],
        rate_per_second: int,
        burst: int = 40,
        max_body_bytes: int = 65536,
    ) -> None:
        self._app = app
        self._token = token.encode("utf-8")
        self._hosts = allowed_hosts
        self._origins = allowed_origins
        self._bucket = TokenBucket(rate_per_second, burst)
        self._max_body = max_body_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return
        host = _header(scope, b"host")
        if host not in self._hosts:
            await _reply(send, 421, "Invalid Host header")
            return
        origin = _header(scope, b"origin")
        if origin is not None and origin not in self._origins:
            await _reply(send, 403, "Invalid Origin header")
            return
        length = _header(scope, b"content-length")
        if length is not None and length.isdigit() and int(length) > self._max_body:
            await _reply(send, 413, "Request body too large")
            return
        auth = _header(scope, b"authorization") or ""
        presented = auth[7:] if auth.lower().startswith("bearer ") else ""
        if not presented or not hmac.compare_digest(presented.encode("latin-1"), self._token):
            await _reply(
                send, 401, "Unauthorized", [(b"www-authenticate", b'Bearer realm="taprivo"')]
            )
            return
        if not self._bucket.take():
            await _reply(send, 429, "Too many requests", [(b"retry-after", b"1")])
            return
        await self._app(scope, receive, send)
