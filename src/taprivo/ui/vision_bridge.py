"""Bridges vision worker callbacks (worker thread) into Qt signals (main thread)."""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QImage

from taprivo.vision.detector import DetectorState
from taprivo.vision.frames import Frame, HandFrame
from taprivo.vision.worker import PreviewCallback


@dataclass(frozen=True)
class PreviewPacket:
    image: QImage
    hand: HandFrame | None
    state: DetectorState


class VisionSignals(QObject):
    preview = Signal(object)


def _to_qimage(frame: Frame) -> QImage:
    rgb = frame.image[:, :, ::-1].copy()  # BGR → RGB, contiguous copy owned by Python
    height, width, _ = rgb.shape
    image = QImage(rgb.data, width, height, 3 * width, QImage.Format.Format_RGB888)
    return image.copy()  # detach from the numpy buffer before crossing threads


def make_preview_callback(signals: VisionSignals) -> PreviewCallback:
    def callback(frame: Frame, hand: HandFrame | None, state: DetectorState) -> None:
        signals.preview.emit(PreviewPacket(_to_qimage(frame), hand, state))

    return callback
