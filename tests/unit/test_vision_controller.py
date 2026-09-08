from __future__ import annotations

import pytest

from taprivo.config import Config
from taprivo.core.energy import EnergyEngine
from taprivo.vision.calibration import CalibrationResult
from taprivo.vision.camera import CameraError
from taprivo.vision.controller import VisionController
from taprivo.vision.squeeze import Levels
from taprivo.vision.tracker import ModelError
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


def test_start_wraps_unexpected_tracker_error_in_model_error() -> None:
    def raising_tracker_factory() -> NoHandTracker:
        raise RuntimeError("mediapipe blew up")

    engine = EnergyEngine(Config())
    controller = VisionController(
        engine,
        Config(),
        source_factory=lambda index, cfg, now_ms: IdleSource(),
        tracker_factory=raising_tracker_factory,
    )
    with pytest.raises(ModelError, match="mediapipe blew up"):
        controller.start(1)
    assert controller.running is False


def test_calibration_apply_and_reset_levels() -> None:
    controller, _ = make()
    assert controller.levels() == Levels(0.80, 0.45)
    result = CalibrationResult(levels=Levels(0.9, 0.3), cycles=5, status="ok")
    controller.apply_calibration(result)
    assert controller.levels() == Levels(0.9, 0.3)
    controller.reset_levels()
    assert controller.levels() == Levels(0.80, 0.45)


def test_calibration_apply_and_reset_levels_while_running() -> None:
    # Same flow as above, but with a live worker: apply_calibration/levels/
    # reset_levels must route through the worker's lock rather than
    # touching the shared SqueezeDetector directly from another thread.
    controller, _ = make()
    controller.start(1)
    try:
        assert controller.levels() == Levels(0.80, 0.45)
        result = CalibrationResult(levels=Levels(0.9, 0.3), cycles=5, status="ok")
        controller.apply_calibration(result)
        assert controller.levels() == Levels(0.9, 0.3)
        controller.reset_levels()
        assert controller.levels() == Levels(0.80, 0.45)
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
