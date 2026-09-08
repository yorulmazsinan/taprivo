"""Synthetic HandFrame generators and CSV replay for vision tests."""

from __future__ import annotations

import csv
import random
from collections.abc import Iterable
from dataclasses import replace
from pathlib import Path

from taprivo.core.events import Finger, Hand, TapEvent
from taprivo.vision.detector import DetectorParams, TapDetector
from taprivo.vision.features import compute_features
from taprivo.vision.frames import FingerFeatures, HandFrame

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
    for frame in frames:
        events.extend(detector.process(frame, frame.ts_ms))
        last = frame.ts_ms
        last_frame = frame
    # Hold the hand still at its last observed pose so in-flight excursions can
    # settle back toward baseline, and let matured candidates clear the
    # attribution window, without inventing new motion.
    for ts in range(last + 40, last + tail_ms, 40):
        held = None if last_frame is None else replace(last_frame, ts_ms=ts)
        events.extend(detector.process(held, ts))
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
