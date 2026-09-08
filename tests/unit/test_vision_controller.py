from __future__ import annotations

import numpy as np
import pytest

from taprivo.config import Config
from taprivo.core.energy import EnergyEngine
from taprivo.core.events import Finger
from taprivo.vision.calibration import CalibrationResult, FingerCalibration
from taprivo.vision.camera import CameraError, CameraSource
from taprivo.vision.controller import VisionController
from taprivo.vision.frames import Frame


class IdleSource(CameraSource):
    def __init__(self, fail: bool = False) -> None:
        super().__init__(index=1, factory=lambda i: None)
        self._fail = fail
        self.closed = False

    def open(self) -> None:
        if self._fail:
            raise CameraError("camera 1 could not be opened (permission denied or device missing)")

    def read(self) -> Frame | None:
        import time

        time.sleep(0.01)
        return Frame(ts_ms=0, image=np.zeros((2, 2, 3), dtype=np.uint8))

    def close(self) -> None:
        self.closed = True


class NoHandTracker:
    def process(self, frame: Frame) -> None:
        return None

    def close(self) -> None:
        pass


def make(fail: bool = False) -> tuple[VisionController, EnergyEngine]:
    engine = EnergyEngine(Config())
    controller = VisionController(
        engine,
        Config(),
        source_factory=lambda index, cfg: IdleSource(fail),
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


def test_begin_calibration_requires_running_worker() -> None:
    controller, _ = make()
    with pytest.raises(RuntimeError, match="camera"):
        controller.begin_calibration()
    controller.start(1)
    session = controller.begin_calibration()
    assert controller.calibration is session
    controller.stop()
    assert controller.calibration is None
