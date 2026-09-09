from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from pytestqt.qtbot import QtBot

from taprivo.config import Config
from taprivo.core.energy import EnergyEngine
from taprivo.core.events import Hand
from taprivo.ui.camera_window import CameraWindow
from taprivo.ui.vision_bridge import VisionSignals, make_preview_callback
from taprivo.vision.calibration import CalibrationPrompt, CalibrationResult
from taprivo.vision.camera import CameraDevice
from taprivo.vision.controller import VisionController
from taprivo.vision.frames import Frame, FrameStats
from taprivo.vision.squeeze import Levels, SqueezeDetector, SqueezeParams
from tests.vision_helpers import IdleSource, NoHandTracker, hands_frame

DEVICES = [
    CameraDevice(0, "Camera 0 (1920x1080) — no signal", 1920, 1080, False),
    CameraDevice(1, "Camera 1 (640x480)", 640, 480, True),
]
NAMED_DEVICES = [
    CameraDevice(
        0,
        "Sinan's iPhone (iPhone camera; select to use)",
        0,
        0,
        False,
        "Sinan's iPhone",
        "continuity",
        False,
    ),
    CameraDevice(1, "Logi Webcam (640x480)", 640, 480, True, "Logi Webcam", "external", True),
    CameraDevice(
        2, "FaceTime HD Kamera (1280x720)", 1280, 720, True, "FaceTime HD Kamera", "builtin", True
    ),
]


class FakeCalibrationSession:
    """A deterministic stand-in for CalibrationSession: each prompt() call
    advances one "tick" of progress, finishing (with a result) on the third."""

    def __init__(self) -> None:
        self.calls = 0

    def prompt(self, ts_ms: int | None = None) -> CalibrationPrompt:
        self.calls += 1
        if self.calls < 3:
            return CalibrationPrompt(
                step="squeeze",
                text=f"Squeeze your hand ({self.calls})",
                remaining_ms=(3 - self.calls) * 1000,
                progress=self.calls / 3,
            )
        return CalibrationPrompt(
            step="done", text="Calibration complete", remaining_ms=0, progress=1.0
        )

    @property
    def finished(self) -> bool:
        return self.calls >= 3

    def result(self) -> CalibrationResult | None:
        if not self.finished:
            return None
        return CalibrationResult(levels=Levels(0.9, 0.3), cycles=5, status="ok")

    def export_rows(self) -> list[dict[str, float | int | str]]:
        return []


class FakeController:
    """Minimal stand-in for VisionController's public surface, for tests that
    need deterministic control over `running`/`error` that a real worker
    thread cannot give without flakiness."""

    def __init__(self) -> None:
        self.running = False
        self.error: str | None = None
        self.stop_calls = 0

    def stats(self) -> FrameStats:
        return FrameStats(0.0, 0.0, 0.0, 0)

    def tracking_status(self) -> str:
        return "tracking" if self.running else "inactive"

    def now_ms(self) -> int:
        return 0

    def levels(self) -> Levels:
        return Levels(0.80, 0.45)

    def begin_calibration(self) -> FakeCalibrationSession:
        return FakeCalibrationSession()

    def apply_calibration(self, result: CalibrationResult) -> None:
        pass

    def reset_levels(self) -> None:
        pass

    def stop(self) -> None:
        self.stop_calls += 1
        self.running = False
        # The real controller discards its worker (and the worker's error) on stop.
        self.error = None


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


def _named_window(qtbot: QtBot, config: Config) -> CameraWindow:
    controller = VisionController(
        EnergyEngine(config),
        config,
        source_factory=lambda index, cfg, now_ms: IdleSource(index=index),
        tracker_factory=NoHandTracker,
    )
    w = CameraWindow(controller, config, VisionSignals(), devices_fn=lambda: NAMED_DEVICES)
    qtbot.addWidget(w)
    w.show()
    qtbot.waitUntil(lambda: w.device_combo.count() == len(NAMED_DEVICES), timeout=3000)
    return w


