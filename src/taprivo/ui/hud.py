"""Heads-up display window."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Qt, QTimer, Slot
from PySide6.QtGui import QColor, QFont, QKeyEvent, QPainter, QPaintEvent
from PySide6.QtWidgets import (
    QApplication,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from taprivo.config import Config
from taprivo.core.energy import EnergyEngine
from taprivo.core.events import Finger
from taprivo.core.state import AppSnapshot
from taprivo.simulator import Simulator
from taprivo.ui.theme import Palette, resolve
from taprivo.ui.widgets import Chip, EnergyBar, StatusDot, StatusKind

RENDER_INTERVAL_MS = 33
WINDOW_WIDTH = 380
CONTENT_MARGIN = 16
TRACKING_TEXT = {
    "inactive": "Inactive",
    "simulator": "Simulator running",
    "tracking": "Tracking",
    "stale": "Stale (disconnected)",
    "no_signal": "No signal",
}
TRACKING_KIND: dict[str, StatusKind] = {
    "inactive": "off",
    "simulator": "ok",
    "tracking": "ok",
    "stale": "warn",
    "no_signal": "err",
}
MCP_TEXT = {"starting": "Starting", "ready": "Ready", "error": "Error"}
MCP_KIND: dict[str, StatusKind] = {"starting": "warn", "ready": "ok", "error": "err"}


class HudWindow(QWidget):
    def __init__(
        self,
        engine: EnergyEngine,
        simulator: Simulator,
        config: Config,
        on_open_camera: Callable[[], None] | None = None,
        *,
        palette: Palette | None = None,
    ) -> None:
        super().__init__()
        self._engine = engine
        self._simulator = simulator
        self._config = config
        self._pending: AppSnapshot | None = None
        self._latest: AppSnapshot | None = None
        if palette is not None:
            self._palette: Palette = palette
        else:
            app = QApplication.instance()
            qt_app = app if isinstance(app, QApplication) else None
            self._palette = resolve(config.hud.theme, qt_app)

        self.setWindowTitle("Taprivo")
        flags = Qt.WindowType.Window
        if config.hud.always_on_top:
            flags |= Qt.WindowType.WindowStaysOnTopHint
        self.setWindowFlags(flags)
        self.setWindowOpacity(config.hud.opacity)
        self.setFixedWidth(WINDOW_WIDTH)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        self._render_timer = QTimer(self)
        self._render_timer.setSingleShot(True)
        self._render_timer.setInterval(RENDER_INTERVAL_MS)
        self._render_timer.timeout.connect(self._render)

        palette = self._palette

        header = QHBoxLayout()
        title = QLabel("⚡ TAPRIVO")
        title_font = QFont()
        title_font.setPixelSize(13)
        title_font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 1.5)
        title.setFont(title_font)
        title.setStyleSheet(f"color: {palette.text_dim};")
        self.energy_label = QLabel("0 / 0")
        energy_font = QFont()
        energy_font.setPixelSize(34)
        energy_font.setWeight(QFont.Weight.Bold)
        energy_font.setStyleHint(QFont.StyleHint.Monospace)
        self.energy_label.setFont(energy_font)
        self.energy_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        header.addWidget(title)
        header.addStretch(1)
        header.addWidget(self.energy_label)

        self.bar = EnergyBar(palette=palette)
        self.bar.setRange(0, config.energy.max_energy)
        self.bar.setAccessibleName("Motion Energy")
        self.bar.setReducedMotion(config.hud.reduced_motion)

        self.fingers_label = QLabel("")
        self.fingers_label.hide()
        self.chips: dict[Finger, Chip] = {}
        chip_row = QGridLayout()
        chip_row.setHorizontalSpacing(8)
        chip_row.setVerticalSpacing(8)
        for index, finger in enumerate(Finger):
            chip = Chip(palette=palette)
            chip.setChip(palette.finger[finger], finger.value.title(), 0)
            self.chips[finger] = chip
            chip_row.addWidget(chip, index // 3, index % 3)

        self.combo_label = QLabel("COMBO x0")
        self.rate_label = QLabel("0 taps/min")
        rate_font = QFont()
        rate_font.setStyleHint(QFont.StyleHint.Monospace)
        self.rate_label.setFont(rate_font)
        self.rate_label.setStyleSheet(f"color: {palette.text_dim}; font-size: 12px;")
        self.combo_label.setStyleSheet("font-size: 12px;")
        self.rate_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        rhythm = QHBoxLayout()
        rhythm.addWidget(self.combo_label)
        rhythm.addWidget(self.rate_label)

        self.tracking_label = StatusDot(palette=palette)
        self.mcp_label = StatusDot(palette=palette)
        status = QHBoxLayout()
        status.addWidget(self.tracking_label)
        status.addStretch(1)
        status.addWidget(self.mcp_label)

        self.toggle_button = QPushButton("Start Simulator")
        self.toggle_button.setObjectName("primary")
        self.toggle_button.clicked.connect(self.toggle_simulator)
        self.reset_button = QPushButton("Reset Session")
        self.reset_button.clicked.connect(self.confirm_reset)
        self.mcp_button = QPushButton("MCP Status")
        self.mcp_button.clicked.connect(self.show_mcp_status)
        self.open_camera_button = QPushButton("Open Camera")
        if on_open_camera is not None:
            self.open_camera_button.clicked.connect(on_open_camera)
        self.open_camera_button.setEnabled(on_open_camera is not None)
        buttons = QGridLayout()
        buttons.setHorizontalSpacing(8)
        buttons.setVerticalSpacing(8)
        for index, button in enumerate(
            (
                self.toggle_button,
                self.reset_button,
                self.mcp_button,
                self.open_camera_button,
            )
        ):
            buttons.addWidget(button, index // 2, index % 2)

        self.footer_label = QLabel(
            "Keys 1-5 tap thumb…pinky (simulator). Balance resets when Taprivo quits."
        )
        self.footer_label.setStyleSheet(f"color: {palette.text_dim}; font-size: 11px;")
        self.footer_label.setWordWrap(True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(CONTENT_MARGIN, CONTENT_MARGIN, CONTENT_MARGIN, CONTENT_MARGIN)
        layout.setSpacing(8)
        layout.addLayout(header)
        layout.addWidget(self.bar)
        layout.addLayout(chip_row)
        layout.addWidget(self.fingers_label)
        layout.addLayout(rhythm)
        layout.addLayout(status)
        layout.addLayout(buttons)
        layout.addWidget(self.footer_label)

    # -- painting --------------------------------------------------------------

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802 (Qt override)
        # Opaque, square-cornered background in both themes: a translucent,
        # rounded top-level window would sit under the native macOS title
        # bar's square frame, and a frameless window is out of scope here.
        painter = QPainter(self)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(self._palette.surface))
        painter.drawRect(self.rect())
        painter.end()
        super().paintEvent(event)

    # -- input ---------------------------------------------------------------

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802 (Qt override)
        if event.isAutoRepeat():
            return
        if self._simulator.tap_key(event.text()) is None:
            super().keyPressEvent(event)

    # -- state ---------------------------------------------------------------

    @Slot(object)
    def on_snapshot(self, snapshot: object) -> None:
        if not isinstance(snapshot, AppSnapshot):
            return
        self._pending = snapshot
        if not self._render_timer.isActive():
            self._render_timer.start()

    def _render(self) -> None:
        snapshot = self._pending
        if snapshot is None:
            return
        self._pending = None
        self._latest = snapshot
        self.energy_label.setText(f"{snapshot.available} / {snapshot.max_energy}")
        self.bar.setValue(snapshot.available)
        self.fingers_label.setText(
            "  ".join(
                f"{finger.value.title()} {snapshot.taps_per_finger.get(finger, 0)}"
                for finger in Finger
            )
        )
        for finger in Finger:
            count = snapshot.taps_per_finger.get(finger, 0)
            self.chips[finger].setChip(self._palette.finger[finger], finger.value.title(), count)
        self.combo_label.setText(f"COMBO x{snapshot.combo}")
        self.rate_label.setText(f"{snapshot.taps_per_minute} taps/min")
        self.tracking_label.setStatus(
            TRACKING_KIND[snapshot.tracking], f"Tracking: {TRACKING_TEXT[snapshot.tracking]}"
        )
        self.mcp_label.setStatus(MCP_KIND[snapshot.mcp], f"MCP: {MCP_TEXT[snapshot.mcp]}")
        self.toggle_button.setText(
            "Stop Simulator" if self._simulator.running else "Start Simulator"
        )

    # -- actions -------------------------------------------------------------

    @Slot()
    def toggle_simulator(self) -> None:
        if self._simulator.running:
            self._simulator.stop()
        else:
            self._simulator.start()
        self.toggle_button.setText(
            "Stop Simulator" if self._simulator.running else "Start Simulator"
        )
        self.setFocus()

    @Slot()
    def confirm_reset(self) -> None:
        answer = QMessageBox.question(
            self,
            "Reset session",
            "Start a new session with zero energy? Agents holding the old session id "
            "will be told it changed.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer == QMessageBox.StandardButton.Yes:
            self._engine.reset_session()

    def mcp_status_text(self) -> str:
        snapshot = self._latest or self._engine.snapshot()
        lines = [
            f"Endpoint: {self._config.endpoint_url}",
            f"Status: {MCP_TEXT[snapshot.mcp]}",
        ]
        if snapshot.mcp_error:
            lines.append(f"Error: {snapshot.mcp_error}")
        last = snapshot.last_tool_call_utc.isoformat() if snapshot.last_tool_call_utc else "never"
        lines.append(f"Last tool call: {last}")
        lines.append("Run 'taprivo doctor' in a terminal for a full check.")
        return "\n".join(lines)

    @Slot()
    def show_mcp_status(self) -> None:
        QMessageBox.information(self, "MCP status", self.mcp_status_text())
