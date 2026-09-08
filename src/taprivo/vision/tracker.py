"""Hand landmark tracking behind a small protocol; MediaPipe is imported lazily."""

from __future__ import annotations

import hashlib
import importlib.util
import uuid
from importlib import resources
from pathlib import Path
from typing import Any, Protocol

import cv2

from taprivo.core.events import Hand
from taprivo.vision.features import DegenerateHandError, compute_features
from taprivo.vision.frames import Frame, HandFrame

MODEL_FILENAME = "hand_landmarker.task"
MODEL_SHA256 = "fbc2a30080c3c557093b5ddfc334698132eb341044ccee322ccf8bcf3607cde1"
HAND_GAP_MS = 250


class ModelError(Exception):
    """The bundled landmark model is missing or corrupt."""


def model_path() -> Path:
    with resources.as_file(
        resources.files("taprivo.resources.models").joinpath(MODEL_FILENAME)
    ) as p:
        return Path(p)


def verify_model(path: Path | None = None) -> Path:
    target = path or model_path()
    if not target.exists():
        raise ModelError(f"hand landmark model missing at {target}")
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    if digest != MODEL_SHA256:
        raise ModelError("hand landmark model checksum mismatch; reinstall Taprivo")
    return target


def available() -> bool:
    return importlib.util.find_spec("mediapipe") is not None


class HandTracker(Protocol):
    def process(self, frame: Frame) -> HandFrame | None: ...

    def close(self) -> None: ...


class HandIdAssigner:
    def __init__(self, gap_ms: int = HAND_GAP_MS) -> None:
        self._gap_ms = gap_ms
        self._last_ts: int | None = None
        self._last_hand: Hand | None = None
        self.current: str | None = None

    def observe(self, ts_ms: int, hand: Hand) -> str:
        gap = self._last_ts is not None and ts_ms - self._last_ts > self._gap_ms
        if self.current is None or gap or hand != self._last_hand:
            self.current = uuid.uuid4().hex
        self._last_ts = ts_ms
        self._last_hand = hand
        return self.current


def hand_frame_from_result(result: Any, ts_ms: int, ids: HandIdAssigner) -> HandFrame | None:
    """Map a raw MediaPipe detection result onto a `HandFrame`, or `None`.

    Pure and mediapipe-free so it can be exercised directly with fake results.
    """
    if not result.hand_landmarks:
        return None
    points = tuple((float(p.x), float(p.y), float(p.z)) for p in result.hand_landmarks[0])
    try:
        features = compute_features(points)
    except DegenerateHandError:
        return None
    category = result.handedness[0][0]
    hand = Hand.RIGHT if category.category_name == "Right" else Hand.LEFT
    return HandFrame(
        ts_ms=ts_ms,
        hand=hand,
        hand_id=ids.observe(ts_ms, hand),
        score=float(category.score),
        landmarks=points,
        features=features,
    )


class MediaPipeHandTracker:
    def __init__(
        self,
        model: Path | None = None,
        min_confidence: float = 0.5,
        landmarker: Any | None = None,
    ) -> None:
        """`landmarker` is a test seam: pass a fake to skip the mediapipe import and
        model load entirely (its `detect_for_video` receives the raw RGB ndarray
        rather than an `mp.Image`)."""
        if landmarker is not None:
            self._mp: Any = None
            self._landmarker: Any = landmarker
        else:
            import mediapipe as mp
            from mediapipe.tasks.python import BaseOptions, vision

            path = verify_model(model)
            self._mp = mp
            options = vision.HandLandmarkerOptions(
                base_options=BaseOptions(model_asset_path=str(path)),
                running_mode=vision.RunningMode.VIDEO,
                num_hands=1,
                min_hand_detection_confidence=min_confidence,
                min_hand_presence_confidence=min_confidence,
                min_tracking_confidence=min_confidence,
            )
            self._landmarker = vision.HandLandmarker.create_from_options(options)
        self._ids = HandIdAssigner()
        self._last_ts = -1

    def process(self, frame: Frame) -> HandFrame | None:
        ts = frame.ts_ms if frame.ts_ms > self._last_ts else self._last_ts + 1
        self._last_ts = ts
        rgb = cv2.cvtColor(frame.image, cv2.COLOR_BGR2RGB)
        image: Any = (
            rgb
            if self._mp is None
            else self._mp.Image(image_format=self._mp.ImageFormat.SRGB, data=rgb)
        )
        result = self._landmarker.detect_for_video(image, ts)
        return hand_frame_from_result(result, frame.ts_ms, self._ids)

    def close(self) -> None:
        self._landmarker.close()
