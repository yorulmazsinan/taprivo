"""Camera window: device selection, live preview with landmarks, calibration."""

from __future__ import annotations

import csv
import logging
import threading
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, Slot
from PySide6.QtGui import (
    QCloseEvent,
    QColor,
    QFont,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QShowEvent,
)
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from taprivo.config import Config
from taprivo.core.events import Finger, Hand
from taprivo.ui.theme import Palette, resolve
from taprivo.ui.vision_bridge import PreviewPacket, VisionSignals, make_preview_callback
from taprivo.ui.widgets import Meter
from taprivo.vision.calibration import CalibrationResult, CalibrationSession
from taprivo.vision.camera import CameraDevice, default_device, list_devices
from taprivo.vision.controller import VisionController

log = logging.getLogger(__name__)

PREVIEW_W, PREVIEW_H = 480, 360
PREVIEW_RADIUS = 12
TICK_MS = 100
CONNECTIONS = [
    (0, 1),
    (1, 2),
    (2, 3),
    (3, 4),
    (0, 5),
    (5, 6),
    (6, 7),
    (7, 8),
    (5, 9),
    (9, 10),
    (10, 11),
    (11, 12),
    (9, 13),
    (13, 14),
    (14, 15),
    (15, 16),
    (13, 17),
    (17, 18),
    (18, 19),
    (19, 20),
    (0, 17),
]
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
IDLE_HINT_TEXT = "Press Calibrate to tune the open/closed levels for your hand."


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
        self.setWindowTitle("Taprivo Camera")

        if palette is not None:
            self._palette: Palette = palette
        else:
            app = QApplication.instance()
            self._palette = resolve(
                config.hud.theme, app if isinstance(app, QApplication) else None
            )
        palette = self._palette
        self.setMinimumSize(760, 560)

        self.device_combo = QComboBox()
        self.refresh_button = QPushButton("Refresh")
        self.refresh_button.clicked.connect(self.refresh_devices)
        self.preview_label = QLabel("Camera off")
        self.preview_label.setFixedSize(PREVIEW_W, PREVIEW_H)
        self.preview_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview_label.setStyleSheet(
            f"background: {palette.surface_alt}; color: {palette.text_dim}; "
            f"border-radius: {PREVIEW_RADIUS}px;"
        )
        self.fps_label = QLabel("")
        self.fps_label.setStyleSheet(
            f"background: rgba(0, 0, 0, 140); color: {palette.text}; "
            "font-size: 11px; padding: 3px 8px; border-radius: 6px;"
        )
        self.fps_label.setParent(self.preview_label)
        self.fps_label.move(8, PREVIEW_H - 26)
        self.fps_label.hide()

        self.status_label = QLabel("")
        self.status_label.setStyleSheet(f"color: {palette.text_dim}; font-size: 12px;")
        self.levels_label = QLabel("")
        self.levels_label.setStyleSheet(f"color: {palette.text_dim}; font-size: 12px;")
        self.meters: dict[Hand, Meter] = {}
        self.state_labels: dict[Hand, QLabel] = {}
        meters = QGridLayout()
        meters.setHorizontalSpacing(8)
        meters.setVerticalSpacing(6)
        for row, hand in enumerate(Hand):
            meter = Meter(palette=palette)
            meter.setAccessibleName(f"{hand.value} hand openness")
            meter.setColor(palette.accent)
            self.meters[hand] = meter
            state_label = QLabel("Not seen")
            state_label.setStyleSheet(f"color: {palette.text_dim};")
            self.state_labels[hand] = state_label
            hand_label = QLabel(hand.value.title())
            hand_label.setFixedWidth(40)
            meters.addWidget(hand_label, row, 0)
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
        self.calibration_progress = QProgressBar()
        self.calibration_progress.setRange(0, 100)
        self.calibration_progress.setTextVisible(False)
        self.calibration_progress.setFixedHeight(6)
        self.result_levels_label = QLabel("")
        self.result_cycles_label = QLabel("")
        self.result_status_label = QLabel("")
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

        result_card = QHBoxLayout()
        result_card.addWidget(self.result_levels_label)
        result_card.addWidget(self.result_cycles_label)
        result_card.addWidget(self.result_status_label)

        self.calibration_card = QFrame()
        self.calibration_card.setObjectName("card")
        card_layout = QVBoxLayout(self.calibration_card)
        card_layout.setContentsMargins(16, 16, 16, 16)
        card_layout.setSpacing(8)
        card_layout.addWidget(self.prompt_label)
        card_layout.addWidget(self.countdown_label)
        card_layout.addWidget(self.calibration_progress)
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
        self._devices = devices
        self.device_combo.setEnabled(True)
        self.device_combo.clear()
        for device in self._devices:
            self.device_combo.addItem(device.label, device.index)
        preferred = self._config.camera.device_index
        chosen = next((d for d in self._devices if d.index == preferred), None) or default_device(
            self._devices
        )
        if chosen is not None:
            self.device_combo.setCurrentIndex(self._devices.index(chosen))
        self._refreshing = False
        self.refresh_button.setEnabled(True)
        self.start_button.setEnabled(bool(self._devices) and not self._controller.running)

    def selected_device_index(self) -> int | None:
        data = self.device_combo.currentData()
        return int(data) if data is not None else None

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
            self._controller.start(index, preview=make_preview_callback(self.signals))
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
        self.preview_label.setPixmap(QPixmap())
        self.preview_label.setText("Camera off")
        self.fps_label.hide()
        for hand in Hand:
            self.meters[hand].setValue(0.0)
            self.meters[hand].setState("Not seen")
            self.state_labels[hand].setText("Not seen")
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
        self.result_levels_label.setText("")
        self.result_cycles_label.setText("")
        self.result_status_label.setText("")
        self.apply_button.setEnabled(False)
        self.export_button.setEnabled(False)
        return self._session

    def show_result(self, result: CalibrationResult) -> None:
        self._result = result
        self.result_levels_label.setText(
            f"open {result.levels.open_level:.2f} · closed {result.levels.closed_level:.2f}"
        )
        self.result_cycles_label.setText(f"cycles: {result.cycles}")
        self.result_status_label.setText(result.status)
        self._set_prompt("Calibration complete")
        self.countdown_label.setText("")
        self.calibration_progress.setValue(100)
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

    @Slot(object)
    def on_preview(self, packet: object) -> None:
        if not isinstance(packet, PreviewPacket):
            return
        raw = QPixmap.fromImage(packet.image).scaled(
            PREVIEW_W, PREVIEW_H, Qt.AspectRatioMode.KeepAspectRatio
        )
        pixmap = QPixmap(PREVIEW_W, PREVIEW_H)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        clip = QPainterPath()
        clip.addRoundedRect(0, 0, PREVIEW_W, PREVIEW_H, PREVIEW_RADIUS, PREVIEW_RADIUS)
        painter.setClipPath(clip)
        x = (PREVIEW_W - raw.width()) // 2
        y = (PREVIEW_H - raw.height()) // 2
        painter.drawPixmap(x, y, raw)
        if packet.hands:
            w, h = raw.width(), raw.height()
            for hand_frame in packet.hands:
                painter.setPen(QPen(QColor("#4fd1c5"), 2))
                pts = [(x + px * w, y + py * h) for px, py, _ in hand_frame.landmarks]
                for a, b in CONNECTIONS:
                    painter.drawLine(int(pts[a][0]), int(pts[a][1]), int(pts[b][0]), int(pts[b][1]))
                painter.setPen(QPen(QColor("#f6e05e"), 5))
                for px, py in pts:
                    painter.drawPoint(int(px), int(py))
        painter.end()
        self.preview_label.setPixmap(pixmap)
        stats = self._controller.stats()
        self.fps_label.setText(
            f"{stats.processed_fps:.0f} fps · hand {stats.detection_ratio * 100:.0f}%"
        )
        self.fps_label.adjustSize()
        self.fps_label.move(8, PREVIEW_H - self.fps_label.height() - 8)
        self.fps_label.show()
        for hand, hand_state in packet.state.hands.items():
            self.meters[hand].setValue(max(0.0, min(1.0, hand_state.ema)))
            if not hand_state.seen:
                text = "Not seen"
            else:
                text = PHASE_TEXT.get(hand_state.phase, hand_state.phase)
            self.meters[hand].setState(text)
            self.state_labels[hand].setText(text)

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
            self.calibration_progress.setValue(int(max(0.0, min(1.0, prompt.progress)) * 100))
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
