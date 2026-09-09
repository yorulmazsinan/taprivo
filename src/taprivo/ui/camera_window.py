"""Camera window: device selection, live preview with landmarks, calibration."""

from __future__ import annotations

import csv
import logging
import threading
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, Slot
from PySide6.QtGui import QCloseEvent, QFont, QShowEvent
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from taprivo.config import Config
from taprivo.core.events import Finger, Hand
from taprivo.ui.preview import (
    Badge,
    HandGlyph,
    HandOverlay,
    PreviewView,
    StepPips,
    hand_color,
)
from taprivo.ui.theme import Palette, resolve
from taprivo.ui.vision_bridge import (
    PreviewPacket,
    VisionSignals,
    make_preview_callback,
    make_taps_callback,
)
from taprivo.ui.widgets import Meter
from taprivo.vision.calibration import CalibrationResult, CalibrationSession
from taprivo.vision.camera import CameraDevice, default_device, list_devices
from taprivo.vision.controller import VisionController
from taprivo.vision.squeeze import HandState

log = logging.getLogger(__name__)

PREVIEW_W, PREVIEW_H = 480, 360
TICK_MS = 100
STATUS_TEXT = {
    "inactive": "Off",
    "tracking": "Tracking",
    "stale": "Stale",
    "no_signal": "No signal",
    "simulator": "Off",
}
PHASE_TEXT = {
    "unknown": "Unknown",
    "open": "Open",
    "closed": "Closed",
}
NOT_SEEN = "Not seen"
IDLE_HINT_TEXT = "Press Calibrate to tune the open/closed levels for your hand."
# Built-in first, iPhone Continuity Cameras last: the list should lead with the
# camera that is actually attached to this Mac.
KIND_ORDER = {"builtin": 0, "external": 1, "unknown": 2, "continuity": 3}


def sort_devices(devices: list[CameraDevice]) -> list[CameraDevice]:
    return sorted(devices, key=lambda d: (KIND_ORDER.get(d.kind, 2), d.index))


