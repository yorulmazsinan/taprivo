"""Camera preview widgets: video frame, hand skeleton, squeeze pulses, chrome.

Everything here paints on the GUI thread. The vision worker only hands over a
`QImage` and the landmark tuples it already computed; the skeleton, the squeeze
rings and the preview chrome are drawn in `PreviewView.paintEvent` so no
drawing work happens on the capture thread.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import NamedTuple

from PySide6.QtCore import QElapsedTimer, QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import (
    QColor,
    QFont,
    QImage,
    QPainter,
    QPainterPath,
    QPaintEvent,
    QPen,
)
from PySide6.QtWidgets import QLabel, QWidget

from taprivo.core.events import Finger, Hand
from taprivo.ui.theme import DARK, Palette

PREVIEW_RADIUS = 12
PULSE_MS = 300
REDUCED_PULSE_MS = 150
PULSE_START_RADIUS = 14.0
PULSE_END_RADIUS = 78.0
REDUCED_PULSE_RADIUS = 44.0
PULSE_FRAME_MS = 16
STRIP_HEIGHT = 22

# MediaPipe's standard 21-point hand skeleton.
CONNECTIONS: tuple[tuple[int, int], ...] = (
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
)
FINGERTIPS: dict[int, Finger] = {
    4: Finger.THUMB,
    8: Finger.INDEX,
    12: Finger.MIDDLE,
    16: Finger.RING,
    20: Finger.PINKY,
}
# Wrist plus the four knuckles: their centroid is a stable palm centre.
PALM = (0, 5, 9, 13, 17)
STEPS: tuple[tuple[str, str], ...] = (
    ("visibility", "Visibility"),
    ("open", "Open"),
    ("fist", "Fist"),
    ("squeeze", "Squeeze"),
)


class HandOverlay(NamedTuple):
    """One hand as the preview needs it: identity, points, openness, state word."""

    hand: Hand
    landmarks: tuple[tuple[float, float, float], ...]
    openness: float
    state: str


def hand_color(palette: Palette, hand: Hand) -> str:
    """The colour a hand is drawn in; the meters and glyphs reuse it."""
    return palette.accent if hand is Hand.LEFT else palette.ok


@dataclass
class _Pulse:
    started_ms: int
    center: QPointF  # normalised (0..1) inside the video rect
    color: QColor


class PreviewView(QWidget):
    """The live camera preview: frame, hand skeleton, squeeze pulses, chrome."""

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        palette: Palette = DARK,
        reduced_motion: bool = False,
    ) -> None:
        super().__init__(parent)
        self._palette = palette
        self._reduced_motion = reduced_motion
        self._image: QImage | None = None
        self._hands: tuple[HandOverlay, ...] = ()
        self._pulses: dict[Hand, _Pulse] = {}
        self._text = ""
        self._chrome = ""
        self._live = False
        self._clock = QElapsedTimer()
        self._clock.start()
        self._anim = QTimer(self)
        self._anim.setInterval(PULSE_FRAME_MS)
        self._anim.timeout.connect(self._on_pulse_tick)

    # -- content -------------------------------------------------------------

    def setText(self, text: str) -> None:  # noqa: N802 (QLabel-compatible)
        """Placeholder text, drawn whenever there is no frame."""
        self._text = text
        self.setAccessibleName(text)
        self.update()

    def text(self) -> str:
        return self._text

    def set_frame(self, image: QImage | None) -> None:
        self._image = image if image is not None and not image.isNull() else None
        if self._image is None:
            self._hands = ()
        self.update()

    def has_frame(self) -> bool:
        return self._image is not None

    def set_hands(self, hands: Iterable[HandOverlay]) -> None:
        self._hands = tuple(hands)
        self.update()

    def hands(self) -> tuple[HandOverlay, ...]:
        return self._hands

    def set_chrome(self, fps: float, detection_ratio: float, device: str) -> None:
        parts = [f"{fps:.0f} fps", f"hand {detection_ratio * 100:.0f} %"]
        if device:
            parts.append(device)
        self._chrome = " · ".join(parts)
        self.update()

    def chrome_text(self) -> str:
        return self._chrome

    def clear_chrome(self) -> None:
        self._chrome = ""
        self.update()

    def set_live(self, live: bool) -> None:
        self._live = live
        self.update()

    def is_live(self) -> bool:
        return self._live

    def set_reduced_motion(self, enabled: bool) -> None:
        self._reduced_motion = enabled
        self._anim.setInterval(REDUCED_PULSE_MS if enabled else PULSE_FRAME_MS)
        self.update()

    def is_reduced_motion(self) -> bool:
        return self._reduced_motion

    # -- squeeze pulses ------------------------------------------------------

    def pulse(self, hand: Hand) -> None:
        """Start a squeeze ring for `hand`, centred on its palm."""
        self._pulses[hand] = _Pulse(
            started_ms=self._clock.elapsed(),
            center=self._palm_center(hand),
            color=QColor(hand_color(self._palette, hand)),
        )
        if not self._anim.isActive():
            self._anim.start()
        self.update()

    def active_pulses(self) -> tuple[Hand, ...]:
        now = self._clock.elapsed()
        return tuple(h for h, p in self._pulses.items() if now - p.started_ms < self._pulse_ms())

    def _pulse_ms(self) -> int:
        return REDUCED_PULSE_MS if self._reduced_motion else PULSE_MS

    def _palm_center(self, hand: Hand) -> QPointF:
        for overlay in self._hands:
            if overlay.hand is hand and len(overlay.landmarks) > max(PALM):
                xs = [overlay.landmarks[i][0] for i in PALM]
                ys = [overlay.landmarks[i][1] for i in PALM]
                return QPointF(sum(xs) / len(xs), sum(ys) / len(ys))
        return QPointF(0.5, 0.5)

    def _on_pulse_tick(self) -> None:
        now = self._clock.elapsed()
        span = self._pulse_ms()
        self._pulses = {h: p for h, p in self._pulses.items() if now - p.started_ms < span}
        if not self._pulses:
            self._anim.stop()
        self.update()

    # -- painting ------------------------------------------------------------

    def _video_rect(self) -> QRectF:
        image = self._image
        if image is None or image.width() <= 0 or image.height() <= 0:
            return QRectF(self.rect())
        scale = min(self.width() / image.width(), self.height() / image.height())
        w, h = image.width() * scale, image.height() * scale
        return QRectF((self.width() - w) / 2, (self.height() - h) / 2, w, h)

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802 (Qt override)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        full = QRectF(self.rect())
        clip = QPainterPath()
        clip.addRoundedRect(full, PREVIEW_RADIUS, PREVIEW_RADIUS)
        painter.setClipPath(clip)
        painter.fillPath(clip, QColor(self._palette.surface_alt))

        if self._image is not None:
            painter.drawImage(self._video_rect(), self._image)
            self._paint_hands(painter)
            self._paint_pulses(painter)
            self._paint_chrome(painter, full)
        else:
            painter.setPen(QColor(self._palette.text_dim))
            painter.drawText(full, Qt.AlignmentFlag.AlignCenter, self._text)
        painter.end()

    def _paint_hands(self, painter: QPainter) -> None:
        rect = self._video_rect()
        for overlay in self._hands:
            if len(overlay.landmarks) < 21:
                continue
            points = [
                QPointF(rect.left() + x * rect.width(), rect.top() + y * rect.height())
                for x, y, _ in overlay.landmarks
            ]
            color = QColor(hand_color(self._palette, overlay.hand))
            painter.setPen(QPen(color, 2))
            for a, b in CONNECTIONS:
                painter.drawLine(points[a], points[b])
            painter.setPen(Qt.PenStyle.NoPen)
            for index, point in enumerate(points):
                finger = FINGERTIPS.get(index)
                if finger is not None:
                    painter.setBrush(QColor(self._palette.finger[finger]))
                    radius = 4.5
                else:
                    painter.setBrush(QColor(self._palette.text_dim))
                    radius = 2.5
                painter.drawEllipse(point, radius, radius)
            painter.setBrush(Qt.BrushStyle.NoBrush)

    def _paint_pulses(self, painter: QPainter) -> None:
        rect = self._video_rect()
        now = self._clock.elapsed()
        span = self._pulse_ms()
        for pulse in self._pulses.values():
            progress = (now - pulse.started_ms) / span
            if not 0.0 <= progress < 1.0:
                continue
            color = QColor(pulse.color)
            if self._reduced_motion:
                radius = REDUCED_PULSE_RADIUS
                color.setAlpha(210)
            else:
                radius = PULSE_START_RADIUS + progress * (PULSE_END_RADIUS - PULSE_START_RADIUS)
                color.setAlpha(int(220 * (1.0 - progress)))
            center = QPointF(
                rect.left() + pulse.center.x() * rect.width(),
                rect.top() + pulse.center.y() * rect.height(),
            )
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(color, 3))
            painter.drawEllipse(center, radius, radius)

    def _paint_chrome(self, painter: QPainter, full: QRectF) -> None:
        if self._chrome:
            strip = QRectF(full.left(), full.bottom() - STRIP_HEIGHT, full.width(), STRIP_HEIGHT)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.fillRect(strip, QColor(0, 0, 0, 140))
            font = QFont(painter.font())
            font.setPixelSize(11)
            painter.setFont(font)
            painter.setPen(QColor("#FFFFFF"))
            painter.drawText(
                strip.adjusted(8, 0, -8, 0),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                self._chrome,
            )
        if self._live:
            font = QFont(painter.font())
            font.setPixelSize(11)
            font.setWeight(QFont.Weight.DemiBold)
            painter.setFont(font)
            label = "Live"
            width = painter.fontMetrics().horizontalAdvance(label) + 26
            badge = QRectF(full.right() - width - 8, full.top() + 8, width, 18)
            path = QPainterPath()
            path.addRoundedRect(badge, 9, 9)
            painter.fillPath(path, QColor(0, 0, 0, 150))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(self._palette.ok))
            painter.drawEllipse(QPointF(badge.left() + 11, badge.center().y()), 4, 4)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QColor("#FFFFFF"))
            painter.drawText(
                badge.adjusted(20, 0, -6, 0),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                label,
            )


class HandGlyph(QWidget):
    """A small painted hand, mirrored per side and tinted in the hand's colour."""

    def __init__(
        self, hand: Hand, parent: QWidget | None = None, *, palette: Palette = DARK
    ) -> None:
        super().__init__(parent)
        self._hand = hand
        self._palette = palette
        self.setFixedSize(22, 22)
        self.setAccessibleName(f"{hand.value.title()} hand")
        self.setToolTip(f"{hand.value.title()} hand")

    def hand(self) -> Hand:
        return self._hand

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802 (Qt override)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self._hand is Hand.LEFT:
            painter.translate(self.width(), 0)
            painter.scale(-1, 1)
        color = QColor(hand_color(self._palette, self._hand))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(color)
        painter.drawRoundedRect(QRectF(5, 9, 11, 11), 3, 3)  # palm
        for i in range(4):  # four fingers
            painter.drawRoundedRect(QRectF(5.5 + i * 2.8, 3 + i % 2, 2.0, 8), 1, 1)
        painter.drawRoundedRect(QRectF(1.5, 11, 4.5, 2.2), 1, 1)  # thumb
        painter.end()


