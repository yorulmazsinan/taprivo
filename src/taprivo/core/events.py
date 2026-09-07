"""Tap event model shared by camera, simulator and engine."""

from __future__ import annotations

import time
from dataclasses import dataclass
from enum import StrEnum


class Finger(StrEnum):
    THUMB = "thumb"
    INDEX = "index"
    MIDDLE = "middle"
    RING = "ring"
    PINKY = "pinky"


class Hand(StrEnum):
    LEFT = "left"
    RIGHT = "right"


class TapSource(StrEnum):
    CAMERA = "camera"
    SIMULATOR = "simulator"


@dataclass(frozen=True, slots=True)
class TapEvent:
    event_id: str
    session_id: str
    hand: Hand
    hand_id: str
    finger: Finger
    timestamp_monotonic_ms: int
    displacement: float
    velocity: float
    confidence: float
    source: TapSource


def now_monotonic_ms() -> int:
    return time.monotonic_ns() // 1_000_000
