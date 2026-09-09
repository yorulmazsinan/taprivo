"""Bridges vision worker callbacks (worker thread) into Qt signals (main thread)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QImage

from taprivo.core.events import Hand, TapEvent
from taprivo.vision.frames import Frame, HandFrame
from taprivo.vision.squeeze import SqueezeState
from taprivo.vision.worker import PreviewCallback, TapsCallback


@dataclass(frozen=True)
class PreviewPacket:
    image: QImage
    hands: tuple[HandFrame, ...]
    state: SqueezeState


class VisionSignals(QObject):
    preview = Signal(object)
    devices = Signal(object)
    # One emission per hand that just completed a squeeze cycle. The preview
    # is throttled to camera.preview_fps, so the pulse rides its own signal
    # rather than the preview packets, which can skip the frame that counted.
    squeeze = Signal(object)


def _to_qimage(frame: Frame) -> QImage:
    rgb = frame.image[:, :, ::-1].copy()  # BGR → RGB, contiguous copy owned by Python
    height, width, _ = rgb.shape
    image = QImage(rgb.data, width, height, 3 * width, QImage.Format.Format_RGB888)
    return image.copy()  # detach from the numpy buffer before crossing threads


def make_preview_callback(signals: VisionSignals) -> PreviewCallback:
    def callback(frame: Frame, hands: tuple[HandFrame, ...], state: SqueezeState) -> None:
        signals.preview.emit(PreviewPacket(_to_qimage(frame), hands, state))

    return callback


def make_taps_callback(signals: VisionSignals) -> TapsCallback:
    """Collapse a cycle's five per-finger taps into one emission per hand."""

    def callback(events: Sequence[TapEvent]) -> None:
        seen: set[Hand] = set()
        for event in events:
            if event.hand not in seen:
                seen.add(event.hand)
                signals.squeeze.emit(event.hand)

    return callback
