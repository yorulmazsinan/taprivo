"""Per-session counters that are not part of the balance."""

from __future__ import annotations

import uuid
from collections import deque
from collections.abc import Callable
from datetime import UTC, datetime

from taprivo.core.events import Finger, Hand, TapSource
from taprivo.core.state import LastSpend, Mode

TAPS_PER_MINUTE_WINDOW_MS = 60_000
REASON_SUMMARY_LENGTH = 60
TAPS_PER_SQUEEZE = 5


class Session:
    def __init__(self, now_ms: Callable[[], int], mode: Mode) -> None:
        self.session_id = uuid.uuid4().hex
        self.started_at_monotonic_ms = now_ms()
        self.started_at_utc = datetime.now(UTC)
        self.mode: Mode = mode
        self.taps_per_finger: dict[Finger, int] = {finger: 0 for finger in Finger}
        self.taps_per_hand: dict[Hand, int] = {hand: 0 for hand in Hand}
        self.taps_per_hand_finger: dict[tuple[Hand, Finger], int] = {
            (hand, finger): 0 for hand in Hand for finger in Finger
        }
        self.taps_per_source: dict[TapSource, int] = {source: 0 for source in TapSource}
        self.taps_total = 0
        self.max_combo = 0
        self.spend_count = 0
        self.last_spend: LastSpend | None = None
        self._recent: deque[int] = deque()

    def record_tap(self, hand: Hand, finger: Finger, ts_ms: int, source: TapSource) -> None:
        self.taps_per_finger[finger] += 1
        self.taps_per_hand[hand] += 1
        self.taps_per_hand_finger[(hand, finger)] += 1
        self.taps_per_source[source] += 1
        self.taps_total += 1
        self._recent.append(ts_ms)
        self._prune(ts_ms)

    def record_combo(self, count: int) -> None:
        self.max_combo = max(self.max_combo, count)

    @property
    def squeezes(self) -> int:
        """Camera squeezes: the detector emits five taps for each one."""
        return self.taps_per_source[TapSource.CAMERA] // TAPS_PER_SQUEEZE

    def taps_per_minute(self, now_ms: int) -> int:
        self._prune(now_ms)
        return len(self._recent)

    def record_spend(self, amount: int, reason: str) -> None:
        self.spend_count += 1
        self.last_spend = LastSpend(
            amount=amount, reason=reason[:REASON_SUMMARY_LENGTH], at_utc=datetime.now(UTC)
        )

    def duration_seconds(self, now_ms: int) -> int:
        return max(0, (now_ms - self.started_at_monotonic_ms) // 1000)

    def _prune(self, now_ms: int) -> None:
        cutoff = now_ms - TAPS_PER_MINUTE_WINDOW_MS
        while self._recent and self._recent[0] <= cutoff:
            self._recent.popleft()
