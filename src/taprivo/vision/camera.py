"""Camera capture with device probing and signal guards (OpenCV)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import cv2
import numpy as np

from taprivo.core.events import now_monotonic_ms
from taprivo.vision.frames import Frame

DARK_MEAN = 5.0
MAX_PROBE_INDEX = 4
NO_SIGNAL_FRAMES = 10
STALE_MS = 2000
PROBE_FRAMES = 5

CaptureFactory = Callable[[int], Any]


class CameraError(Exception):
    """The camera could not be opened."""


@dataclass(frozen=True, slots=True)
class CameraDevice:
    index: int
    label: str
    width: int
    height: int
    has_signal: bool


def default_capture_factory(index: int) -> Any:
    return cv2.VideoCapture(index)


def frame_is_dark(image: np.ndarray) -> bool:
    return float(image.mean()) < DARK_MEAN


def list_devices(
    factory: CaptureFactory = default_capture_factory, max_index: int = MAX_PROBE_INDEX
) -> list[CameraDevice]:
    devices: list[CameraDevice] = []
    for index in range(max_index + 1):
        capture = factory(index)
        try:
            if not capture.isOpened():
                continue
            width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
            height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
            signal = False
            for _ in range(PROBE_FRAMES):
                ok, image = capture.read()
                if ok and image is not None:
                    if width == 0 or height == 0:
                        height, width = image.shape[:2]
                    if not frame_is_dark(image):
                        signal = True
                        break
            label = f"Camera {index} ({width}x{height})" + ("" if signal else " — no signal")
            devices.append(CameraDevice(index, label, width, height, signal))
        finally:
            capture.release()
    return devices


def default_device(devices: list[CameraDevice]) -> CameraDevice | None:
    for device in devices:
        if device.has_signal:
            return device
    return devices[0] if devices else None


class CameraSource:
    def __init__(
        self,
        index: int,
        width: int = 640,
        height: int = 480,
        factory: CaptureFactory = default_capture_factory,
        now_ms: Callable[[], int] = now_monotonic_ms,
    ) -> None:
        self._index = index
        self._width = width
        self._height = height
        self._factory = factory
        self._now_ms = now_ms
        self._capture: Any | None = None
        self._dark_streak = 0
        self.no_signal = False
        self.last_frame_ts: int | None = None

    @property
    def is_open(self) -> bool:
        return self._capture is not None

    def open(self) -> None:
        if self._capture is not None:
            self.close()
        capture = self._factory(self._index)
        if not capture.isOpened():
            capture.release()
            raise CameraError(
                f"camera {self._index} could not be opened (permission denied or device missing)"
            )
        capture.set(cv2.CAP_PROP_FRAME_WIDTH, self._width)
        capture.set(cv2.CAP_PROP_FRAME_HEIGHT, self._height)
        self._capture = capture
        self._dark_streak = 0
        self.no_signal = False

    def read(self) -> Frame | None:
        if self._capture is None:
            return None
        ok, image = self._capture.read()
        if not ok or image is None:
            return None
        if frame_is_dark(image):
            self._dark_streak += 1
            if self._dark_streak >= NO_SIGNAL_FRAMES:
                self.no_signal = True
        else:
            self._dark_streak = 0
            self.no_signal = False
        ts = self._now_ms()
        self.last_frame_ts = ts
        return Frame(ts_ms=ts, image=image)

    def close(self) -> None:
        if self._capture is not None:
            self._capture.release()
            self._capture = None

    def __enter__(self) -> CameraSource:
        self.open()
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
