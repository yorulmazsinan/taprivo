"""Combo counter: consecutive taps within a timeout, and their multiplier."""

from __future__ import annotations

from taprivo.config import ComboTier


class ComboTracker:
    def __init__(
        self,
        timeout_ms: int,
        enabled: bool,
        tiers: tuple[ComboTier, ...] = (),
        multiplier_enabled: bool = False,
    ) -> None:
        self._timeout_ms = timeout_ms
        self._enabled = enabled
        self._tiers = tiers
        self._multiplier_enabled = multiplier_enabled
        self._count = 0
        self._last_ts: int | None = None

    @property
    def count(self) -> int:
        return self._count

    @property
    def multiplier(self) -> float:
        """Energy multiplier for the current combo: the highest tier reached."""
        if not self._multiplier_enabled:
            return 1.0
        reached = [tier.multiplier for tier in self._tiers if tier.at <= self._count]
        return reached[-1] if reached else 1.0

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
