"""Custom-painted widgets used by the HUD and camera window."""

from __future__ import annotations

from typing import Literal

from PySide6.QtCore import QEvent, QPointF, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import (
    QColor,
    QFont,
    QHelpEvent,
    QLinearGradient,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPaintEvent,
    QPen,
    QResizeEvent,
)
from PySide6.QtWidgets import QFrame, QGraphicsDropShadowEffect, QLabel, QToolTip, QWidget

from taprivo.core.events import Finger, Hand
from taprivo.simulator import KEY_MAP
from taprivo.ui.theme import DARK, Palette

StatusKind = Literal["ok", "warn", "err", "off"]


def blend(base: QColor, tint: QColor, ratio: float) -> QColor:
    """Mix `tint` into `base`; ratio 0 keeps the base, 1 returns the tint."""
    ratio = max(0.0, min(1.0, ratio))
    return QColor(
        round(base.red() + (tint.red() - base.red()) * ratio),
        round(base.green() + (tint.green() - base.green()) * ratio),
        round(base.blue() + (tint.blue() - base.blue()) * ratio),
    )


def _status_color(palette: Palette, kind: StatusKind) -> QColor:
    return QColor(
        {
            "ok": palette.ok,
            "warn": palette.warn,
            "err": palette.err,
            "off": palette.text_dim,
        }[kind]
    )


