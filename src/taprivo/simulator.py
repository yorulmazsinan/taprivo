"""Keyboard simulator: produces TapEvents without a camera."""

from __future__ import annotations

import uuid
from collections.abc import Callable

from taprivo.config import Config
from taprivo.core.energy import EnergyEngine, TapResult
from taprivo.core.events import Finger, Hand, TapEvent, TapSource, now_monotonic_ms

KEY_TO_FINGER: dict[str, Finger] = {
    "1": Finger.THUMB,
    "2": Finger.INDEX,
    "3": Finger.MIDDLE,
    "4": Finger.RING,
    "5": Finger.PINKY,
}

SIMULATED_HAND_ID = "sim-right"


class Simulator:
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

    @property
    def running(self) -> bool:
        return self._running

    def start(self) -> None:
        self._running = True
        self._engine.set_tracking("simulator")

    def stop(self) -> None:
        self._running = False
        self._engine.set_tracking("inactive")

    def tap(self, finger: Finger) -> TapResult | None:
        if not self._running:
            return None
        event = TapEvent(
            event_id=uuid.uuid4().hex,
            session_id=self._engine.session_id,
            hand=Hand.RIGHT,
            hand_id=SIMULATED_HAND_ID,
            finger=finger,
            timestamp_monotonic_ms=self._now_ms(),
            displacement=self._config.simulator.displacement,
            velocity=self._config.simulator.velocity,
            confidence=1.0,
            source=TapSource.SIMULATOR,
        )
        return self._engine.apply_tap(event)

    def tap_key(self, key: str) -> TapResult | None:
        finger = KEY_TO_FINGER.get(key)
        if finger is None:
            return None
        return self.tap(finger)
