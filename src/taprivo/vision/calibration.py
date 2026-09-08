"""Guided calibration: a pure state machine driven by HandFrame tuples and timestamps."""

from __future__ import annotations

import statistics
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal

from taprivo.core.events import Finger
from taprivo.vision.frames import HandFrame
from taprivo.vision.squeeze import Levels, SqueezeDetector, SqueezeParams

VISIBILITY_MS = 2000
VISIBILITY_MIN = 0.9
OPEN_MS = 2000
FIST_MS = 2000
SQUEEZE_MS = 8000
TARGET_CYCLES = 5
MIN_CYCLES = 3
MAX_CYCLES = 7
MIN_SPAN = 0.15

Step = Literal["visibility", "open", "fist", "squeeze", "done"]
Status = Literal["ok", "uncalibrated"]
TOTAL_STEPS = 5


@dataclass(frozen=True, slots=True)
class CalibrationPrompt:
    step: Step
    text: str
    remaining_ms: int
    progress: float


@dataclass(frozen=True, slots=True)
class CalibrationResult:
    levels: Levels
    cycles: int
    status: Status


@dataclass
class _Progress:
    step: Step = "visibility"
    step_start: int = 0
    seen: int = 0
    total: int = 0
    open_samples: list[float] = field(default_factory=list)
    fist_samples: list[float] = field(default_factory=list)
    cycles: int = 0
    visibility_failed: bool = False


class CalibrationSession:
    def __init__(self, params: SqueezeParams, session_id_provider: Callable[[], str]) -> None:
        self._params = params
        self._session_id_provider = session_id_provider
        self._squeeze_detector: SqueezeDetector | None = None
        self._s = _Progress()
        self._result: CalibrationResult | None = None
        self._rows: list[dict[str, float | int | str]] = []
        # Reentrant because process() calls prompt() while already holding the lock,
        # and both are called from the worker thread (process) and the UI thread
        # (prompt, result, finished, export_rows) concurrently.
        self._lock = threading.RLock()

    # -- lifecycle -------------------------------------------------------------

    def start(self, ts_ms: int) -> None:
        with self._lock:
            self._s = _Progress(step_start=ts_ms)
            self._result = None
            self._rows = []
            self._squeeze_detector = None

    @property
    def finished(self) -> bool:
        with self._lock:
            return self._s.step == "done"

    def result(self) -> CalibrationResult | None:
        with self._lock:
            return self._result

    def export_rows(self) -> list[dict[str, float | int | str]]:
        with self._lock:
            return list(self._rows)

    # -- processing --------------------------------------------------------------

    def process(self, hands: tuple[HandFrame, ...], ts_ms: int) -> CalibrationPrompt:
        with self._lock:
            s = self._s
            for frame in hands:
                row: dict[str, float | int | str] = {
                    "ts_ms": ts_ms,
                    "step": s.step,
                    "hand": frame.hand.value,
                    "openness": frame.features.openness,
                }
                row.update({f.value: frame.features.c2[f] for f in Finger})
                self._rows.append(row)
            elapsed = ts_ms - s.step_start
            if s.step == "visibility":
                s.total += 1
                if hands:
                    s.seen += 1
                if elapsed >= VISIBILITY_MS:
                    ratio = s.seen / s.total if s.total else 0.0
                    if ratio >= VISIBILITY_MIN:
                        self._enter("open", ts_ms)
                    else:
                        s.visibility_failed = True
                        s.seen = s.total = 0
                        s.step_start = ts_ms
            elif s.step == "open":
                for frame in hands:
                    s.open_samples.append(frame.features.openness)
                if elapsed >= OPEN_MS:
                    self._enter("fist", ts_ms)
            elif s.step == "fist":
                for frame in hands:
                    s.fist_samples.append(frame.features.openness)
                if elapsed >= FIST_MS:
                    open_level = (
                        statistics.median(s.open_samples)
                        if s.open_samples
                        else self._params.open_level
                    )
                    closed_level = (
                        statistics.median(s.fist_samples)
                        if s.fist_samples
                        else self._params.closed_level
                    )
                    self._squeeze_detector = SqueezeDetector(
                        self._params,
                        self._session_id_provider,
                        Levels(open_level, closed_level),
                    )
                    self._enter("squeeze", ts_ms)
            elif s.step == "squeeze":
                detector = self._squeeze_detector
                assert detector is not None
                events = detector.process(hands, ts_ms)
                s.cycles += len(events) // 5
                if elapsed >= SQUEEZE_MS:
                    self._finish()
                    self._enter("done", ts_ms)
            return self.prompt(ts_ms)

    def prompt(self, ts_ms: int | None = None) -> CalibrationPrompt:
        with self._lock:
            s = self._s
            now = ts_ms if ts_ms is not None else s.step_start
            elapsed = max(0, now - s.step_start)
            if s.step == "visibility":
                text = (
                    "Move your hand into view"
                    if s.visibility_failed
                    else "Show your hand to the camera"
                )
                remaining = max(0, VISIBILITY_MS - elapsed)
                done_steps = 0
            elif s.step == "open":
                text = "Open your hand wide"
                remaining = max(0, OPEN_MS - elapsed)
                done_steps = 1
            elif s.step == "fist":
                text = "Make a fist"
                remaining = max(0, FIST_MS - elapsed)
                done_steps = 2
            elif s.step == "squeeze":
                text = f"Squeeze your hand {TARGET_CYCLES} times (open, fist, open)"
                remaining = max(0, SQUEEZE_MS - elapsed)
                done_steps = 3
            else:
                text = "Calibration complete"
                remaining = 0
                done_steps = TOTAL_STEPS
            return CalibrationPrompt(s.step, text, remaining, done_steps / TOTAL_STEPS)

    # -- internals -----------------------------------------------------------

    def _enter(self, step: Step, ts_ms: int) -> None:
        self._s.step = step
        self._s.step_start = ts_ms

    def _finish(self) -> None:
        s = self._s
        open_level = (
            statistics.median(s.open_samples) if s.open_samples else self._params.open_level
        )
        closed_level = (
            statistics.median(s.fist_samples) if s.fist_samples else self._params.closed_level
        )
        status: Status = (
            "ok"
            if MIN_CYCLES <= s.cycles <= MAX_CYCLES and open_level - closed_level >= MIN_SPAN
            else "uncalibrated"
        )
        levels = (
            Levels(open_level, closed_level)
            if status == "ok"
            else Levels(self._params.open_level, self._params.closed_level)
        )
        self._result = CalibrationResult(levels=levels, cycles=s.cycles, status=status)
