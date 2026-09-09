"""Camera capture with device probing and signal guards (OpenCV)."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import cv2
import numpy as np

from taprivo.core.events import now_monotonic_ms
from taprivo.vision.devices import DeviceInfo, enumerate_devices
from taprivo.vision.frames import Frame

log = logging.getLogger(__name__)

DARK_MEAN = 5.0
MAX_PROBE_INDEX = 4
NO_SIGNAL_FRAMES = 10
STALE_MS = 2000
PROBE_FRAMES = 5

CaptureFactory = Callable[[int], Any]
EnumerateFn = Callable[[], list[DeviceInfo]]
CONTINUITY_HINT = "iPhone camera; select to use"


class CameraError(Exception):
    """The camera could not be opened."""


@dataclass(frozen=True, slots=True)
class CameraDevice:
    index: int
    label: str
    width: int
    height: int
    has_signal: bool
    name: str = ""
    kind: str = "unknown"
    probed: bool = True


def default_capture_factory(index: int) -> Any:
    return cv2.VideoCapture(index)


def frame_is_dark(image: np.ndarray) -> bool:
    return float(image.mean()) < DARK_MEAN


def _label(name: str, width: int, height: int, signal: bool, probed: bool) -> str:
    if not probed:
        return f"{name} ({CONTINUITY_HINT})"
    return f"{name} ({width}x{height})" + ("" if signal else " — no signal")


def _probe(capture: Any) -> tuple[int, int, bool]:
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
    return width, height, signal


def list_devices(
    factory: CaptureFactory = default_capture_factory,
    max_index: int = MAX_PROBE_INDEX,
    enumerate_fn: EnumerateFn = enumerate_devices,
) -> list[CameraDevice]:
    """Every capture index that opens, with a real device name where macOS
    offers one.

    A device AVFoundation reports as a Continuity Camera is listed but never
    opened: opening one wakes the iPhone. Such a device comes back with
    `probed=False`, `has_signal=False` and a label that says to select it.
    """
    try:
        infos = enumerate_fn()
    except Exception:  # names are a nicety; a failure must not hide the cameras
        log.exception("camera device enumeration failed")
        infos = []
    by_index = {info.index: info for info in infos}
    found: list[tuple[int, int, int, bool, bool]] = []  # index, w, h, signal, probed
    for index in range(max_index + 1):
        if (info := by_index.get(index)) is not None and info.kind == "continuity":
            found.append((index, 0, 0, False, False))
            continue
        capture = factory(index)
        try:
            if not capture.isOpened():
                continue
            width, height, signal = _probe(capture)
            found.append((index, width, height, signal, True))
        finally:
            capture.release()
    if len(infos) != len(found):
        # OpenCV and AVFoundation disagree about how many cameras exist, so the
        # index -> name mapping cannot be trusted: fall back to index labels.
        by_index = {}
    devices: list[CameraDevice] = []
    for index, width, height, signal, probed in found:
        info = by_index.get(index)
        name = info.name if info is not None else f"Camera {index}"
        kind = info.kind if info is not None else "unknown"
        devices.append(
            CameraDevice(
                index=index,
                label=_label(name, width, height, signal, probed),
                width=width,
                height=height,
                has_signal=signal,
                name=name,
                kind=kind,
                probed=probed,
            )
        )
    return devices


def default_device(
    devices: list[CameraDevice], *, prefer_builtin: bool = True
) -> CameraDevice | None:
    """The device to select when the user has not picked one.

    With `prefer_builtin` (the default) the built-in camera wins even when
    another device also has a signal, so a nearby iPhone cannot take over.
    """
    if not devices:
        return None
    if prefer_builtin:
        for device in devices:
            if device.kind == "builtin" and device.has_signal:
                return device
    for device in devices:
        if device.probed and device.has_signal:
            return device
    if prefer_builtin:
        for device in devices:
            if device.kind == "builtin":
                return device
    return devices[0]


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