def test_builtin_leads_the_list_and_is_selected(qtbot: QtBot) -> None:
    """Built-in first, external next, the iPhone last -- and the built-in is
    picked even though the webcam earlier in the raw list also has a signal."""
    w = _named_window(qtbot, Config())
    texts = [w.device_combo.itemText(i) for i in range(w.device_combo.count())]
    assert [t.split(" (")[0] for t in texts] == [
        "FaceTime HD Kamera",
        "Logi Webcam",
        "Sinan's iPhone",
    ]
    assert "iPhone camera; select to use" in texts[2]
    assert w.selected_device_index() == 2


def test_prefer_builtin_off_falls_back_to_first_with_signal(qtbot: QtBot) -> None:
    config = Config.model_validate({"camera": {"prefer_builtin": False}})
    w = _named_window(qtbot, config)
    assert w.selected_device_index() == 1  # the webcam, first in list order with a signal


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


def test_preview_packet_updates_pixmap_and_meters(window: tuple, qtbot: QtBot) -> None:
    w, _controller, _engine = window
    callback = make_preview_callback(w.signals)
    frame = Frame(ts_ms=100, image=np.full((48, 64, 3), 120, dtype=np.uint8))
    hands = hands_frame(100, {Hand.RIGHT: 0.9})
    detector = SqueezeDetector(SqueezeParams(), lambda: "s")
    detector.process(hands, 100)
    callback(frame, hands, detector.state())

    def has_pixmap() -> bool:
        pixmap = w.preview_label.pixmap()
        return pixmap is not None and not pixmap.isNull()

    qtbot.waitUntil(has_pixmap, timeout=2000)
    qtbot.waitUntil(lambda: w.meters[Hand.RIGHT].value() > 0, timeout=2000)
    assert w.meters[Hand.LEFT].value() == 0
    assert w.state_labels[Hand.LEFT].text() == "Not seen"


def test_calibration_flow_and_apply(window: tuple, qtbot: QtBot) -> None:
    w, controller, _ = window
    w.start_camera()
    qtbot.waitUntil(lambda: controller.running, timeout=3000)
    w.begin_calibration()
    assert controller.calibration is not None
    qtbot.waitUntil(lambda: "hand" in w.prompt_label.text().lower(), timeout=2000)
    result = CalibrationResult(levels=Levels(0.9, 0.3), cycles=5, status="ok")
    w.show_result(result)
    assert "0.90" in w.result_levels_label.text()
    assert "5" in w.result_cycles_label.text()
    assert w.apply_button.isEnabled()
    w.apply_calibration()
    assert controller.levels() == Levels(0.9, 0.3)
    w.use_defaults()
    assert controller.levels() == Levels(0.80, 0.45)
    w.stop_camera()


def test_export_writes_features_only(window: tuple, qtbot: QtBot, tmp_path: Path) -> None:
    w, controller, _ = window
    w.start_camera()
    qtbot.waitUntil(lambda: controller.running, timeout=3000)
    session = w.begin_calibration()
    session.process(hands_frame(10, {Hand.RIGHT: 0.5}), 10)
    out = tmp_path / "cal.csv"
    w.export_calibration(out)
    text = out.read_text()
    assert text.splitlines()[0].startswith("ts_ms,step,hand,openness,thumb,index,middle,ring,pinky")
    assert "image" not in text
    w.stop_camera()


def test_close_event_stops_the_camera(window: tuple, qtbot: QtBot) -> None:
    w, controller, _engine = window
    w.start_camera()
    qtbot.waitUntil(lambda: controller.running, timeout=3000)
    w.close()
    qtbot.waitUntil(lambda: not controller.running, timeout=3000)


def test_snapshot_renders_minimum_size_and_colors(window: tuple) -> None:
    w, _controller, _engine = window
    assert w.width() >= 760
    assert w.height() >= 560
    image = w.grab().toImage()
    colors: set[int] = set()
    for y in range(image.height()):
        for x in range(image.width()):
            colors.add(image.pixel(x, y))
            if len(colors) > 2:
                break
        if len(colors) > 2:
            break
    assert len(colors) > 2


