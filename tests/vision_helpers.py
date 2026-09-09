"""Synthetic HandFrame generators and CSV replay for vision tests."""

from __future__ import annotations

import csv
import dataclasses
import time
from pathlib import Path

import numpy as np

from taprivo.core.events import Finger, Hand, TapEvent
from taprivo.vision.camera import CameraError, CameraSource
from taprivo.vision.features import compute_features
from taprivo.vision.frames import FingerFeatures, Frame, HandFrame
from taprivo.vision.squeeze import SqueezeDetector, SqueezeParams

DUMMY_LANDMARKS = tuple((0.5, 0.5, 0.0) for _ in range(21))


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


def hands_frame(
    ts_ms: int, openness_by_hand: dict[Hand, float], score: float = 0.95
) -> tuple[HandFrame, ...]:
    """One HandFrame per hand; all four non-thumb fingers share the openness value."""
    frames = []
    for hand, openness in openness_by_hand.items():
        c2 = {f: openness for f in Finger}
        c2[Finger.THUMB] = 0.5
        frames.append(hand_frame(ts_ms, c2, hand_id=f"{hand.value}-1", score=score, hand=hand))
    return tuple(frames)


def squeeze_cycle(
    start_ms: int,
    hand: Hand = Hand.RIGHT,
    open_value: float = 0.9,
    closed_value: float = 0.3,
    duration_ms: int = 800,
    fps: int = 25,
) -> list[tuple[HandFrame, ...]]:
    """Open → closed → open triangle over duration_ms; returns per-frame hand tuples."""
    step = 1000 // fps
    out = []
    for ts in range(start_ms, start_ms + duration_ms + step, step):
        phase = (ts - start_ms) / duration_ms
        depth = (open_value - closed_value) * (1 - abs(2 * phase - 1)) if 0 <= phase <= 1 else 0.0
        out.append(hands_frame(ts, {hand: open_value - depth}))
    return out


def steady(
    start_ms: int,
    duration_ms: int,
    value: float = 0.9,
    hand: Hand = Hand.RIGHT,
    fps: int = 25,
) -> list[tuple[HandFrame, ...]]:
    step = 1000 // fps
    return [hands_frame(ts, {hand: value}) for ts in range(start_ms, start_ms + duration_ms, step)]


def run_squeeze(
    detector: SqueezeDetector, sequence: list[tuple[HandFrame, ...]], tail_ms: int = 400
) -> list[TapEvent]:
    events: list[TapEvent] = []
    last_ts = None
    for hands in sequence:
        ts = hands[0].ts_ms if hands else (last_ts or 0) + 40
        events.extend(detector.process(hands, ts))
        last_ts = ts
    if sequence and sequence[-1]:
        for ts in range((last_ts or 0) + 40, (last_ts or 0) + tail_ms, 40):
            held = tuple(dataclasses.replace(h, ts_ms=ts) for h in sequence[-1])
            events.extend(detector.process(held, ts))
    return events


def replay_csv(path: Path, params: SqueezeParams) -> list[TapEvent]:
    detector = SqueezeDetector(params, lambda: "replay-session")
    frames_by_ts: dict[int, list[HandFrame]] = {}
    with path.open() as fh:
        for row in csv.DictReader(fh):
            pts = tuple(
                (float(row[f"x{i}"]), float(row[f"y{i}"]), float(row[f"z{i}"])) for i in range(21)
            )
            hand = Hand.RIGHT if row["hand"] == "Right" else Hand.LEFT
            ts_ms = int(row["ts_ms"])
            frames_by_ts.setdefault(ts_ms, []).append(
                HandFrame(
                    ts_ms=ts_ms,
                    hand=hand,
                    hand_id=hand.value,
                    score=float(row["score"]),
                    landmarks=pts,
                    features=compute_features(pts),
                )
            )
    sequence: list[tuple[HandFrame, ...]] = [tuple(hands) for hands in frames_by_ts.values()]
    return run_squeeze(detector, sequence)


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
    def process(self, frame: Frame) -> tuple[HandFrame, ...]:
        return ()

    def close(self) -> None:
        pass
