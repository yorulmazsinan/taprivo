"""Synthetic HandFrame generators and CSV replay for vision tests."""

from __future__ import annotations

import csv
import random
import time
from collections.abc import Iterable
from dataclasses import replace
from pathlib import Path
from weakref import WeakKeyDictionary

import numpy as np

from taprivo.core.events import Finger, Hand, TapEvent
from taprivo.vision.camera import CameraError, CameraSource
from taprivo.vision.detector import DetectorParams, TapDetector
from taprivo.vision.features import compute_features
from taprivo.vision.frames import FingerFeatures, Frame, HandFrame

DUMMY_LANDMARKS = tuple((0.5, 0.5, 0.0) for _ in range(21))

# Last frame timestamp actually fed to each detector via run(), across calls, so
# chained run() calls on the same detector can be checked for non-decreasing
# timestamps too (see run() below).
_LAST_FED_TS: WeakKeyDictionary[TapDetector, int] = WeakKeyDictionary()


def hand_frame(
    ts_ms: int,
    c2: dict[Finger, float],
    hand_id: str = "h1",
    score: float = 0.95,
    hand: Hand = Hand.RIGHT,
) -> HandFrame:
    full = {finger: c2.get(finger, 0.6) for finger in Finger}
    return HandFrame(
        ts_ms=ts_ms,
        hand=hand,
        hand_id=hand_id,
        score=score,
        landmarks=DUMMY_LANDMARKS,
        features=FingerFeatures(scale=0.1, c2=full),
    )


def flat(
    start_ms: int,
    duration_ms: int,
    fps: int = 25,
    base: float = 0.6,
    jitter: float = 0.0,
    seed: int = 0,
) -> list[HandFrame]:
    rng = random.Random(seed)
    step = 1000 // fps
    frames = []
    for ts in range(start_ms, start_ms + duration_ms, step):
        c2 = {f: base + (rng.uniform(-jitter, jitter) if jitter else 0.0) for f in Finger}
        frames.append(hand_frame(ts, c2))
    return frames


def pulse(
    start_ms: int,
    finger: Finger,
    amplitude: float,
    base: float = 0.6,
    duration_ms: int = 300,
    fps: int = 25,
    others: float = 0.6,
    hand_id: str = "h1",
) -> list[HandFrame]:
    """Triangular flex/extend cycle on one finger; the others stay flat."""
    step = 1000 // fps
    frames = []
    for ts in range(start_ms, start_ms + duration_ms + step, step):
        phase = (ts - start_ms) / duration_ms
        depth = amplitude * (1 - abs(2 * phase - 1)) if 0 <= phase <= 1 else 0.0
        c2 = {f: others for f in Finger}
        c2[finger] = base - depth
        frames.append(hand_frame(ts, c2, hand_id=hand_id))
    return frames


def run(detector: TapDetector, frames: Iterable[HandFrame], tail_ms: int = 400) -> list[TapEvent]:
    events: list[TapEvent] = []
    last = 0
    last_frame: HandFrame | None = None
    prior = _LAST_FED_TS.get(detector)

    def feed(frame: HandFrame | None, ts_ms: int) -> None:
        nonlocal prior
        if prior is not None and ts_ms < prior:
            raise AssertionError("non-monotonic frame timestamps in test input")
        prior = ts_ms
        _LAST_FED_TS[detector] = ts_ms
        events.extend(detector.process(frame, ts_ms))

    for frame in frames:
        feed(frame, frame.ts_ms)
        last = frame.ts_ms
        last_frame = frame
    # Hold the hand still at its last observed pose so in-flight excursions can
    # settle back toward baseline, and let matured candidates clear the
    # attribution window, without inventing new motion.
    for ts in range(last + 40, last + tail_ms, 40):
        held = None if last_frame is None else replace(last_frame, ts_ms=ts)
        feed(held, ts)
    return events


def replay_csv(path: Path, params: DetectorParams) -> list[TapEvent]:
    detector = TapDetector(params, lambda: "replay-session")
    frames: list[HandFrame] = []
    with path.open() as fh:
        for row in csv.DictReader(fh):
            pts = tuple(
                (float(row[f"x{i}"]), float(row[f"y{i}"]), float(row[f"z{i}"])) for i in range(21)
            )
            frames.append(
                HandFrame(
                    ts_ms=int(row["ts_ms"]),
                    hand=Hand.RIGHT if row["hand"] == "Right" else Hand.LEFT,
                    hand_id="spike",
                    score=float(row["score"]),
                    landmarks=pts,
                    features=compute_features(pts),
                )
            )
    return run(detector, frames)


class IdleSource(CameraSource):
    """Opens (or fails), yields blank frames slowly, records close()."""

    def __init__(self, fail: bool = False, index: int = 1) -> None:
        super().__init__(index=index, factory=lambda i: None)
        self._fail = fail
        self.closed = False
        self._n = 0

    def open(self) -> None:
        if self._fail:
            raise CameraError(
                f"camera {self._index} could not be opened (permission denied or device missing)"
            )

    @property
    def is_open(self) -> bool:
        return not self.closed

    def read(self) -> Frame | None:
        time.sleep(0.01)
        self._n += 1
        return Frame(ts_ms=self._n * 40, image=np.full((48, 64, 3), 90, dtype=np.uint8))

    def close(self) -> None:
        self.closed = True


class NoHandTracker:
    def process(self, frame: Frame) -> None:
        return None

    def close(self) -> None:
        pass
