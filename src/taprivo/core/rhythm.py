"""Rhythm tracker: tap intervals, the tempo they imply and a steady-beat bonus."""

from __future__ import annotations

from collections import deque
from statistics import fmean, pstdev

MS_PER_MINUTE = 60_000
# A pause longer than this always breaks the beat, however slow the tempo was.
MIN_GAP_MS = 1500


class RhythmTracker:
    """Tracks the interval between consecutive taps over a sliding window.

    The window holds the last `window` intervals in milliseconds. A gap longer
    than twice the current mean interval (never less than MIN_GAP_MS) is read
    as a pause rather than a beat, and clears the window.
    """

    def __init__(
        self,
        window: int = 8,
        tolerance: float = 0.15,
        bpm_min: int = 60,
        bpm_max: int = 240,
        enabled: bool = True,
        steady_multiplier: float = 1.25,
    ) -> None:
        self._tolerance = tolerance
        self._bpm_min = bpm_min
        self._bpm_max = bpm_max
        self._enabled = enabled
        self._steady_multiplier = steady_multiplier
        self._intervals: deque[int] = deque(maxlen=window)
        self._last_ts: int | None = None

    @property
    def bpm(self) -> float:
        """Beats per minute implied by the mean interval; 0.0 below two taps."""
        if not self._intervals:
            return 0.0
        mean = fmean(self._intervals)
        if mean <= 0:
            return 0.0
        return MS_PER_MINUTE / mean

    @property
    def steady(self) -> bool:
        """True while the recent intervals are even and the tempo is in range."""
        if len(self._intervals) < 4:
            return False
        mean = fmean(self._intervals)
        if mean <= 0:
            return False
        if pstdev(self._intervals) / mean > self._tolerance:
            return False
        return self._bpm_min <= self.bpm <= self._bpm_max

    @property
    def multiplier(self) -> float:
        """Energy multiplier the current beat earns."""
        if self._enabled and self.steady:
            return self._steady_multiplier
        return 1.0

    def record(self, ts_ms: int) -> None:
        last = self._last_ts
        self._last_ts = ts_ms
        if last is None:
            return
        gap = ts_ms - last
        if gap > self._gap_limit():
            self._intervals.clear()
            return
        self._intervals.append(gap)

    def reset(self) -> None:
        self._intervals.clear()
        self._last_ts = None

    def _gap_limit(self) -> float:
        if not self._intervals:
            return MIN_GAP_MS
        return max(2 * fmean(self._intervals), MIN_GAP_MS)
