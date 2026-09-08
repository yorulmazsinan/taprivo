"""Per-finger features derived from 21 hand landmarks."""

from __future__ import annotations

import math
from collections.abc import Sequence

from taprivo.core.events import Finger
from taprivo.vision.frames import FingerFeatures

WRIST = 0
MIDDLE_MCP = 9
TIP: dict[Finger, int] = {
    Finger.THUMB: 4,
    Finger.INDEX: 8,
    Finger.MIDDLE: 12,
    Finger.RING: 16,
    Finger.PINKY: 20,
}
MCP: dict[Finger, int] = {
    Finger.THUMB: 2,
    Finger.INDEX: 5,
    Finger.MIDDLE: 9,
    Finger.RING: 13,
    Finger.PINKY: 17,
}
LANDMARK_COUNT = 21
MIN_SCALE = 1e-4

Point = tuple[float, float, float]


class DegenerateHandError(ValueError):
    """The hand is too small or collapsed to normalise."""


def dist2(a: Point, b: Point) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def compute_features(landmarks: Sequence[Point]) -> FingerFeatures:
    if len(landmarks) != LANDMARK_COUNT:
        raise ValueError(f"expected {LANDMARK_COUNT} landmarks, got {len(landmarks)}")
    scale = dist2(landmarks[WRIST], landmarks[MIDDLE_MCP])
    if scale < MIN_SCALE:
        raise DegenerateHandError(f"hand scale {scale:.6f} below {MIN_SCALE}")
    c2 = {
        finger: dist2(landmarks[TIP[finger]], landmarks[MCP[finger]]) / scale for finger in Finger
    }
    return FingerFeatures(scale=scale, c2=c2)