class EnergyBar(QWidget):
    """Horizontal energy meter: rounded track, gradient fill, quarter ticks."""

    #: Quarter marks, so a glance at the bar reads as a fraction of the budget.
    TICKS = (0.25, 0.5, 0.75)

    def __init__(self, parent: QWidget | None = None, *, palette: Palette = DARK) -> None:
        super().__init__(parent)
        self._min = 0
        self._max = 100
        self._value = 0
        self._track_color = QColor(palette.surface_alt)
        self._accent = QColor(palette.accent)
        self._tick_color = QColor(palette.bg)
        self._reduced_motion = False
        self.setFixedHeight(14)

    def setRange(self, lo: int, hi: int) -> None:
        self._min, self._max = lo, hi
        self._value = max(self._min, min(self._value, self._max))
        self.update()

    def setValue(self, value: int) -> None:
        self._value = max(self._min, min(value, self._max))
        self.update()

    def value(self) -> int:
        return self._value

    def setAccent(self, color: str) -> None:
        self._accent = QColor(color)
        self.update()

    def accent(self) -> str:
        return self._accent.name()

    def setTrackColor(self, color: str) -> None:
        self._track_color = QColor(color)
        self.update()

    def setReducedMotion(self, enabled: bool) -> None:
        self._reduced_motion = enabled
        self.update()

    def isReducedMotion(self) -> bool:
        return self._reduced_motion

    def _ratio(self) -> float:
        span = self._max - self._min
        if span <= 0:
            return 0.0
        return max(0.0, min(1.0, (self._value - self._min) / span))

    def paintEvent(self, event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        radius = rect.height() / 2
        track = QPainterPath()
        track.addRoundedRect(rect, radius, radius)
        painter.fillPath(track, self._track_color)

        width = rect.width() * self._ratio()
        if width > 0.5:
            fill_rect = QRectF(rect.left(), rect.top(), width, rect.height())
            fill_path = QPainterPath()
            fill_path.addRoundedRect(fill_rect, radius, radius)
            if not self._reduced_motion:
                glow = QColor(self._accent)
                glow.setAlpha(90)
                painter.setPen(QPen(glow, 4))
                painter.drawPath(fill_path)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.fillPath(fill_path, self._fill_gradient(rect))

        painter.setClipPath(track)
        for fraction in self.TICKS:
            x = rect.left() + rect.width() * fraction
            tick = QColor(self._tick_color)
            tick.setAlpha(150 if x <= rect.left() + width else 90)
            painter.setPen(QPen(tick, 1))
            painter.drawLine(QPointF(x, rect.top() + 3), QPointF(x, rect.bottom() - 3))
        painter.end()

    def _fill_gradient(self, rect: QRectF) -> QLinearGradient:
        """Accent on the left, a lighter accent on the right: the bar gains depth."""
        gradient = QLinearGradient(rect.left(), 0.0, rect.right(), 0.0)
        gradient.setColorAt(0.0, self._accent)
        gradient.setColorAt(1.0, self._accent.lighter(135))
        return gradient


class Meter(QWidget):
    """Openness meter (0.0-1.0): track, fill, level ticks and a state word."""

    def __init__(self, parent: QWidget | None = None, *, palette: Palette = DARK) -> None:
        super().__init__(parent)
        self._palette = palette
        self._value = 0.0
        self._open_level = 1.0
        self._closed_level = 0.0
        self._color = QColor(palette.accent)
        self._state = ""
        self.setFixedHeight(22)

    def setValue(self, value: float) -> None:
        self._value = max(0.0, min(1.0, value))
        self.update()

    def value(self) -> float:
        return self._value

    def setLevels(self, open_level: float, closed_level: float) -> None:
        self._open_level = open_level
        self._closed_level = closed_level
        self.update()

    def levels(self) -> tuple[float, float]:
        return self._open_level, self._closed_level

    def setColor(self, color: str) -> None:
        self._color = QColor(color)
        self.update()

    def setState(self, text: str) -> None:
        self._state = text
        self.update()

    def state(self) -> str:
        return self._state

    def paintEvent(self, event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        fm = self.fontMetrics()
        state_width = fm.horizontalAdvance(self._state) + 8 if self._state else 0
        track_rect = QRectF(0, 6, max(0.0, self.width() - state_width), 10)
        radius = track_rect.height() / 2
        track = QPainterPath()
        track.addRoundedRect(track_rect, radius, radius)
        painter.fillPath(track, QColor(self._palette.surface_alt))

        fill_width = track_rect.width() * self._value
        if fill_width > 0.5:
            fill_rect = QRectF(track_rect.left(), track_rect.top(), fill_width, track_rect.height())
            fill_path = QPainterPath()
            fill_path.addRoundedRect(fill_rect, radius, radius)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.fillPath(fill_path, self._color)

        painter.setPen(QPen(QColor(self._palette.text_dim), 1))
        for level in (self._open_level, self._closed_level):
            x = track_rect.left() + track_rect.width() * max(0.0, min(1.0, level))
            painter.drawLine(QPointF(x, track_rect.top() - 1), QPointF(x, track_rect.bottom() + 1))

        if self._state:
            painter.setPen(QColor(self._palette.text))
            text_rect = QRectF(track_rect.right() + 4, 0, state_width, self.height())
            painter.drawText(
                text_rect, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, self._state
            )
        painter.end()


class StatusDot(QWidget):
    """A small coloured dot with a status label; the text is always drawn."""

    def __init__(self, parent: QWidget | None = None, *, palette: Palette = DARK) -> None:
        super().__init__(parent)
        self._palette = palette
        self._kind: StatusKind = "off"
        self._text = ""
        self.setFixedHeight(18)

    def setStatus(self, kind: StatusKind, text: str) -> None:
        self._kind = kind
        self._text = text
        self.setAccessibleName(text)
        self.updateGeometry()
        self.update()

    def text(self) -> str:
        return self._text

    def sizeHint(self) -> QSize:
        fm = self.fontMetrics()
        width = 8 + 6 + fm.horizontalAdvance(self._text) + 4
        return QSize(width, 18)

    def paintEvent(self, event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(_status_color(self._palette, self._kind))
        cy = self.height() / 2
        painter.drawEllipse(QPointF(4 + 4, cy), 4, 4)
        painter.setPen(QColor(self._palette.text))
        text_rect = QRectF(8 + 6, 0, max(0.0, self.width() - 18), self.height())
        painter.drawText(
            text_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, self._text
        )
        painter.end()


class Chip(QLabel):
    """A rounded label with a coloured dot prefix, e.g. 'Index 3'."""

    def __init__(self, parent: QWidget | None = None, *, palette: Palette = DARK) -> None:
        super().__init__(parent)
        self._palette = palette
        self.setTextFormat(Qt.TextFormat.RichText)
        self.setStyleSheet(
            f"background-color: {palette.surface_alt}; border-radius: 8px; "
            f"padding: 3px 5px; font-size: 11px;"
        )

    def setChip(self, color: str, label: str, value: int) -> None:
        self.setText(f'<span style="color:{color};">●</span>&nbsp;{label} {value}')


class Card(QFrame):
    """A raised panel: surface fill, hairline border, one soft shadow.

    The HUD carries its own palette so it renders identically with or without
    the application stylesheet, hence the inline QSS rather than a global rule.
    """

    RADIUS = 12

    def __init__(self, parent: QWidget | None = None, *, palette: Palette = DARK) -> None:
        super().__init__(parent)
        self.setObjectName("card")
        self.setStyleSheet(
            f"QFrame#card {{ background-color: {palette.surface}; "
            f"border: 1px solid {palette.border}; border-radius: {self.RADIUS}px; }}"
        )
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(18)
        shadow.setXOffset(0)
        shadow.setYOffset(2)
        shadow_color = QColor(0, 0, 0)
        shadow_color.setAlpha(38)
        shadow.setColor(shadow_color)
        self.setGraphicsEffect(shadow)


class Badge(QWidget):
    """A compact status pill: glyph, short text, status colour. Never colour alone."""

    clicked = Signal()

    H_PADDING = 9
    GLYPH_GAP = 5
    HEIGHT = 22

    def __init__(self, parent: QWidget | None = None, *, palette: Palette = DARK) -> None:
        super().__init__(parent)
        self._palette = palette
        self._kind: StatusKind = "off"
        self._glyph = ""
        self._short = ""
        self._text = ""
        self._clickable = False
        self.setFixedHeight(self.HEIGHT)

    def setStatus(self, kind: StatusKind, glyph: str, short: str, text: str = "") -> None:
        self._kind = kind
        self._glyph = glyph
        self._short = short
        self._text = text or short
        self.setToolTip(self._text)
        self.setAccessibleName(self._text)
        self.updateGeometry()
        self.update()

    def text(self) -> str:
        """The full status sentence; the badge itself shows the short form."""
        return self._text

    def shortText(self) -> str:
        return self._short

    def kind(self) -> StatusKind:
        return self._kind

    def setClickable(self, clickable: bool) -> None:
        self._clickable = clickable
        self.setCursor(
            Qt.CursorShape.PointingHandCursor if clickable else Qt.CursorShape.ArrowCursor
        )

    def sizeHint(self) -> QSize:
        fm = self.fontMetrics()
        width = (
            self.H_PADDING * 2
            + fm.horizontalAdvance(self._glyph)
            + (self.GLYPH_GAP if self._glyph else 0)
            + fm.horizontalAdvance(self._short)
        )
        return QSize(width, self.HEIGHT)

    def minimumSizeHint(self) -> QSize:
        return self.sizeHint()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if self._clickable and self.rect().contains(event.position().toPoint()):
            self.clicked.emit()
        super().mouseReleaseEvent(event)

    def paintEvent(self, event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        color = _status_color(self._palette, self._kind)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        radius = rect.height() / 2
        background = blend(QColor(self._palette.surface_alt), color, 0.14)
        painter.setPen(QPen(blend(QColor(self._palette.border), color, 0.45), 1))
        painter.setBrush(background)
        painter.drawRoundedRect(rect, radius, radius)

        fm = self.fontMetrics()
        x = rect.left() + self.H_PADDING
        if self._glyph:
            painter.setPen(color)
            glyph_rect = QRectF(x, rect.top(), fm.horizontalAdvance(self._glyph), rect.height())
            painter.drawText(glyph_rect, Qt.AlignmentFlag.AlignCenter, self._glyph)
            x = glyph_rect.right() + self.GLYPH_GAP
        painter.setPen(QColor(self._palette.text))
        text_rect = QRectF(x, rect.top(), max(0.0, rect.right() - x), rect.height())
        painter.drawText(
            text_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, self._short
        )
        painter.end()


#: Left hand reads pinky to index, right hand index to pinky, so the drawn
#: fingers run in the same order as the keys under them (1-2-3-4, 7-8-9-0).
HAND_ORDER: dict[Hand, tuple[Finger, ...]] = {
    Hand.LEFT: (Finger.PINKY, Finger.RING, Finger.MIDDLE, Finger.INDEX),
    Hand.RIGHT: (Finger.INDEX, Finger.MIDDLE, Finger.RING, Finger.PINKY),
}
#: Finger tip height as a fraction of the hand box: middle reaches highest.
FINGER_REACH: dict[Finger, float] = {
    Finger.PINKY: 0.38,
    Finger.RING: 0.13,
    Finger.MIDDLE: 0.05,
    Finger.INDEX: 0.22,
    Finger.THUMB: 0.48,
}


class HandMap(QWidget):
    """Two hand silhouettes whose finger tips light up as the keys are drummed.

    Replaces a row of text chips: the picture answers "which finger, how often"
    without reading, and the key caps underneath tie each finger to its key.
    """

    FLASH_MS = 250
    DECAY_INTERVAL_MS = 25
    IDLE_TINT = 0.42
    HEIGHT = 96
    HAND_GAP = 26
    #: A hand is drawn at most this wide, then centred in its half, so it stays
    #: hand-shaped instead of stretching into a comb as the window grows.
    MAX_COLUMN = 26.0
    PALM_TOP = 0.60
    KEYCAP_HEIGHT = 20
    KEYCAP_GAP = 6

    def __init__(self, parent: QWidget | None = None, *, palette: Palette = DARK) -> None:
        super().__init__(parent)
        self._palette = palette
        self._reduced_motion = False
        self._counts: dict[tuple[Hand, Finger], int] = {}
        self._flash: dict[tuple[Hand, Finger], float] = {}
        self._tips: dict[tuple[Hand, Finger], QPointF] = {}
        self._hit: dict[tuple[Hand, Finger], QRectF] = {}
        self._caps: dict[tuple[Hand, Finger], QRectF] = {}
        #: Key label per drummed finger, e.g. (LEFT, PINKY) -> "1".
        self.keycaps: dict[tuple[Hand, Finger], str] = {
            (hand, finger): key for key, (hand, finger) in KEY_MAP.items()
        }
        self._decay = QTimer(self)
        self._decay.setInterval(self.DECAY_INTERVAL_MS)
        self._decay.timeout.connect(self._step_decay)
        # Reduced motion blinks the tips off in one step instead of fading them.
        self._blink = QTimer(self)
        self._blink.setSingleShot(True)
        self._blink.setInterval(self.FLASH_MS)
        self._blink.timeout.connect(self._clear_flashes)
        self.setFixedHeight(self.HEIGHT)
        self.setMinimumWidth(240)
        self.setAccessibleName("Taps per finger")

    # -- state ---------------------------------------------------------------

    def setReducedMotion(self, enabled: bool) -> None:
        self._reduced_motion = enabled

    def isReducedMotion(self) -> bool:
        return self._reduced_motion

    def setCounts(self, counts: dict[tuple[Hand, Finger], int]) -> None:
        if counts == self._counts:
            return
        self._counts = dict(counts)
        self.update()

    def counts(self) -> dict[tuple[Hand, Finger], int]:
        return dict(self._counts)

    def flash(self, hand: Hand, finger: Finger) -> None:
        """Light a finger tip; it decays back over FLASH_MS, or blinks off flat."""
        self._flash[(hand, finger)] = 1.0
        if self._reduced_motion:
            self._blink.start()
        elif not self._decay.isActive():
            self._decay.start()
        self.update()

    def flashLevel(self, hand: Hand, finger: Finger) -> float:
        return self._flash.get((hand, finger), 0.0)

    def _clear_flashes(self) -> None:
        if self._flash:
            self._flash.clear()
            self.update()

    def _step_decay(self) -> None:
        step = self.DECAY_INTERVAL_MS / self.FLASH_MS
        for key in list(self._flash):
            level = self._flash[key] - step
            if level <= 0.0:
                del self._flash[key]
            else:
                self._flash[key] = level
        if not self._flash:
            self._decay.stop()
        self.update()

    # -- geometry ------------------------------------------------------------

    def resizeEvent(self, event: QResizeEvent) -> None:
        self._layout_hands()
        super().resizeEvent(event)

    def _metrics(self) -> tuple[float, float, float]:
        """Half-width, drawing height and column width for one hand."""
        hand_width = (self.width() - self.HAND_GAP) / 2
        hand_height = self.height() - self.KEYCAP_HEIGHT - self.KEYCAP_GAP
        return hand_width, hand_height, min(hand_width / 5.0, self.MAX_COLUMN)

    def _hand_left(self, index: int, hand_width: float, column: float) -> float:
        """Left edge of one hand's drawing, centred inside its half."""
        return index * (hand_width + self.HAND_GAP) + (hand_width - column * 5) / 2

    def _layout_hands(self) -> None:
        self._tips.clear()
        self._hit.clear()
        self._caps.clear()
        hand_width, hand_height, column = self._metrics()
        if hand_width <= 0:
            return
        for index, hand in enumerate((Hand.LEFT, Hand.RIGHT)):
            left = self._hand_left(index, hand_width, column)
            # The thumb takes the outer slot, so the fingers shift inward by one.
            offset = 0 if hand is Hand.LEFT else 1
            for slot, finger in enumerate(HAND_ORDER[hand]):
                cx = left + column * (slot + offset + 0.5)
                tip = QPointF(cx, hand_height * FINGER_REACH[finger])
                self._tips[(hand, finger)] = tip
                self._hit[(hand, finger)] = QRectF(
                    cx - column / 2, 0.0, column, float(self.height())
                )
                cap_width = max(8.0, min(24.0, column - 4))
                self._caps[(hand, finger)] = QRectF(
                    cx - cap_width / 2,
                    hand_height + self.KEYCAP_GAP,
                    cap_width,
                    float(self.KEYCAP_HEIGHT),
                )
            thumb_slot = 4 if hand is Hand.LEFT else 0
            thumb_cx = left + column * (thumb_slot + 0.5)
            self._tips[(hand, Finger.THUMB)] = QPointF(
                thumb_cx, hand_height * FINGER_REACH[Finger.THUMB]
            )
            self._hit[(hand, Finger.THUMB)] = QRectF(
                thumb_cx - column / 2, 0.0, column, float(self.height())
            )

    def _finger_at(self, point: QPointF) -> tuple[Hand, Finger] | None:
        for key, rect in self._hit.items():
            if rect.contains(point):
                return key
        return None

    def event(self, event: QEvent) -> bool:
        if event.type() == QEvent.Type.ToolTip and isinstance(event, QHelpEvent):
            key = self._finger_at(QPointF(event.pos()))
            if key is None:
                QToolTip.hideText()
            else:
                hand, finger = key
                cap = self.keycaps.get(key)
                label = f"{hand.value.title()} {finger.value.title()}"
                if cap:
                    label = f"{label} (key {cap})"
                QToolTip.showText(event.globalPos(), f"{label}: {self._counts.get(key, 0)} taps")
            return True
        return super().event(event)

    # -- painting ------------------------------------------------------------

    def paintEvent(self, event: QPaintEvent) -> None:
        if not self._tips:
            self._layout_hands()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        _, hand_height, column = self._metrics()
        stroke = max(4.0, min(12.0, column * 0.46))
        for hand in (Hand.LEFT, Hand.RIGHT):
            self._paint_hand(painter, hand, hand_height, stroke)
        self._paint_keycaps(painter)
        painter.end()

    def _finger_color(self, hand: Hand, finger: Finger) -> QColor:
        base = QColor(self._palette.surface_alt)
        tint = QColor(self._palette.finger[finger])
        level = self._flash.get((hand, finger), 0.0)
        if self._counts.get((hand, finger), 0):
            level = max(level, self.IDLE_TINT)
        return blend(base, tint, level)

    def _paint_hand(self, painter: QPainter, hand: Hand, height: float, stroke: float) -> None:
        palm_top = height * self.PALM_TOP
        fingers = HAND_ORDER[hand]
        xs = [self._tips[(hand, finger)].x() for finger in fingers]
        palm = QRectF(
            min(xs) - stroke / 2 - 1,
            palm_top,
            (max(xs) - min(xs)) + stroke + 2,
            height - palm_top - 3,
        )

        thumb_tip = self._tips[(hand, Finger.THUMB)]
        thumb_start = QPointF(
            palm.right() - stroke * 0.6 if hand is Hand.LEFT else palm.left() + stroke * 0.6,
            palm.top() + palm.height() * 0.55,
        )
        thumb_path = QPainterPath(thumb_start)
        thumb_path.lineTo(thumb_tip)
        painter.setPen(
            QPen(
                self._finger_color(hand, Finger.THUMB),
                stroke * 0.8,
                Qt.PenStyle.SolidLine,
                Qt.PenCapStyle.RoundCap,
            )
        )
        painter.drawPath(thumb_path)

        for finger in fingers:
            tip = self._tips[(hand, finger)]
            painter.setPen(
                QPen(
                    self._finger_color(hand, finger),
                    stroke,
                    Qt.PenStyle.SolidLine,
                    Qt.PenCapStyle.RoundCap,
                )
            )
            painter.drawLine(QPointF(tip.x(), palm_top + 2), tip)

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(self._palette.surface_alt))
        palm_radius = min(palm.height() / 2, stroke * 1.5)
        painter.drawRoundedRect(palm, palm_radius, palm_radius)

        # Tips last, so a lit finger reads over the palm.
        for finger in (*fingers, Finger.THUMB):
            level = self._flash.get((hand, finger), 0.0)
            if level <= 0.0 and not self._counts.get((hand, finger), 0):
                continue
            tip = self._tips[(hand, finger)]
            color = QColor(self._palette.finger[finger])
            if level > 0.0:
                halo = QColor(color)
                halo.setAlpha(round(70 * level))
                painter.setBrush(halo)
                painter.drawEllipse(tip, stroke * 0.95, stroke * 0.95)
            else:
                color = blend(QColor(self._palette.surface_alt), color, self.IDLE_TINT + 0.3)
            painter.setBrush(color)
            painter.drawEllipse(tip, stroke * 0.52, stroke * 0.52)

    def _paint_keycaps(self, painter: QPainter) -> None:
        font = QFont(self.font())
        font.setPixelSize(11)
        painter.setFont(font)
        for key, rect in self._caps.items():
            painter.setPen(QPen(QColor(self._palette.border), 1))
            painter.setBrush(QColor(self._palette.surface_alt))
            painter.drawRoundedRect(rect.adjusted(0.5, 0.5, -0.5, -0.5), 5, 5)
            painter.setPen(QColor(self._palette.text_dim))
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, self.keycaps.get(key, ""))
