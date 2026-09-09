"""Keyboard drumming: turns key presses into TapEvents without a camera."""

from __future__ import annotations

import logging
import uuid
from collections import deque
from collections.abc import Callable

from taprivo.config import Config
from taprivo.core.energy import EnergyEngine, TapResult
from taprivo.core.events import Finger, Hand, TapEvent, TapSource, now_monotonic_ms

log = logging.getLogger(__name__)

#: Left hand drums on 1-2-3-4 (pinky to index), right hand on 7-8-9-0
#: (index to pinky), so the two hands mirror each other around the home row.
KEY_MAP: dict[str, tuple[Hand, Finger]] = {
    "1": (Hand.LEFT, Finger.PINKY),
    "2": (Hand.LEFT, Finger.RING),
    "3": (Hand.LEFT, Finger.MIDDLE),
    "4": (Hand.LEFT, Finger.INDEX),
    "7": (Hand.RIGHT, Finger.INDEX),
    "8": (Hand.RIGHT, Finger.MIDDLE),
    "9": (Hand.RIGHT, Finger.RING),
    "0": (Hand.RIGHT, Finger.PINKY),
}

HAND_IDS: dict[Hand, str] = {Hand.LEFT: "kbd-left", Hand.RIGHT: "kbd-right"}

CAP_WINDOW_MS = 1_000


class Simulator:
    """Keyboard tap source; both hands share one rate cap."""

    def __init__(
        self,
        engine: EnergyEngine,
        config: Config,
        now_ms: Callable[[], int] = now_monotonic_ms,
    ) -> None:
        self._engine = engine
        self._config = config
        self._now_ms = now_ms
        self._running = False
        self._recent: deque[int] = deque()

    @property
    def running(self) -> bool:
        return self._running

    def start(self) -> None:
        self._running = True
        self._recent.clear()
        self._engine.set_tracking("simulator")

    def stop(self) -> None:
        self._running = False
        self._recent.clear()
        self._engine.set_tracking("inactive")

    def tap(self, hand: Hand, finger: Finger) -> TapResult | None:
        """Register one keyboard tap; returns None when it is not counted."""
        if not self._running:
            return None
        now = self._now_ms()
        if not self._admit(now):
            log.debug("[TAP] dropped %s:%s above the tap cap", hand, finger)
            return None
        event = TapEvent(
            event_id=uuid.uuid4().hex,
            session_id=self._engine.session_id,
            hand=hand,
            hand_id=HAND_IDS[hand],
            finger=finger,
            timestamp_monotonic_ms=now,
            displacement=self._config.simulator.displacement,
            velocity=self._config.simulator.velocity,
            confidence=1.0,
            source=TapSource.SIMULATOR,
        )
        return self._engine.apply_tap(event)

    def tap_key(self, key: str) -> TapResult | None:
        mapped = KEY_MAP.get(key)
        if mapped is None:
            return None
        hand, finger = mapped
        return self.tap(hand, finger)

    def _admit(self, now_ms: int) -> bool:
        """Sliding-window rate cap shared by both hands (anti-macro guard)."""
        cutoff = now_ms - CAP_WINDOW_MS
        while self._recent and self._recent[0] <= cutoff:
            self._recent.popleft()
        if len(self._recent) >= self._config.simulator.max_taps_per_second:
            return False
        self._recent.append(now_ms)
        return True
