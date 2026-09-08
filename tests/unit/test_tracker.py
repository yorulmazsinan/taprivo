from __future__ import annotations

import hashlib
import math
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from taprivo.core.events import Finger, Hand
from taprivo.vision import tracker
from taprivo.vision.features import MCP, MIDDLE_MCP, TIP, WRIST
from taprivo.vision.frames import Frame
from taprivo.vision.tracker import (
    MODEL_SHA256,
    HandIdAssigner,
    MediaPipeHandTracker,
    ModelError,
    hand_frame_from_result,
    model_path,
    verify_model,
)


def test_model_path_points_at_bundled_file() -> None:
    path = model_path()
    assert path.name == "hand_landmarker.task" and path.exists()
    assert verify_model() == path


def test_verify_model_rejects_wrong_checksum(tmp_path: Path) -> None:
    bad = tmp_path / "hand_landmarker.task"
    bad.write_bytes(b"not a model")
    assert hashlib.sha256(bad.read_bytes()).hexdigest() != MODEL_SHA256
    with pytest.raises(ModelError, match="checksum"):
        verify_model(bad)


def test_verify_model_rejects_missing_file(tmp_path: Path) -> None:
    with pytest.raises(ModelError, match="missing"):
        verify_model(tmp_path / "nope.task")


def test_hand_id_is_stable_until_gap_or_hand_change() -> None:
    ids = HandIdAssigner(gap_ms=250)
    a = ids.observe(0, Hand.RIGHT)
    assert ids.observe(40, Hand.RIGHT) == a
    assert ids.observe(290, Hand.RIGHT) == a  # gap of 250 is not > 250
    b = ids.observe(600, Hand.RIGHT)  # gap 310 > 250
    assert b != a and len(b) == 32
    c = ids.observe(640, Hand.LEFT)
    assert c != b
    assert ids.current == c


def test_available_reports_mediapipe_presence() -> None:
    assert isinstance(tracker.available(), bool)


class _Pt:
    """Fake mediapipe landmark point."""

    def __init__(self, x: float, y: float, z: float) -> None:
        self.x = x
        self.y = y
        self.z = z


class _Cat:
    """Fake mediapipe handedness category."""

    def __init__(self, category_name: str, score: float) -> None:
        self.category_name = category_name
        self.score = score


class _Result:
    """Fake mediapipe HandLandmarkerResult."""

    def __init__(self, hand_landmarks: list[list[_Pt]], handedness: list[list[_Cat]]) -> None:
        self.hand_landmarks = hand_landmarks
        self.handedness = handedness


def _open_hand() -> list[_Pt]:
    """Synthetic 21-point open hand, mirroring test_features.open_hand()'s geometry:
    wrist at (0.5, 0.8), middle MCP 0.1 up, each tip 0.1 above its own MCP → c2 ≈ 1.0."""
    pts = [_Pt(0.0, 0.0, 0.0) for _ in range(21)]
    pts[WRIST] = _Pt(0.5, 0.8, 0.0)
    pts[MIDDLE_MCP] = _Pt(0.5, 0.7, 0.0)
    xs = {
        Finger.THUMB: 0.40,
        Finger.INDEX: 0.45,
        Finger.MIDDLE: 0.50,
        Finger.RING: 0.55,
        Finger.PINKY: 0.60,
    }
    for finger, x in xs.items():
        pts[MCP[finger]] = _Pt(x, 0.70, 0.0)
        pts[TIP[finger]] = _Pt(x, 0.60, 0.0)
    return pts


def test_hand_frame_from_result_maps_handedness_and_score() -> None:
    ids = HandIdAssigner()
    result = _Result([_open_hand()], [[_Cat("Left", 0.87)]])
    frame = hand_frame_from_result(result, 0, ids)
    assert frame is not None
    assert frame.hand is Hand.LEFT
    assert frame.score == 0.87
    assert len(frame.landmarks) == 21
    assert math.isclose(frame.features.c2[Finger.INDEX], 1.0)
    assert frame.hand_id == ids.current

    right = _Result([_open_hand()], [[_Cat("Right", 0.5)]])
    later = hand_frame_from_result(right, 40, ids)
    assert later is not None
    assert later.hand is Hand.RIGHT
    assert later.hand_id != frame.hand_id


def test_hand_frame_from_result_returns_none_without_hands() -> None:
    ids = HandIdAssigner()
    result = _Result([], [])
    assert hand_frame_from_result(result, 0, ids) is None


def test_hand_frame_from_result_returns_none_for_degenerate_hand() -> None:
    ids = HandIdAssigner()
    pts = _open_hand()
    pts[MIDDLE_MCP] = pts[WRIST]
    result = _Result([pts], [[_Cat("Right", 0.9)]])
    assert hand_frame_from_result(result, 0, ids) is None


class _FakeLandmarker:
    """Records the timestamps it is called with in place of a real MediaPipe
    HandLandmarker; used to exercise MediaPipeHandTracker without mediapipe."""

    def __init__(self) -> None:
        self.timestamps: list[int] = []
        self.closed = False

    def detect_for_video(self, image: Any, ts_ms: int) -> _Result:
        self.timestamps.append(ts_ms)
        return _Result([], [])

    def close(self) -> None:
        self.closed = True


def test_tracker_with_injected_landmarker() -> None:
    mediapipe_already_imported = "mediapipe" in sys.modules
    fake = _FakeLandmarker()
    t = MediaPipeHandTracker(landmarker=fake)
    if not mediapipe_already_imported:
        assert "mediapipe" not in sys.modules

    blank = np.zeros((4, 4, 3), dtype=np.uint8)
    assert t.process(Frame(ts_ms=10, image=blank)) is None
    assert t.process(Frame(ts_ms=10, image=blank)) is None  # non-increasing ts tolerated
    assert t.process(Frame(ts_ms=5, image=blank)) is None  # non-increasing ts tolerated
    assert fake.timestamps == [10, 11, 12]

    t.close()
    assert fake.closed


@pytest.mark.skipif(not tracker.available(), reason="mediapipe not installed")
def test_mediapipe_tracker_runs_on_blank_frame() -> None:
    t = MediaPipeHandTracker()
    try:
        blank = Frame(ts_ms=10, image=np.full((480, 640, 3), 90, dtype=np.uint8))
        assert t.process(blank) is None  # no hand in a flat grey image
        assert t.process(Frame(ts_ms=10, image=blank.image)) is None  # non-increasing ts tolerated
    finally:
        t.close()