def test_reopen_restarts_the_tick_timer_and_calibration_still_works(qtbot: QtBot) -> None:
    # app.py builds one CameraWindow and reuses it for every "Open Camera"
    # click; closeEvent stops the tick timer, so unless showEvent restarts
    # it, the calibration UI is dead after a single close -> reopen.
    controller = FakeController()
    w = CameraWindow(controller, Config(), VisionSignals(), devices_fn=lambda: DEVICES)
    qtbot.addWidget(w)
    w.show()
    assert w._timer.isActive()

    w.close()
    assert not w._timer.isActive()

    w.show()
    qtbot.waitUntil(lambda: w._timer.isActive(), timeout=1000)

    controller.running = True
    session = w.begin_calibration()
    assert session is not None
    assert not w.apply_button.isEnabled()

    # Driven entirely by the restarted timer's _tick -> session.prompt(); the
    # test never calls show_result() itself.
    qtbot.waitUntil(lambda: w.apply_button.isEnabled(), timeout=3000)
    assert "Squeeze" in w.prompt_label.text() or "complete" in w.prompt_label.text().lower()
    assert w.countdown_label.text() != "" or w.calibration_progress.value() > 0


def test_worker_death_surfaces_error_and_resets_the_ui(qtbot: QtBot) -> None:
    controller = FakeController()
    controller.running = True
    w = CameraWindow(controller, Config(), VisionSignals(), devices_fn=lambda: DEVICES)
    qtbot.addWidget(w)
    w.show()
    qtbot.waitUntil(lambda: w.device_combo.count() == len(DEVICES), timeout=3000)

    # Let at least one tick observe running=True so the running -> not
    # running edge can be detected on the next tick.
    qtbot.waitUntil(lambda: w._was_running, timeout=1000)

    calls: list[int] = []
    original_stop_camera = w.stop_camera

    def counting_stop_camera() -> None:
        calls.append(1)
        original_stop_camera()

    w.stop_camera = counting_stop_camera  # type: ignore[method-assign]

    # Fake "still showing the last live frame" state that only stop_camera()'s
    # cleanup would reset, so the assertions below prove the edge fired
    # rather than merely restating the widgets' initial state.
    w.preview_label.setText("stale live frame")
    w.meters[Hand.RIGHT].setValue(0.7)
    w.state_labels[Hand.RIGHT].setText("Closed")

    controller.running = False
    controller.error = "tracker graph crashed"

    qtbot.waitUntil(lambda: len(calls) >= 1, timeout=1000)
    assert "tracker graph crashed" in w.status_label.text()
    assert w.preview_label.text() == "Camera off"
    for hand in Hand:
        assert w.state_labels[hand].text() == "Not seen"
        assert w.meters[hand].value() == 0.0
    assert w.start_button.isEnabled()
    assert not w.stop_button.isEnabled()
    assert not w.calibrate_button.isEnabled()

    # Cleanup must run exactly once even though the controller stays "dead"
    # for further ticks.
    qtbot.wait(400)
    assert len(calls) == 1


def test_export_button_enabled_only_with_a_calibration_result(window: tuple, qtbot: QtBot) -> None:
    w, controller, _engine = window
    assert not w.export_button.isEnabled()  # disabled at start

    w.start_camera()
    qtbot.waitUntil(lambda: controller.running, timeout=3000)
    assert not w.export_button.isEnabled()

    w.begin_calibration()
    assert not w.export_button.isEnabled()  # disabled again until a result exists

    result = CalibrationResult(levels=Levels(0.9, 0.3), cycles=5, status="ok")
    w.show_result(result)
    assert w.export_button.isEnabled()

    w.stop_camera()
    assert not w.export_button.isEnabled()  # disabled after stop
