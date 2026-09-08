from __future__ import annotations

import pytest

from taprivo.config import Config
from taprivo.core.energy import EnergyEngine
from taprivo.core.events import Finger
from taprivo.vision.calibration import CalibrationResult, FingerCalibration
from taprivo.vision.camera import CameraError
from taprivo.vision.controller import VisionController
from tests.vision_helpers import IdleSource, NoHandTracker


def make(fail: bool = False) -> tuple[VisionController, EnergyEngine]:
    engine = EnergyEngine(Config())
    controller = VisionController(
        engine,
        Config(),
        source_factory=lambda index, cfg, now_ms: IdleSource(fail),
        tracker_factory=NoHandTracker,
    )
    return controller, engine


def test_start_stop_lifecycle() -> None:
    controller, engine = make()
    assert controller.running is False
    controller.start(1)
    assert controller.running and controller.device_index == 1
    controller.stop()
    assert controller.running is False
    assert engine.snapshot().tracking == "inactive"


def test_start_failure_raises_and_leaves_stopped() -> None:
    controller, _ = make(fail=True)
    with pytest.raises(CameraError):
        controller.start(1)
    assert controller.running is False


def test_calibration_apply_and_reset_thresholds() -> None:
    controller, _ = make()
    assert controller.thresholds()[Finger.INDEX] == 0.22
    result = CalibrationResult(
        fingers={f: FingerCalibration(f, 0.01, 0.4, 5, 0.3, "ok") for f in Finger},
        thresholds={f: 0.3 for f in Finger},
    )
    controller.apply_calibration(result)
    assert controller.thresholds()[Finger.PINKY] == 0.3
    controller.reset_thresholds()
    assert controller.thresholds()[Finger.PINKY] == 0.22


def test_calibration_apply_and_reset_thresholds_while_running() -> None:
    # Same flow as above, but with a live worker: apply_calibration/thresholds/
    # reset_thresholds must route through the worker's lock rather than
    # touching the shared TapDetector directly from another thread.
    controller, _ = make()
    controller.start(1)
    try:
        assert controller.thresholds()[Finger.INDEX] == 0.22
        result = CalibrationResult(
            fingers={f: FingerCalibration(f, 0.01, 0.4, 5, 0.3, "ok") for f in Finger},
            thresholds={f: 0.3 for f in Finger},
        )
        controller.apply_calibration(result)
        assert controller.thresholds()[Finger.PINKY] == 0.3
        controller.reset_thresholds()
        assert controller.thresholds()[Finger.PINKY] == 0.22
    finally:
        controller.stop()


def test_begin_calibration_requires_running_worker() -> None:
    controller, _ = make()
    with pytest.raises(RuntimeError, match="camera"):
        controller.begin_calibration()
    controller.start(1)
    session = controller.begin_calibration()
    assert controller.calibration is session
    controller.stop()
    assert controller.calibration is None
