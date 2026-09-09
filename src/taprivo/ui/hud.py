"""Heads-up display window."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import (
    QEasingCurve,
    QParallelAnimationGroup,
    QPoint,
    QPropertyAnimation,
    Qt,
    QTimer,
    QVariantAnimation,
    Slot,
)
from PySide6.QtGui import QAction, QColor, QFont, QKeyEvent, QPainter, QPaintEvent
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QMenu,
    QMessageBox,
    QPushButton,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from taprivo.config import Config
from taprivo.core.energy import EnergyEngine
from taprivo.core.events import Finger, Hand
from taprivo.core.state import AppSnapshot
from taprivo.simulator import KEY_MAP, Simulator
from taprivo.ui.theme import Palette, is_dark, resolve
from taprivo.ui.widgets import Badge, Card, Chip, EnergyBar, HandMap, StatusKind

RENDER_INTERVAL_MS = 33
WINDOW_WIDTH = 480
CONTENT_MARGIN = 20
SECTION_GAP = 12
CARD_MARGIN = 14
ENERGY_PIXEL_SIZE = 40
COMBO_PIXEL_SIZE = 15
SPEND_FLOAT_MS = 600
SPEND_FLOAT_RISE = 24
COMBO_PULSE_MS = 300
COMBO_PULSE_SCALE = 1.06
TRACKING_TEXT = {
    "inactive": "Inactive",
    "simulator": "Keyboard running",
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
#: Badge glyph and short label per tracking state; the full sentence is the tooltip.
TRACKING_BADGE = {
    "inactive": ("◦", "Idle"),
    "simulator": ("⌨", "Keyboard"),
    "tracking": ("◎", "Camera"),
    "stale": ("◎", "Camera stale"),
    "no_signal": ("◎", "No signal"),
}
MCP_TEXT = {"starting": "Starting", "ready": "Ready", "error": "Error"}
MCP_KIND: dict[str, StatusKind] = {"starting": "warn", "ready": "ok", "error": "err"}
MCP_GLYPH = "⇄"


def _toggle_text(running: bool) -> str:
    return "Stop Keyboard" if running else "Start Keyboard"


def _rate_text(snapshot: AppSnapshot) -> str:
    text = f"{snapshot.taps_per_minute} taps/min"
    if snapshot.bpm > 0:
        text += f" · {round(snapshot.bpm)} BPM"
        if snapshot.rhythm_steady:
            text += " steady"
    return text


def _combo_text(snapshot: AppSnapshot) -> str:
    text = f"COMBO x{snapshot.combo}"
    if snapshot.combo_multiplier > 1.0:
        text += f" · {snapshot.combo_multiplier:g}×"
    return text


def _tracking_badge(snapshot: AppSnapshot) -> tuple[str, str]:
    """Glyph and short label; `Camera 29 fps` carries the frame rate while it runs."""
    glyph, short = TRACKING_BADGE[snapshot.tracking]
    if snapshot.tracking == "tracking" and snapshot.camera_fps > 0:
        short = f"Camera {round(snapshot.camera_fps)} fps"
    return glyph, short


class HudWindow(QWidget):
    def __init__(
        self,
        engine: EnergyEngine,
        simulator: Simulator,
        config: Config,
        on_open_camera: Callable[[], None] | None = None,
        *,
        on_open_setup: Callable[[], None] | None = None,
        palette: Palette | None = None,
    ) -> None:
        super().__init__()
        self._engine = engine
        self._simulator = simulator
        self._config = config
        self._pending: AppSnapshot | None = None
        self._latest: AppSnapshot | None = None
        self._rate_steady: bool | None = None
        self._spend_count: int | None = None
        self._combo_multiplier: float | None = None
        self._tap_counts: dict[tuple[Hand, Finger], int] = {}
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

        layout = QVBoxLayout(self)
        layout.setContentsMargins(CONTENT_MARGIN, CONTENT_MARGIN, CONTENT_MARGIN, CONTENT_MARGIN)
        layout.setSpacing(SECTION_GAP)
        layout.addLayout(self._build_header())
        layout.addWidget(self._build_energy_card())
        layout.addWidget(self._build_hands_card())
        layout.addLayout(self._build_toolbar(on_open_camera, on_open_setup))
        layout.addWidget(self._build_footer())

    # -- construction ----------------------------------------------------------

    def _build_header(self) -> QHBoxLayout:
        palette = self._palette
        title = QLabel("⚡ TAPRIVO")
        title_font = QFont()
        title_font.setPixelSize(13)
        title_font.setWeight(QFont.Weight.DemiBold)
        title_font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 2.0)
        title.setFont(title_font)
        wordmark = palette.accent if is_dark(palette) else palette.text
        # The application stylesheet sets a font-size on every QWidget, and QSS
        # beats setFont(), so every sized label repeats its size here.
        title.setStyleSheet(f"color: {wordmark}; font-size: 13px; font-weight: 600;")

        self.tracking_label = Badge(palette=palette)
        self.mcp_label = Badge(palette=palette)
        self.mcp_label.setClickable(True)
        self.mcp_label.clicked.connect(self.show_mcp_status)

        header = QHBoxLayout()
        header.setSpacing(6)
        header.addWidget(title)
        header.addStretch(1)
        header.addWidget(self.tracking_label)
        header.addWidget(self.mcp_label)
        return header

    def _build_energy_card(self) -> Card:
        palette = self._palette
        card = Card(palette=palette)

        self.energy_label = QLabel("0")
        energy_font = QFont()
        energy_font.setPixelSize(ENERGY_PIXEL_SIZE)
        energy_font.setWeight(QFont.Weight.Bold)
        # Tabular figures: a counting number must not jitter as digits change.
        energy_font.setFeature(QFont.Tag("tnum"), 1)
        energy_font.setStyleStrategy(QFont.StyleStrategy.PreferQuality)
        self.energy_label.setFont(energy_font)
        self.energy_label.setStyleSheet(
            f"color: {palette.text}; font-size: {ENERGY_PIXEL_SIZE}px; font-weight: 700;"
        )

        self.energy_cap_label = QLabel(f"/ {self._config.energy.max_energy}")
        cap_font = QFont()
        cap_font.setPixelSize(13)
        cap_font.setFeature(QFont.Tag("tnum"), 1)
        self.energy_cap_label.setFont(cap_font)
        self.energy_cap_label.setStyleSheet(f"color: {palette.text_dim}; font-size: 13px;")
        self.energy_cap_label.setAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignBottom
        )

        self.combo_label = QLabel("COMBO x0")
        self._combo_pixel_size = COMBO_PIXEL_SIZE
        self._apply_combo_size(COMBO_PIXEL_SIZE)

        self.rate_label = QLabel("0 taps/min")
        rate_font = QFont()
        rate_font.setPixelSize(12)
        rate_font.setFeature(QFont.Tag("tnum"), 1)
        self.rate_label.setFont(rate_font)
        self.rate_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self._set_rate_steady(False)

        self.bar = EnergyBar(palette=palette)
        self.bar.setRange(0, self._config.energy.max_energy)
        self.bar.setAccessibleName("Motion Energy")
        self.bar.setReducedMotion(self._config.hud.reduced_motion)

        # Floats up and fades out over a spend; parented to the card so it can
        # sit over the hero number without disturbing the layout.
        self.spend_label = QLabel("", card)
        spend_font = QFont()
        spend_font.setPixelSize(15)
        spend_font.setWeight(QFont.Weight.DemiBold)
        self.spend_label.setFont(spend_font)
        self._spend_rgb = QColor(palette.warn)
        self._spend_alpha = 1.0
        self._apply_spend_alpha(1.0)
        self.spend_label.hide()
        self._spend_animation = QParallelAnimationGroup(self)
        self._spend_move = QPropertyAnimation(self.spend_label, b"pos", self)
        self._spend_move.setDuration(SPEND_FLOAT_MS)
        self._spend_move.setEasingCurve(QEasingCurve.Type.OutCubic)
        # The alpha rides the stylesheet rather than a QGraphicsOpacityEffect:
        # an effect on a child widget is composited at the wrong origin when the
        # window is grabbed, which is how the HUD is screenshotted and tested.
        self._spend_fade = QVariantAnimation(self)
        self._spend_fade.setDuration(SPEND_FLOAT_MS)
        self._spend_fade.setStartValue(1.0)
        self._spend_fade.setEndValue(0.0)
        self._spend_fade.valueChanged.connect(self._on_spend_alpha)
        self._spend_animation.addAnimation(self._spend_move)
        self._spend_animation.addAnimation(self._spend_fade)
        self._spend_animation.finished.connect(self.spend_label.hide)
        # Reduced motion shows the amount flat and takes it away again; the timer
        # is parented so it cannot fire into a destroyed window.
        self._spend_hide = QTimer(self)
        self._spend_hide.setSingleShot(True)
        self._spend_hide.setInterval(SPEND_FLOAT_MS)
        self._spend_hide.timeout.connect(self.spend_label.hide)

        self._combo_pulse = QVariantAnimation(self)
        self._combo_pulse.setDuration(COMBO_PULSE_MS)
        self._combo_pulse.setKeyValueAt(0.0, 1.0)
        self._combo_pulse.setKeyValueAt(0.5, COMBO_PULSE_SCALE)
        self._combo_pulse.setKeyValueAt(1.0, 1.0)
        self._combo_pulse.valueChanged.connect(self._apply_combo_scale)

        top = QHBoxLayout()
        top.setSpacing(6)
        top.addWidget(self.energy_label)
        top.addWidget(self.energy_cap_label)
        top.addStretch(1)

        bottom = QHBoxLayout()
        bottom.addWidget(self.combo_label)
        bottom.addStretch(1)
        bottom.addWidget(self.rate_label)

        inner = QVBoxLayout(card)
        inner.setContentsMargins(CARD_MARGIN, CARD_MARGIN, CARD_MARGIN, CARD_MARGIN)
        inner.setSpacing(10)
        inner.addLayout(top)
        inner.addWidget(self.bar)
        inner.addLayout(bottom)
        return card

    def _build_hands_card(self) -> Card:
        palette = self._palette
        card = Card(palette=palette)
        self.hand_map = HandMap(palette=palette)
        self.hand_map.setReducedMotion(self._config.hud.reduced_motion)

        # The chips the hand map replaced stay alive, hidden, as the textual
        # per-key readout behind the picture (screen readers, tests, debugging).
        self.chips: dict[tuple[Hand, Finger], Chip] = {}
        self._chip_labels: dict[tuple[Hand, Finger], str] = {
            (hand, finger): f"{key} {finger.value.title()}"
            for key, (hand, finger) in KEY_MAP.items()
        }
        for hand, finger in KEY_MAP.values():
            chip = Chip(self, palette=palette)
            chip.setChip(palette.finger[finger], self._chip_labels[(hand, finger)], 0)
            chip.hide()
            self.chips[(hand, finger)] = chip
        self.fingers_label = QLabel("", self)
        self.fingers_label.hide()

        inner = QVBoxLayout(card)
        inner.setContentsMargins(CARD_MARGIN, 10, CARD_MARGIN, 10)
        inner.addWidget(self.hand_map)
        return card

    def _build_toolbar(
        self,
        on_open_camera: Callable[[], None] | None,
        on_open_setup: Callable[[], None] | None,
    ) -> QHBoxLayout:
        self.toggle_button = QPushButton("Start Keyboard")
        self.toggle_button.setObjectName("primary")
        self.toggle_button.clicked.connect(self.toggle_simulator)

        self.open_camera_button = QPushButton("Open Camera")
        if on_open_camera is not None:
            self.open_camera_button.clicked.connect(on_open_camera)
        self.open_camera_button.setEnabled(on_open_camera is not None)

        # The rarely used actions move into an overflow menu so the primary row
        # stays two buttons wide. The attribute names are the action objects.
        self._more_menu = QMenu(self)
        self.reset_button = QAction("Reset Session", self)
        self.reset_button.triggered.connect(self.confirm_reset)
        self.mcp_button = QAction("MCP Status", self)
        self.mcp_button.triggered.connect(self.show_mcp_status)
        self.setup_button = QAction("Setup…", self)
        if on_open_setup is not None:
            self.setup_button.triggered.connect(on_open_setup)
        self.setup_button.setEnabled(on_open_setup is not None)
        for action in (self.reset_button, self.mcp_button, self.setup_button):
            self._more_menu.addAction(action)

        self.more_button = QToolButton()
        self.more_button.setText("⋯")
        self.more_button.setToolTip("More actions")
        self.more_button.setAccessibleName("More actions")
        self.more_button.setMenu(self._more_menu)
        self.more_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.more_button.setMinimumWidth(44)

        toolbar = QHBoxLayout()
        toolbar.setSpacing(8)
        toolbar.addWidget(self.toggle_button, 1)
        toolbar.addWidget(self.open_camera_button, 1)
        toolbar.addWidget(self.more_button)
        return toolbar

    def _build_footer(self) -> QLabel:
        self.footer_label = QLabel(
            "Keys 1-4 left hand, 7-8-9-0 right hand. Balance resets when Taprivo quits."
        )
        self.footer_label.setStyleSheet(f"color: {self._palette.text_dim}; font-size: 11px;")
        self.footer_label.setWordWrap(True)
        return self.footer_label

    # -- painting --------------------------------------------------------------

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802 (Qt override)
        # Opaque, square-cornered background in both themes: a translucent,
        # rounded top-level window would sit under the native macOS title
        # bar's square frame, and a frameless window is out of scope here.
        painter = QPainter(self)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(self._palette.bg))
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
        self.energy_label.setText(str(snapshot.available))
        self.energy_cap_label.setText(f"/ {snapshot.max_energy}")
        self.bar.setValue(snapshot.available)
        self.fingers_label.setText(
            "  ".join(
                f"{finger.value.title()} {snapshot.taps_per_finger.get(finger, 0)}"
                for finger in Finger
            )
        )
        self._render_hands(snapshot)
        self.combo_label.setText(_combo_text(snapshot))
        self.rate_label.setText(_rate_text(snapshot))
        self._set_rate_steady(snapshot.rhythm_steady)
        self._render_badges(snapshot)
        self._render_spend(snapshot)
        self._render_combo_tier(snapshot)
        self.toggle_button.setText(_toggle_text(self._simulator.running))

    def _render_hands(self, snapshot: AppSnapshot) -> None:
        counts = snapshot.taps_per_hand_finger
        for key, chip in self.chips.items():
            color = self._palette.finger[key[1]]
            chip.setChip(color, self._chip_labels[key], counts.get(key, 0))
        for key, count in counts.items():
            if count > self._tap_counts.get(key, 0):
                self.hand_map.flash(*key)
        self._tap_counts = dict(counts)
        self.hand_map.setCounts(counts)

    def _render_badges(self, snapshot: AppSnapshot) -> None:
        glyph, short = _tracking_badge(snapshot)
        self.tracking_label.setStatus(
            TRACKING_KIND[snapshot.tracking],
            glyph,
            short,
            f"Tracking: {TRACKING_TEXT[snapshot.tracking]}",
        )
        self.mcp_label.setStatus(
            MCP_KIND[snapshot.mcp],
            MCP_GLYPH,
            f"MCP {MCP_TEXT[snapshot.mcp].lower()}",
            f"MCP: {MCP_TEXT[snapshot.mcp]}",
        )

    def _render_spend(self, snapshot: AppSnapshot) -> None:
        """A spend floats a `−250` away from the hero number, then fades."""
        previous, self._spend_count = self._spend_count, snapshot.spend_count
        if previous is None or snapshot.spend_count <= previous or snapshot.last_spend is None:
            return
        self.spend_label.setText(f"−{snapshot.last_spend.amount}")
        self.spend_label.adjustSize()
        card = self.spend_label.parentWidget()
        if card is None:  # pragma: no cover - the label is always parented to the card
            return
        # Just right of the "/ 10000", level with the middle of the hero number,
        # so the rise stays inside the card.
        cap = self.energy_cap_label.mapTo(card, QPoint(0, 0))
        top = self.energy_label.mapTo(card, QPoint(0, 0)).y()
        start = QPoint(
            cap.x() + self.energy_cap_label.width() + 14,
            top + self.energy_label.height() // 3,
        )
        self.spend_label.move(start)
        self.spend_label.show()
        self.spend_label.raise_()
        if self._config.hud.reduced_motion:
            self._spend_hide.start()
            return
        self._spend_animation.stop()
        self._apply_spend_alpha(1.0)
        self._spend_move.setStartValue(start)
        self._spend_move.setEndValue(QPoint(start.x(), start.y() - SPEND_FLOAT_RISE))
        self._spend_animation.start()

    def _render_combo_tier(self, snapshot: AppSnapshot) -> None:
        """The combo line pulses once whenever the multiplier tier changes."""
        previous = self._combo_multiplier
        self._combo_multiplier = snapshot.combo_multiplier
        if previous is None or previous == snapshot.combo_multiplier:
            return
        if self._config.hud.reduced_motion:
            return
        self._combo_pulse.stop()
        self._combo_pulse.start()

    def _apply_spend_alpha(self, alpha: float) -> None:
        self._spend_alpha = alpha
        rgb = self._spend_rgb
        self.spend_label.setStyleSheet(
            f"color: rgba({rgb.red()}, {rgb.green()}, {rgb.blue()}, {alpha:.2f}); "
            f"font-size: 15px; font-weight: 600;"
        )

    def _on_spend_alpha(self, value: object) -> None:
        """Restyle in coarse steps; a QSS re-polish every animation frame is not free."""
        if not isinstance(value, float):
            return
        stepped = round(value * 10) / 10
        if stepped != self._spend_alpha:
            self._apply_spend_alpha(stepped)

    def _apply_combo_size(self, pixel_size: int) -> None:
        self._combo_pixel_size = pixel_size
        self.combo_label.setStyleSheet(
            f"color: {self._palette.text}; font-size: {pixel_size}px; font-weight: 600;"
        )

    def _apply_combo_scale(self, scale: object) -> None:
        """Restyle only when the rounded size actually changes; QSS re-polish is not free."""
        if not isinstance(scale, float):
            return
        pixel_size = round(COMBO_PIXEL_SIZE * scale)
        if pixel_size != self._combo_pixel_size:
            self._apply_combo_size(pixel_size)

    def _set_rate_steady(self, steady: bool) -> None:
        """Highlight the rate line while the beat is steady; restyle only on change."""
        if steady == self._rate_steady:
            return
        self._rate_steady = steady
        color = self._palette.accent if steady else self._palette.text_dim
        self.rate_label.setStyleSheet(f"color: {color}; font-size: 12px;")

    # -- actions -------------------------------------------------------------

    @Slot()
    def toggle_simulator(self) -> None:
        if self._simulator.running:
            self._simulator.stop()
        else:
            self._simulator.start()
        self.toggle_button.setText(_toggle_text(self._simulator.running))
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
