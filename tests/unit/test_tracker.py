from __future__ import annotations

import hashlib
from pathlib import Path

import numpy as np
import pytest

from taprivo.core.events import Hand
from taprivo.vision import tracker
from taprivo.vision.frames import Frame
from taprivo.vision.tracker import (
    MODEL_SHA256,
    HandIdAssigner,
    ModelError,
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


@pytest.mark.skipif(not tracker.available(), reason="mediapipe not installed")
def test_mediapipe_tracker_runs_on_blank_frame() -> None:
    from taprivo.vision.tracker import MediaPipeHandTracker

    t = MediaPipeHandTracker()
    try:
        blank = Frame(ts_ms=10, image=np.full((480, 640, 3), 90, dtype=np.uint8))
        assert t.process(blank) is None  # no hand in a flat grey image
        assert t.process(Frame(ts_ms=10, image=blank.image)) is None  # non-increasing ts tolerated
    finally:
        t.close()
