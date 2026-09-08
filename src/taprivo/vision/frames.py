"""Immutable data carried through the vision pipeline."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from taprivo.core.events import Finger, Hand


@dataclass(frozen=True, slots=True)
class Frame:
    ts_ms: int
    image: np.ndarray  # BGR uint8, shape (H, W, 3)


@dataclass(frozen=True, slots=True)
class FingerFeatures:
    scale: float
    c2: dict[Finger, float]


@dataclass(frozen=True, slots=True)
class HandFrame:
    ts_ms: int
    hand: Hand
    hand_id: str
    score: float
    landmarks: tuple[tuple[float, float, float], ...]
    features: FingerFeatures


@dataclass(frozen=True, slots=True)
class FrameStats:
    captured_fps: float
    processed_fps: float
    detection_ratio: float
    last_frame_age_ms: int