class CameraWindow(QWidget):
    def __init__(
        self,
        controller: VisionController,
        config: Config,
        signals: VisionSignals,
        *,
        devices_fn: Callable[[], list[CameraDevice]] = list_devices,
        palette: Palette | None = None,
    ) -> None:
        super().__init__()
        self._controller = controller
        self._config = config
        self._devices_fn = devices_fn
        self._devices: list[CameraDevice] = []
        self._refreshing = False
        self._session: CalibrationSession | None = None
        self._result: CalibrationResult | None = None
        self._last_error = ""
        self._was_running = False
        self.signals = signals
        self.signals.preview.connect(self.on_preview, Qt.ConnectionType.QueuedConnection)
        self.signals.devices.connect(self.on_devices, Qt.ConnectionType.QueuedConnection)
        self.signals.squeeze.connect(self.on_squeeze, Qt.ConnectionType.QueuedConnection)
        self.setWindowTitle("Taprivo Camera")

        if palette is not None:
            self._palette: Palette = palette
        else:
            app = QApplication.instance()
            self._palette = resolve(
                config.hud.theme, app if isinstance(app, QApplication) else None
            )
        palette = self._palette
        # Tall enough for the fixed-size preview, the meters and the whole
        # calibration card: a shorter minimum lets the buttons overlap the
        # preview, because the preview cannot shrink.
        self.setMinimumSize(760, 700)

        self.device_combo = QComboBox()
        self.refresh_button = QPushButton("Refresh")
        self.refresh_button.clicked.connect(self.refresh_devices)
        self.preview_label = PreviewView(palette=palette, reduced_motion=config.hud.reduced_motion)
        self.preview_label.setFixedSize(PREVIEW_W, PREVIEW_H)
        self.preview_label.setText("Camera off")

        self.status_label = QLabel("")
        self.status_label.setStyleSheet(f"color: {palette.text_dim}; font-size: 12px;")
        self.levels_label = QLabel("")
        self.levels_label.setStyleSheet(f"color: {palette.text_dim}; font-size: 12px;")
        self.meters: dict[Hand, Meter] = {}
        self.hand_glyphs: dict[Hand, HandGlyph] = {}
        self.state_labels: dict[Hand, QLabel] = {}
        meters = QGridLayout()
        meters.setHorizontalSpacing(8)
        meters.setVerticalSpacing(6)
        for row, hand in enumerate(Hand):
            meter = Meter(palette=palette)
            meter.setAccessibleName(f"{hand.value} hand openness")
            meter.setColor(hand_color(palette, hand))
            self.meters[hand] = meter
            state_label = QLabel(NOT_SEEN)
            state_label.setFixedWidth(64)
            self.state_labels[hand] = state_label
            self._paint_state(hand, NOT_SEEN)
            glyph = HandGlyph(hand, palette=palette)
            self.hand_glyphs[hand] = glyph
            meters.addWidget(glyph, row, 0)
            meters.addWidget(meter, row, 1)
            meters.addWidget(state_label, row, 2)

        self.start_button = QPushButton("Start Camera")
        self.start_button.setObjectName("primary")
        self.start_button.clicked.connect(self.start_camera)
        self.stop_button = QPushButton("Stop Camera")
        self.stop_button.clicked.connect(self.stop_camera)
        self.stop_button.setEnabled(False)
        self.calibrate_button = QPushButton("Calibrate")
        self.calibrate_button.clicked.connect(self.begin_calibration)
        self.calibrate_button.setEnabled(False)

        self.prompt_label = QLabel("")
        prompt_font = QFont()
        prompt_font.setPixelSize(18)
        prompt_font.setWeight(QFont.Weight.DemiBold)
        self.prompt_label.setFont(prompt_font)
        self.countdown_label = QLabel("")
        countdown_font = QFont()
        countdown_font.setPixelSize(40)
        countdown_font.setWeight(QFont.Weight.Bold)
        self.countdown_label.setFont(countdown_font)
        self.countdown_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.step_pips = StepPips(palette=palette)
        self.step_pips.setFixedWidth(320)
        self.result_levels_label = Badge(palette=palette)
        self.result_cycles_label = Badge(palette=palette)
        self.result_status_label = Badge(palette=palette)
        self.apply_button = QPushButton("Apply")
        self.apply_button.clicked.connect(self.apply_calibration)
        self.apply_button.setEnabled(False)
        self.defaults_button = QPushButton("Use defaults")
        self.defaults_button.clicked.connect(self.use_defaults)
        self.export_button = QPushButton("Export CSV…")
        self.export_button.clicked.connect(lambda: self.export_calibration(None))
        self.export_button.setEnabled(False)

        top = QHBoxLayout()
        top.addWidget(QLabel("Device"))
        top.addWidget(self.device_combo, 1)
        top.addWidget(self.refresh_button)

        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        for b in (self.start_button, self.stop_button, self.calibrate_button):
            buttons.addWidget(b)

        cal_buttons = QHBoxLayout()
        cal_buttons.setSpacing(8)
        for b in (self.apply_button, self.defaults_button, self.export_button):
            cal_buttons.addWidget(b)

        body = QHBoxLayout()
        body.setSpacing(16)
        left = QVBoxLayout()
        left.addWidget(self.preview_label)
        left.addWidget(self.status_label)
        body.addLayout(left)
        right = QVBoxLayout()
        right.addWidget(self.levels_label)
        right.addLayout(meters)
        right.addStretch(1)
        body.addLayout(right)

        pips_row = QHBoxLayout()
        pips_row.addStretch(1)
        pips_row.addWidget(self.step_pips)
        pips_row.addStretch(1)

        result_card = QHBoxLayout()
        result_card.setSpacing(6)
        result_card.addWidget(self.result_levels_label)
        result_card.addWidget(self.result_cycles_label)
        result_card.addWidget(self.result_status_label)
        result_card.addStretch(1)

        self.calibration_card = QFrame()
        self.calibration_card.setObjectName("card")
        card_layout = QVBoxLayout(self.calibration_card)
        card_layout.setContentsMargins(16, 16, 16, 16)
        card_layout.setSpacing(8)
        card_layout.addWidget(self.prompt_label)
        card_layout.addWidget(self.countdown_label)
        card_layout.addLayout(pips_row)
        card_layout.addLayout(result_card)
        card_layout.addLayout(cal_buttons)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)
        layout.addLayout(top)
        layout.addLayout(body)
        layout.addLayout(buttons)
        layout.addWidget(self.calibration_card)

        self._timer = QTimer(self)
        self._timer.setInterval(TICK_MS)
        self._timer.timeout.connect(self._tick)
        self._timer.start()
        self.refresh_devices()
        self._render_status()
        self._render_levels()
        self._show_idle_hint()

    # -- prompt --------------------------------------------------------------

    def _set_prompt(self, text: str, *, dim: bool = False) -> None:
        self.prompt_label.setStyleSheet(f"color: {self._palette.text_dim};" if dim else "")
        self.prompt_label.setText(text)

    def _show_idle_hint(self) -> None:
        self._set_prompt(IDLE_HINT_TEXT, dim=True)

    # -- devices -------------------------------------------------------------

    @Slot()
    def refresh_devices(self) -> None:
        if self._refreshing:
            return
        self._refreshing = True
        self.refresh_button.setEnabled(False)
        self.start_button.setEnabled(False)
        self.device_combo.clear()
        self.device_combo.addItem("Refreshing…")
        self.device_combo.setEnabled(False)
        devices_fn = self._devices_fn
        signals = self.signals

        def probe() -> None:
            try:
                devices = devices_fn()
            except Exception:
                log.exception("device probing failed")
                devices = []
            signals.devices.emit(devices)

        threading.Thread(target=probe, name="taprivo-camera-probe", daemon=True).start()

    @Slot(object)
    def on_devices(self, devices: object) -> None:
        if not isinstance(devices, list):
            return
        # The combo is reordered for display, but the default is chosen from the
        # capture-index order so that prefer_builtin, not the sort, decides it.
        self._devices = sort_devices(devices)
        self.device_combo.setEnabled(True)
        self.device_combo.clear()
        for device in self._devices:
            self.device_combo.addItem(device.label, device.index)
        preferred = self._config.camera.device_index
        chosen = next((d for d in self._devices if d.index == preferred), None) or default_device(
            devices, prefer_builtin=self._config.camera.prefer_builtin
        )
        if chosen is not None:
            self.device_combo.setCurrentIndex(self._devices.index(chosen))
        self._refreshing = False
        self.refresh_button.setEnabled(True)
        self.start_button.setEnabled(bool(self._devices) and not self._controller.running)

    def selected_device_index(self) -> int | None:
        data = self.device_combo.currentData()
        return int(data) if data is not None else None

    def selected_device_name(self) -> str:
        """The plain device name for the preview strip, without the resolution."""
        index = self.selected_device_index()
        device = next((d for d in self._devices if d.index == index), None)
        if device is not None and device.name:
            return device.name
        return self.device_combo.currentText().split(" (")[0]

    # -- camera --------------------------------------------------------------

    @Slot()
    def start_camera(self) -> None:
        if self._refreshing:
            return
        index = self.selected_device_index()
        if index is None:
            return
        self._last_error = ""
        try:
            self._controller.start(
                index,
                preview=make_preview_callback(self.signals),
                taps=make_taps_callback(self.signals),
            )
        except Exception as exc:
            log.exception("failed to start camera")
            self._last_error = str(exc)
            QMessageBox.warning(self, "Camera", str(exc))
        self._render_status()

    @Slot()
    def stop_camera(self) -> None:
        self._controller.stop()
        self._was_running = False
        self._session = None
        self.preview_label.set_frame(None)
        self.preview_label.setText("Camera off")
        self.preview_label.clear_chrome()
        for hand in Hand:
            self.meters[hand].setValue(0.0)
            self._paint_state(hand, NOT_SEEN)
        self.export_button.setEnabled(False)
        self._render_status()
        self._show_idle_hint()

    # -- calibration ---------------------------------------------------------

    @Slot()
    def begin_calibration(self) -> CalibrationSession | None:
        if not self._controller.running:
            return None
        self._session = self._controller.begin_calibration()
        self._result = None
        self.result_levels_label.setBadge("")
        self.result_cycles_label.setBadge("")
        self.result_status_label.setBadge("")
        self.step_pips.setFilled(0)
        self.apply_button.setEnabled(False)
        self.export_button.setEnabled(False)
        return self._session

    def show_result(self, result: CalibrationResult) -> None:
        self._result = result
        self.result_levels_label.setBadge(
            f"open {result.levels.open_level:.2f} · closed {result.levels.closed_level:.2f}",
            self._palette.text,
        )
        self.result_cycles_label.setBadge(f"cycles: {result.cycles}", self._palette.text)
        self.result_status_label.setBadge(
            result.status,
            self._palette.ok if result.status == "ok" else self._palette.warn,
        )
        self._set_prompt("Calibration complete")
        self.countdown_label.setText("")
        self.step_pips.setStep("done")
        self.apply_button.setEnabled(True)
        self.export_button.setEnabled(True)

    @Slot()
    def apply_calibration(self) -> None:
        if self._result is not None:
            self._controller.apply_calibration(self._result)
            self._set_prompt("Levels applied for this session")
            self._render_levels()

    @Slot()
    def use_defaults(self) -> None:
        self._controller.reset_levels()
        self._set_prompt("Default levels in use")
        self._render_levels()

    def export_calibration(self, path: Path | None) -> None:
        if self._session is None:
            return
        if path is None:
            chosen, _ = QFileDialog.getSaveFileName(
                self, "Export calibration features", "taprivo-calibration.csv", "CSV (*.csv)"
            )
            if not chosen:
                return
            path = Path(chosen)
        rows = self._session.export_rows()
        fields = ["ts_ms", "step", "hand", "openness", *[f.value for f in Finger]]
        with path.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=fields)
            writer.writeheader()
            for row in rows:
                writer.writerow({k: row.get(k, "") for k in fields})

    # -- rendering -----------------------------------------------------------

    @staticmethod
    def _state_text(state: HandState | None) -> str:
        if state is None or not state.seen:
            return NOT_SEEN
        return PHASE_TEXT.get(state.phase, state.phase)

    def _state_color(self, text: str) -> str:
        if text == PHASE_TEXT["open"]:
            return self._palette.ok
        if text == PHASE_TEXT["closed"]:
            return self._palette.accent
        return self._palette.text_dim

    def _paint_state(self, hand: Hand, text: str) -> None:
        """Colour carries the phase; the word is always spelled out beside it."""
        label = self.state_labels[hand]
        label.setStyleSheet(f"color: {self._state_color(text)}; font-size: 12px;")
        label.setText(text)
        label.setAccessibleName(f"{hand.value} hand {text}")

    @Slot(object)
    def on_preview(self, packet: object) -> None:
        if not isinstance(packet, PreviewPacket):
            return
        states = packet.state.hands
        self.preview_label.set_frame(packet.image)
        self.preview_label.set_hands(
            HandOverlay(
                frame.hand,
                frame.landmarks,
                frame.features.openness,
                self._state_text(states.get(frame.hand)),
            )
            for frame in packet.hands
        )
        stats = self._controller.stats()
        self.preview_label.set_chrome(
            stats.processed_fps, stats.detection_ratio, self.selected_device_name()
        )
        for hand, hand_state in states.items():
            self.meters[hand].setValue(max(0.0, min(1.0, hand_state.ema)))
            self._paint_state(hand, self._state_text(hand_state))

    @Slot(object)
    def on_squeeze(self, hand: object) -> None:
        if isinstance(hand, Hand):
            self.preview_label.pulse(hand)

    def status_text(self) -> str:
        stats = self._controller.stats()
        status = self._controller.tracking_status()
        text = f"Camera: {STATUS_TEXT.get(status, status)}"
        if self._controller.running:
            text += f"  {stats.processed_fps:.0f} fps  hand {stats.detection_ratio * 100:.0f}%"
        error = self._controller.error or self._last_error
        if error:
            text += f"  ({error})"
        return text

    def _render_status(self) -> None:
        running = self._controller.running
        self.start_button.setEnabled(bool(self._devices) and not running and not self._refreshing)
        self.stop_button.setEnabled(running)
        self.calibrate_button.setEnabled(running)
        self.preview_label.set_live(running)
        self.status_label.setText(self.status_text())

    def _render_levels(self) -> None:
        levels = self._controller.levels()
        self.levels_label.setText(
            f"open {levels.open_level:.2f} · closed {levels.closed_level:.2f}"
        )
        for meter in self.meters.values():
            meter.setLevels(levels.open_level, levels.closed_level)

    def _tick(self) -> None:
        try:
            running = self._controller.running
            if self._was_running and not running:
                # The worker died on its own (an uncaught exception in the
                # capture loop) rather than via an explicit Stop click: run
                # the same cleanup stop_camera() does, once, so the preview
                # and meters do not freeze on stale state. Capture the reason
                # first: stopping the controller discards its worker and error.
                self._last_error = self._controller.error or self._last_error
                self.stop_camera()
                return
            self._was_running = running
            self._render_status()
            session = self._session
            if session is None:
                return
            prompt = session.prompt(self._controller.now_ms())
            self._set_prompt(prompt.text)
            self.countdown_label.setText(
                f"{prompt.remaining_ms / 1000:.1f} s" if prompt.remaining_ms else ""
            )
            self.step_pips.setStep(prompt.step)
            if session.finished and self._result is None:
                result = session.result()
                if result is not None:
                    self.show_result(result)
        except Exception:
            log.exception("camera window tick failed")

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802 (Qt override)
        self._timer.stop()
        self.stop_camera()
        super().closeEvent(event)

    def showEvent(self, event: QShowEvent) -> None:  # noqa: N802 (Qt override)
        super().showEvent(event)
        # app.py constructs one CameraWindow and reuses it for every
        # "Open Camera" click; closeEvent stops the tick timer, so it must be
        # restarted here or the calibration UI is dead after a reopen.
        if not self._timer.isActive():
            self._timer.start()
