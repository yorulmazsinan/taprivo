"""Combo counter: consecutive taps within a timeout."""

from __future__ import annotations


class ComboTracker:
    def __init__(self, timeout_ms: int, enabled: bool) -> None:
        self._timeout_ms = timeout_ms
        self._enabled = enabled
        self._count = 0
        self._last_ts: int | None = None

    @property
    def count(self) -> int:
        return self._count

    def record(self, ts_ms: int) -> int:
        if not self._enabled:
            return 0
        if self._last_ts is not None and ts_ms - self._last_ts <= self._timeout_ms:
            self._count += 1
        else:
            self._count = 1
        self._last_ts = ts_ms
        return self._count

    def reset(self) -> None:
        self._count = 0
        self._last_ts = None
