"""Custom-painted widgets used by the HUD and camera window."""

from __future__ import annotations

from typing import Literal

from PySide6.QtCore import QPointF, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPaintEvent, QPen
from PySide6.QtWidgets import QLabel, QWidget

from taprivo.ui.theme import DARK, Palette

StatusKind = Literal["ok", "warn", "err", "off"]


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
    """Horizontal energy meter: rounded track, rounded fill, soft glow."""

    def __init__(self, parent: QWidget | None = None, *, palette: Palette = DARK) -> None:
        super().__init__(parent)
        self._min = 0
        self._max = 100
        self._value = 0
        self._track_color = QColor(palette.surface_alt)
        self._accent = QColor(palette.accent)
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
            painter.fillPath(fill_path, self._accent)
        painter.end()


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