class StepPips(QWidget):
    """Four labelled pips that fill as a calibration session advances."""

    def __init__(self, parent: QWidget | None = None, *, palette: Palette = DARK) -> None:
        super().__init__(parent)
        self._palette = palette
        self._filled = 0
        self.setFixedHeight(26)
        self.setAccessibleName("Calibration steps")

    def setStep(self, step: str) -> None:  # noqa: N802 (Qt naming)
        """Fill every pip up to and including `step`; "done" fills them all."""
        names = [name for name, _ in STEPS]
        if step == "done":
            self._filled = len(STEPS)
        else:
            self._filled = names.index(step) + 1 if step in names else 0
        self.update()

    def setFilled(self, count: int) -> None:  # noqa: N802 (Qt naming)
        self._filled = max(0, min(len(STEPS), count))
        self.update()

    def filled(self) -> int:
        return self._filled

    def labels(self) -> tuple[str, ...]:
        return tuple(label for _, label in STEPS)

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802 (Qt override)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        font = QFont(painter.font())
        font.setPixelSize(10)
        painter.setFont(font)
        slot = self.width() / len(STEPS)
        for index, (_, label) in enumerate(STEPS):
            done = index < self._filled
            cx = slot * index + slot / 2
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(self._palette.accent if done else self._palette.surface_alt))
            painter.drawEllipse(QPointF(cx, 7), 4, 4)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QColor(self._palette.text if done else self._palette.text_dim))
            painter.drawText(
                QRectF(slot * index, 14, slot, 12),
                Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter,
                label,
            )
        painter.end()


class Badge(QLabel):
    """A rounded, tinted label. The text is always drawn; colour only adds to it."""

    def __init__(self, parent: QWidget | None = None, *, palette: Palette = DARK) -> None:
        super().__init__(parent)
        self._palette = palette
        self.setBadge("")

    def setBadge(self, text: str, color: str | None = None) -> None:  # noqa: N802 (Qt naming)
        tint = QColor(color) if color else QColor(self._palette.text_dim)
        fill = QColor(tint)
        fill.setAlpha(38)
        self.setStyleSheet(
            f"background-color: rgba({fill.red()}, {fill.green()}, {fill.blue()}, "
            f"{fill.alpha()}); color: {tint.name()}; border-radius: 8px; "
            "padding: 2px 8px; font-size: 11px; font-weight: 600;"
        )
        self.setText(text)
        self.setVisible(bool(text))
        self.setAccessibleName(text)
