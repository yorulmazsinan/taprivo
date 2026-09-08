"""Camera window: device selection, live preview with landmarks, calibration."""

from __future__ import annotations

import csv
import logging
import threading
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, Slot
from PySide6.QtGui import QCloseEvent, QColor, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from taprivo.config import Config
from taprivo.core.events import Finger
from taprivo.ui.vision_bridge import PreviewPacket, VisionSignals, make_preview_callback
from taprivo.vision.calibration import CalibrationResult, CalibrationSession
from taprivo.vision.camera import CameraDevice, default_device, list_devices
from taprivo.vision.controller import VisionController

log = logging.getLogger(__name__)

PREVIEW_W, PREVIEW_H = 320, 240
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


class CameraWindow(QWidget):
    def __init__(
        self,
        controller: VisionController,
        config: Config,
        signals: VisionSignals,
        *,
        devices_fn: Callable[[], list[CameraDevice]] = list_devices,
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
        self.signals = signals
        self.signals.preview.connect(self.on_preview, Qt.ConnectionType.QueuedConnection)
        self.signals.devices.connect(self.on_devices, Qt.ConnectionType.QueuedConnection)
        self.setWindowTitle("Taprivo Camera")

        self.device_combo = QComboBox()
        self.refresh_button = QPushButton("Refresh")
        self.refresh_button.clicked.connect(self.refresh_devices)
        self.preview_label = QLabel("Camera off")
        self.preview_label.setFixedSize(PREVIEW_W, PREVIEW_H)
        self.preview_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview_label.setStyleSheet("background: #222; color: #ccc;")
        self.status_label = QLabel("")
        self.bars: dict[Finger, QProgressBar] = {}
        bars = QGridLayout()
        for row, finger in enumerate(Finger):
            bar = QProgressBar()
            bar.setRange(0, 100)
            bar.setTextVisible(False)
            bar.setAccessibleName(f"{finger.value} movement")
            if config.hud.reduced_motion:
                bar.setStyleSheet("QProgressBar::chunk { margin: 0px; }")
            self.bars[finger] = bar
            bars.addWidget(QLabel(finger.value.title()), row, 0)
            bars.addWidget(bar, row, 1)

        self.start_button = QPushButton("Start Camera")
        self.start_button.clicked.connect(self.start_camera)
        self.stop_button = QPushButton("Stop Camera")
        self.stop_button.clicked.connect(self.stop_camera)
        self.stop_button.setEnabled(False)
        self.calibrate_button = QPushButton("Calibrate")
        self.calibrate_button.clicked.connect(self.begin_calibration)
        self.calibrate_button.setEnabled(False)

        self.prompt_label = QLabel("")
        self.prompt_label.setStyleSheet("font-size: 15px; font-weight: 600;")
        self.countdown_label = QLabel("")
        self.result_table = QTableWidget(0, 5)
        self.result_table.setHorizontalHeaderLabels(
            ["Finger", "Noise sigma", "Amplitude", "Taps", "Threshold"]
        )
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
        for b in (self.start_button, self.stop_button, self.calibrate_button):
            buttons.addWidget(b)
        cal_buttons = QHBoxLayout()
        for b in (self.apply_button, self.defaults_button, self.export_button):
            cal_buttons.addWidget(b)
        body = QHBoxLayout()
        left = QVBoxLayout()
        left.addWidget(self.preview_label)
        left.addWidget(self.status_label)
        body.addLayout(left)
        body.addLayout(bars)
        layout = QVBoxLayout(self)
        layout.addLayout(top)
        layout.addLayout(body)
        layout.addLayout(buttons)
        layout.addWidget(self.prompt_label)
        layout.addWidget(self.countdown_label)
        layout.addWidget(self.result_table)
        layout.addLayout(cal_buttons)

        self._timer = QTimer(self)
        self._timer.setInterval(TICK_MS)
        self._timer.timeout.connect(self._tick)
        self._timer.start()
        self.refresh_devices()
        self._render_status()

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
        self._session = None
        self.preview_label.setPixmap(QPixmap())
        self.preview_label.setText("Camera off")
        for bar in self.bars.values():
            bar.setValue(0)
        self._render_status()

    # -- calibration ---------------------------------------------------------

    @Slot()
    def begin_calibration(self) -> CalibrationSession | None:
        if not self._controller.running:
            return None
        self._session = self._controller.begin_calibration()
        self._result = None
        self.result_table.setRowCount(0)
        self.apply_button.setEnabled(False)
        self.export_button.setEnabled(True)
        return self._session

    def show_result(self, result: CalibrationResult) -> None:
        self._result = result
        self.result_table.setRowCount(len(Finger))
        for row, finger in enumerate(Finger):
            fc = result.fingers[finger]
            cells = [
                finger.value.title(),
                f"{fc.sigma:.3f}",
                f"{fc.median_amplitude:.3f}",
                str(fc.taps),
                f"{fc.threshold:.2f} ({fc.status})",
            ]
            for col, text in enumerate(cells):
                self.result_table.setItem(row, col, QTableWidgetItem(text))
        self.prompt_label.setText("Calibration complete")
        self.countdown_label.setText("")
        self.apply_button.setEnabled(True)

    @Slot()
    def apply_calibration(self) -> None:
        if self._result is not None:
            self._controller.apply_calibration(self._result)
            self.prompt_label.setText("Thresholds applied for this session")

    @Slot()
    def use_defaults(self) -> None:
        self._controller.reset_thresholds()
        self.prompt_label.setText("Default thresholds in use")

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
        fields = ["ts_ms", "step", "finger", *[f.value for f in Finger]]
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
        pixmap = QPixmap.fromImage(packet.image).scaled(
            PREVIEW_W, PREVIEW_H, Qt.AspectRatioMode.KeepAspectRatio
        )
        if packet.hand is not None:
            painter = QPainter(pixmap)
            painter.setPen(QPen(QColor("#4fd1c5"), 2))
            w, h = pixmap.width(), pixmap.height()
            pts = [(x * w, y * h) for x, y, _ in packet.hand.landmarks]
            for a, b in CONNECTIONS:
                painter.drawLine(int(pts[a][0]), int(pts[a][1]), int(pts[b][0]), int(pts[b][1]))
            painter.setPen(QPen(QColor("#f6e05e"), 5))
            for x, y in pts:
                painter.drawPoint(int(x), int(y))
            painter.end()
        self.preview_label.setPixmap(pixmap)
        for finger, state in packet.state.fingers.items():
            ratio = min(abs(state.deviation) / state.threshold, 2.0) if state.threshold else 0.0
            self.bars[finger].setValue(int(ratio * 50))

    def status_text(self) -> str:
        stats = self._controller.stats()
        status = self._controller.tracking_status()
        text = f"Camera: {STATUS_TEXT.get(status, status)}"
        if self._controller.running:
            text += f"  {stats.processed_fps:.0f} fps  hand {stats.detection_ratio * 100:.0f}%"
        if self._last_error:
            text += f"  ({self._last_error})"
        return text

    def _render_status(self) -> None:
        running = self._controller.running
        self.start_button.setEnabled(bool(self._devices) and not running and not self._refreshing)
        self.stop_button.setEnabled(running)
        self.calibrate_button.setEnabled(running)
        self.status_label.setText(self.status_text())

    def _tick(self) -> None:
        try:
            self._render_status()
            session = self._session
            if session is None:
                return
            prompt = session.prompt(self._controller.now_ms())
            self.prompt_label.setText(prompt.text)
            self.countdown_label.setText(
                f"{prompt.remaining_ms / 1000:.1f} s" if prompt.remaining_ms else ""
            )
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
