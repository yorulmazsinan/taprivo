from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from pytestqt.qtbot import QtBot

from taprivo.config import Config
from taprivo.core.energy import EnergyEngine
from taprivo.core.events import Finger
from taprivo.ui.camera_window import CameraWindow
from taprivo.ui.vision_bridge import VisionSignals, make_preview_callback
from taprivo.vision.calibration import CalibrationResult, FingerCalibration
from taprivo.vision.camera import CameraDevice
from taprivo.vision.controller import VisionController
from taprivo.vision.detector import DetectorParams, TapDetector
from taprivo.vision.frames import Frame
from tests.vision_helpers import IdleSource, NoHandTracker, hand_frame

DEVICES = [
    CameraDevice(0, "Camera 0 (1920x1080) — no signal", 1920, 1080, False),
    CameraDevice(1, "Camera 1 (640x480)", 640, 480, True),
]


@pytest.fixture
def window(qtbot: QtBot) -> tuple[CameraWindow, VisionController, EnergyEngine]:
    engine = EnergyEngine(Config())
    controller = VisionController(
        engine,
        Config(),
        source_factory=lambda index, cfg, now_ms: IdleSource(index=index),
        tracker_factory=NoHandTracker,
    )
    signals = VisionSignals()
    w = CameraWindow(controller, Config(), signals, devices_fn=lambda: DEVICES)
    qtbot.addWidget(w)
    w.show()
    # Device probing now runs on a background thread; wait for it to land before
    # tests rely on the combo/start button being populated.
    qtbot.waitUntil(lambda: w.device_combo.count() == len(DEVICES), timeout=3000)
    return w, controller, engine


def test_devices_populated_and_default_selected(window: tuple) -> None:
    w, _, _ = window
    assert w.device_combo.count() == 2
    assert w.selected_device_index() == 1  # first device with signal
    assert "no signal" in w.device_combo.itemText(0)


def test_start_and_stop_camera(window: tuple, qtbot: QtBot) -> None:
    w, controller, _engine = window
    w.start_camera()
    qtbot.waitUntil(lambda: controller.running, timeout=3000)
    assert not w.start_button.isEnabled() and w.stop_button.isEnabled()
    assert controller.device_index == 1
    w.stop_camera()
    qtbot.waitUntil(lambda: not controller.running, timeout=3000)
    assert w.start_button.isEnabled()
    assert "Off" in w.status_text()


def test_start_failure_is_reported_not_raised(
    qtbot: QtBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    from PySide6.QtWidgets import QMessageBox

    monkeypatch.setattr(
        QMessageBox, "warning", staticmethod(lambda *a, **k: QMessageBox.StandardButton.Ok)
    )
    engine = EnergyEngine(Config())
    controller = VisionController(
        engine,
        Config(),
        source_factory=lambda i, c, n: IdleSource(fail=True),
        tracker_factory=NoHandTracker,
    )
    w = CameraWindow(controller, Config(), VisionSignals(), devices_fn=lambda: DEVICES)
    qtbot.addWidget(w)
    qtbot.waitUntil(lambda: w.device_combo.count() == len(DEVICES), timeout=3000)
    w.start_camera()
    assert not controller.running
    assert "permission" in w.status_text()


def test_start_failure_from_unexpected_exception_is_reported_not_raised(
    window: tuple, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    from PySide6.QtWidgets import QMessageBox

    monkeypatch.setattr(
        QMessageBox, "warning", staticmethod(lambda *a, **k: QMessageBox.StandardButton.Ok)
    )
    w, controller, _engine = window

    def raise_runtime_error(*args: object, **kwargs: object) -> None:
        raise RuntimeError("boom from an unexpected code path")

    monkeypatch.setattr(controller, "start", raise_runtime_error)
    w.start_camera()  # must not raise, even though controller.start() escaped its own guards
    assert not controller.running
    assert "boom from an unexpected code path" in w.status_text()


def test_start_camera_is_noop_while_refreshing(qtbot: QtBot) -> None:
    engine = EnergyEngine(Config())
    controller = VisionController(
        engine,
        Config(),
        source_factory=lambda index, cfg, now_ms: IdleSource(index=index),
        tracker_factory=NoHandTracker,
    )
    signals = VisionSignals()
    w = CameraWindow(controller, Config(), signals, devices_fn=lambda: DEVICES)
    qtbot.addWidget(w)
    assert not w.start_button.isEnabled()  # still refreshing
    w.start_camera()  # no-op: nothing selected/enabled yet
    assert not controller.running
    qtbot.waitUntil(lambda: w.device_combo.count() == len(DEVICES), timeout=3000)
    assert w.start_button.isEnabled()


def test_preview_packet_updates_pixmap_and_bars(window: tuple, qtbot: QtBot) -> None:
    w, _controller, _engine = window
    callback = make_preview_callback(w.signals)
    frame = Frame(ts_ms=100, image=np.full((48, 64, 3), 120, dtype=np.uint8))
    hand = hand_frame(100, {Finger.INDEX: 0.3})
    detector = TapDetector(DetectorParams(), lambda: "s")
    detector.process(hand_frame(0, {}), 0)
    detector.process(hand, 100)
    callback(frame, hand, detector.state())

    def has_pixmap() -> bool:
        pixmap = w.preview_label.pixmap()
        return pixmap is not None and not pixmap.isNull()

    qtbot.waitUntil(has_pixmap, timeout=2000)
    qtbot.waitUntil(lambda: w.bars[Finger.INDEX].value() > 0, timeout=2000)
    assert w.bars[Finger.RING].value() == 0


def test_calibration_flow_and_apply(window: tuple, qtbot: QtBot) -> None:
    w, controller, _ = window
    w.start_camera()
    qtbot.waitUntil(lambda: controller.running, timeout=3000)
    w.begin_calibration()
    assert controller.calibration is not None
    qtbot.waitUntil(lambda: "hand" in w.prompt_label.text().lower(), timeout=2000)
    result = CalibrationResult(
        fingers={f: FingerCalibration(f, 0.01, 0.4, 5, 0.3, "ok") for f in Finger},
        thresholds={f: 0.3 for f in Finger},
    )
    w.show_result(result)
    assert w.result_table.rowCount() == 5
    assert w.apply_button.isEnabled()
    w.apply_calibration()
    assert controller.thresholds()[Finger.INDEX] == 0.3
    w.use_defaults()
    assert controller.thresholds()[Finger.INDEX] == 0.22
    w.stop_camera()


def test_export_writes_features_only(window: tuple, qtbot: QtBot, tmp_path: Path) -> None:
    w, controller, _ = window
    w.start_camera()
    qtbot.waitUntil(lambda: controller.running, timeout=3000)
    session = w.begin_calibration()
    session.process(hand_frame(10, {Finger.INDEX: 0.5}), 10)
    out = tmp_path / "cal.csv"
    w.export_calibration(out)
    text = out.read_text()
    assert text.splitlines()[0].startswith("ts_ms,step,finger,thumb,index,middle,ring,pinky")
    assert "image" not in text
    w.stop_camera()


def test_close_event_stops_the_camera(window: tuple, qtbot: QtBot) -> None:
    w, controller, _engine = window
    w.start_camera()
    qtbot.waitUntil(lambda: controller.running, timeout=3000)
    w.close()
    qtbot.waitUntil(lambda: not controller.running, timeout=3000)
