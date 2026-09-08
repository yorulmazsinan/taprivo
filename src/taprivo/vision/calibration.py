"""Guided calibration: a pure state machine driven by HandFrames and timestamps."""

from __future__ import annotations

import statistics
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal

from taprivo.core.events import Finger
from taprivo.vision.detector import DetectorParams, TapDetector
from taprivo.vision.frames import HandFrame

ORDER: tuple[Finger, ...] = (Finger.INDEX, Finger.MIDDLE, Finger.RING, Finger.PINKY, Finger.THUMB)
VISIBILITY_MS = 2000
VISIBILITY_MIN = 0.9
NOISE_MS = 2000
COUNTDOWN_MS = 3000
RECORD_MS = 6000
TARGET_TAPS = 5
MIN_TAPS = 3
MAX_TAPS = 7
THRESHOLD_MIN = 0.12
THRESHOLD_MAX = 0.40

Step = Literal["visibility", "noise", "countdown", "record", "done"]
Status = Literal["ok", "uncalibrated"]
TOTAL_STEPS = 2 + 2 * len(ORDER)


@dataclass(frozen=True, slots=True)
class CalibrationPrompt:
    step: Step
    finger: Finger | None
    text: str
    remaining_ms: int
    progress: float


@dataclass(frozen=True, slots=True)
class FingerCalibration:
    finger: Finger
    sigma: float
    median_amplitude: float
    taps: int
    threshold: float
    status: Status


@dataclass(frozen=True, slots=True)
class CalibrationResult:
    fingers: dict[Finger, FingerCalibration]
    thresholds: dict[Finger, float]


def compute_threshold(sigma: float, median_amplitude: float) -> float:
    return min(max(max(3 * sigma, 0.5 * median_amplitude), THRESHOLD_MIN), THRESHOLD_MAX)


@dataclass
class _Progress:
    step: Step = "visibility"
    finger_index: int = 0
    step_start: int = 0
    seen: int = 0
    total: int = 0
    noise: dict[Finger, list[float]] = field(default_factory=lambda: {f: [] for f in Finger})
    amplitudes: dict[Finger, list[float]] = field(default_factory=lambda: {f: [] for f in Finger})
    sigma: dict[Finger, float] = field(default_factory=dict)
    visibility_failed: bool = False


class CalibrationSession:
    def __init__(self, params: DetectorParams, session_id_provider: Callable[[], str]) -> None:
        self._params = params
        self._detector = TapDetector(params, session_id_provider)
        self._s = _Progress()
        self._result: CalibrationResult | None = None
        self._rows: list[dict[str, float | int | str]] = []

    # -- lifecycle -----------------------------------------------------------

    def start(self, ts_ms: int) -> None:
        self._s = _Progress(step_start=ts_ms)
        self._result = None
        self._rows = []
        self._detector.reset()

    @property
    def finished(self) -> bool:
        return self._s.step == "done"

    def result(self) -> CalibrationResult | None:
        return self._result

    def export_rows(self) -> list[dict[str, float | int | str]]:
        return list(self._rows)

    # -- processing ----------------------------------------------------------

    def process(self, frame: HandFrame | None, ts_ms: int) -> CalibrationPrompt:
        s = self._s
        if frame is not None:
            current = self._current_finger()
            row: dict[str, float | int | str] = {
                "ts_ms": ts_ms,
                "step": s.step,
                "finger": current.value if current is not None else "",
            }
            row.update({f.value: frame.features.c2[f] for f in Finger})
            self._rows.append(row)
        elapsed = ts_ms - s.step_start
        if s.step == "visibility":
            s.total += 1
            if frame is not None:
                s.seen += 1
            if elapsed >= VISIBILITY_MS:
                ratio = s.seen / s.total if s.total else 0.0
                if ratio >= VISIBILITY_MIN:
                    self._enter("noise", ts_ms)
                else:
                    s.visibility_failed = True
                    s.seen = s.total = 0
                    s.step_start = ts_ms
        elif s.step == "noise":
            if frame is not None:
                for f in Finger:
                    s.noise[f].append(frame.features.c2[f])
            if elapsed >= NOISE_MS:
                s.sigma = {
                    f: statistics.pstdev(v) if len(v) >= 2 else 0.0 for f, v in s.noise.items()
                }
                self._enter("countdown", ts_ms)
        elif s.step == "countdown":
            if elapsed >= COUNTDOWN_MS:
                self._detector.reset()
                self._enter("record", ts_ms)
        elif s.step == "record":
            finger = ORDER[s.finger_index]
            for event in self._detector.process(frame, ts_ms):
                if event.finger is finger:
                    s.amplitudes[finger].append(event.displacement)
            if elapsed >= RECORD_MS:
                s.finger_index += 1
                if s.finger_index >= len(ORDER):
                    self._finish()
                    self._enter("done", ts_ms)
                else:
                    self._enter("countdown", ts_ms)
        return self.prompt(ts_ms)

    def prompt(self, ts_ms: int | None = None) -> CalibrationPrompt:
        s = self._s
        finger = self._current_finger()
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
        elif s.step == "noise":
            text = "Hold your hand still"
            remaining = max(0, NOISE_MS - elapsed)
            done_steps = 1
        elif s.step == "countdown":
            assert finger is not None
            text = f"Get ready: {finger.value}"
            remaining = max(0, COUNTDOWN_MS - elapsed)
            done_steps = 2 + 2 * s.finger_index
        elif s.step == "record":
            assert finger is not None
            text = f"Tap your {finger.value} finger {TARGET_TAPS} times"
            remaining = max(0, RECORD_MS - elapsed)
            done_steps = 3 + 2 * s.finger_index
        else:
            text = "Calibration complete"
            remaining = 0
            done_steps = TOTAL_STEPS
        return CalibrationPrompt(s.step, finger, text, remaining, done_steps / TOTAL_STEPS)

    # -- internals -----------------------------------------------------------

    def _current_finger(self) -> Finger | None:
        if self._s.step in ("countdown", "record") and self._s.finger_index < len(ORDER):
            return ORDER[self._s.finger_index]
        return None

    def _enter(self, step: Step, ts_ms: int) -> None:
        self._s.step = step
        self._s.step_start = ts_ms

    def _finish(self) -> None:
        fingers: dict[Finger, FingerCalibration] = {}
        for f in Finger:
            amps = self._s.amplitudes[f]
            sigma = self._s.sigma.get(f, 0.0)
            median = statistics.median(amps) if amps else 0.0
            if MIN_TAPS <= len(amps) <= MAX_TAPS:
                fingers[f] = FingerCalibration(
                    f, sigma, median, len(amps), compute_threshold(sigma, median), "ok"
                )
            else:
                fingers[f] = FingerCalibration(
                    f, sigma, median, len(amps), self._params.threshold, "uncalibrated"
                )
        self._result = CalibrationResult(fingers, {f: c.threshold for f, c in fingers.items()})
