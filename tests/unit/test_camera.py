from __future__ import annotations

from collections.abc import Callable

import numpy as np
import pytest

from taprivo.vision.camera import (
    DARK_MEAN,
    NO_SIGNAL_FRAMES,
    CameraDevice,
    CameraError,
    CameraSource,
    default_device,
    frame_is_dark,
    list_devices,
)
from taprivo.vision.devices import DeviceInfo


def info(index: int, name: str, kind: str) -> DeviceInfo:
    return DeviceInfo(index, name, "model", f"unique-{index}", kind)  # type: ignore[arg-type]


def tracked_factory(
    captures: dict[int, FakeCapture],
) -> tuple[Callable[[int], FakeCapture], list[int]]:
    """A capture factory that records every index it is asked to open."""
    opened: list[int] = []

    def factory(index: int) -> FakeCapture:
        opened.append(index)
        return captures[index]

    return factory, opened


class FakeCapture:
    def __init__(
        self,
        frames: list[np.ndarray | None],
        opened: bool = True,
        size: tuple[int, int] = (640, 480),
    ):
        self._frames = list(frames)
        self._opened = opened
        self._size = size
        self.props: dict[int, float] = {}
        self.released = False
        self.opened_checked = False

    def isOpened(self) -> bool:  # noqa: N802 (OpenCV API)
        self.opened_checked = True
        return self._opened

    def set(self, prop: int, value: float) -> bool:
        self.props[prop] = value
        return True

    def get(self, prop: int) -> float:
        import cv2

        sizes = {
            cv2.CAP_PROP_FRAME_WIDTH: self._size[0],
            cv2.CAP_PROP_FRAME_HEIGHT: self._size[1],
        }
        return sizes.get(prop, 0.0)

    def read(self):  # type: ignore[no-untyped-def]
        if not self._frames:
            return False, None
        frame = self._frames.pop(0)
        return (frame is not None), frame

    def release(self) -> None:
        self.released = True


def bright(n: int = 1) -> list[np.ndarray]:
    return [np.full((480, 640, 3), 128, dtype=np.uint8) for _ in range(n)]


def dark(n: int = 1) -> list[np.ndarray]:
    return [np.zeros((480, 640, 3), dtype=np.uint8) for _ in range(n)]


def test_frame_is_dark_threshold() -> None:
    assert frame_is_dark(np.zeros((4, 4, 3), dtype=np.uint8))
    assert frame_is_dark(np.full((4, 4, 3), DARK_MEAN - 1, dtype=np.uint8))
    assert not frame_is_dark(np.full((4, 4, 3), 20, dtype=np.uint8))


def test_list_devices_probes_signal_and_labels() -> None:
    captures = {
        0: FakeCapture(dark(5), size=(1920, 1080)),
        1: FakeCapture(bright(5)),
        2: FakeCapture([], opened=False),
    }
    devices = list_devices(lambda i: captures[i], max_index=2, enumerate_fn=lambda: [])
    assert [d.index for d in devices] == [0, 1]
    assert devices[0].has_signal is False and "no signal" in devices[0].label
    assert devices[1].has_signal is True and devices[1].label == "Camera 1 (640x480)"
    assert [d.kind for d in devices] == ["unknown", "unknown"]
    assert all(d.probed for d in devices)
    assert all(c.released for c in captures.values())
    assert default_device(devices) == devices[1]
    assert default_device([]) is None


def test_list_devices_falls_back_to_frame_shape() -> None:
    devices = list_devices(
        lambda i: FakeCapture(bright(5), size=(0, 0)), max_index=0, enumerate_fn=lambda: []
    )
    assert devices == [
        CameraDevice(0, "Camera 0 (640x480)", 640, 480, True, "Camera 0", "unknown", True)
    ]


def test_list_devices_merges_avfoundation_names_by_index() -> None:
    captures = {0: FakeCapture(bright(5)), 1: FakeCapture(bright(5), size=(1280, 720))}
    factory, opened = tracked_factory(captures)
    devices = list_devices(
        factory,
        max_index=1,
        enumerate_fn=lambda: [
            info(0, "FaceTime HD Kamera", "builtin"),
            info(1, "Logi Webcam", "external"),
        ],
    )
    assert opened == [0, 1]
    assert [(d.name, d.kind, d.label) for d in devices] == [
        ("FaceTime HD Kamera", "builtin", "FaceTime HD Kamera (640x480)"),
        ("Logi Webcam", "external", "Logi Webcam (1280x720)"),
    ]


def test_list_devices_never_opens_a_continuity_camera() -> None:
    """Opening a Continuity Camera wakes the iPhone -- the whole point of the
    AVFoundation lookup is to list one without touching it."""
    captures = {0: FakeCapture(bright(5)), 1: FakeCapture(bright(5))}
    factory, opened = tracked_factory(captures)
    devices = list_devices(
        factory,
        max_index=1,
        enumerate_fn=lambda: [
            info(0, "Sinan's iPhone", "continuity"),
            info(1, "FaceTime HD Kamera", "builtin"),
        ],
    )
    assert opened == [1]
    assert captures[0].released is False and not captures[0].opened_checked
    phone, facetime = devices
    assert phone.probed is False and phone.has_signal is False
    assert phone.label == "Sinan's iPhone (iPhone camera; select to use)"
    assert (phone.width, phone.height) == (0, 0)
    assert facetime.probed is True and facetime.has_signal is True
    assert default_device(devices) == facetime


def test_list_devices_ignores_names_when_the_counts_disagree() -> None:
    """OpenCV opened a camera AVFoundation never mentioned: the index -> name
    mapping is then guesswork, so every name is dropped."""
    captures = {0: FakeCapture(bright(5)), 1: FakeCapture(bright(5))}
    devices = list_devices(
        lambda i: captures[i],
        max_index=1,
        enumerate_fn=lambda: [info(0, "FaceTime HD Kamera", "builtin")],
    )
    assert [(d.name, d.kind) for d in devices] == [
        ("Camera 0", "unknown"),
        ("Camera 1", "unknown"),
    ]


def test_list_devices_survives_a_failing_enumeration() -> None:
    def explode() -> list[DeviceInfo]:
        raise RuntimeError("AVFoundation went away")

    devices = list_devices(lambda i: FakeCapture(bright(5)), max_index=0, enumerate_fn=explode)
    assert [d.label for d in devices] == ["Camera 0 (640x480)"]


def test_default_device_prefers_the_builtin_over_another_live_camera() -> None:
    phone = CameraDevice(0, "iPhone", 0, 0, False, "iPhone", "continuity", False)
    webcam = CameraDevice(1, "Webcam", 640, 480, True, "Webcam", "external", True)
    builtin = CameraDevice(2, "FaceTime", 1280, 720, True, "FaceTime", "builtin", True)
    devices = [phone, webcam, builtin]
    assert default_device(devices) is builtin
    assert default_device(devices, prefer_builtin=False) is webcam


def test_default_device_falls_back_through_the_policy() -> None:
    phone = CameraDevice(0, "iPhone", 0, 0, False, "iPhone", "continuity", False)
    dark_builtin = CameraDevice(1, "FaceTime", 1280, 720, False, "FaceTime", "builtin", True)
    # Nothing has a signal (a closed MacBook lid): the built-in still wins over
    # the unprobed iPhone, and it must never be the unprobed device by default.
    assert default_device([phone, dark_builtin]) is dark_builtin
    assert default_device([phone, dark_builtin], prefer_builtin=False) is phone
    assert default_device([phone]) is phone


def test_open_failure_raises_camera_error() -> None:
    capture = FakeCapture([], opened=False)
    source = CameraSource(3, factory=lambda i: capture)
    with pytest.raises(CameraError, match="permission"):
        source.open()
    assert capture.released is True


def test_read_returns_frames_with_monotonic_timestamps() -> None:
    clock = [1000]
    source = CameraSource(1, factory=lambda i: FakeCapture(bright(3)), now_ms=lambda: clock[0])
    source.open()
    assert source.is_open
    f1 = source.read()
    clock[0] = 1040
    f2 = source.read()
    assert f1 is not None and f2 is not None
    assert (f1.ts_ms, f2.ts_ms) == (1000, 1040)
    assert f1.image.shape == (480, 640, 3)
    assert source.last_frame_ts == 1040


def test_no_signal_after_consecutive_dark_frames() -> None:
    source = CameraSource(0, factory=lambda i: FakeCapture(dark(NO_SIGNAL_FRAMES) + bright(1)))
    source.open()
    for _ in range(NO_SIGNAL_FRAMES - 1):
        source.read()
    assert source.no_signal is False
    source.read()
    assert source.no_signal is True
    source.read()  # a bright frame clears the flag
    assert source.no_signal is False


def test_read_none_when_capture_fails_and_close_releases() -> None:
    capture = FakeCapture([None])
    source = CameraSource(1, factory=lambda i: capture)
    with source as opened:
        assert opened.read() is None
    assert capture.released
    assert source.is_open is False
    assert source.read() is None  # closed source reads nothing


def test_requested_size_is_applied() -> None:
    import cv2

    capture = FakeCapture(bright(1))
    CameraSource(1, width=320, height=240, factory=lambda i: capture).open()
    assert capture.props[cv2.CAP_PROP_FRAME_WIDTH] == 320
    assert capture.props[cv2.CAP_PROP_FRAME_HEIGHT] == 240


def test_reopen_releases_previous_capture() -> None:
    captures = [FakeCapture(bright(1)), FakeCapture(bright(1))]
    factory_calls = iter(captures)
    source = CameraSource(0, factory=lambda i: next(factory_calls))
    source.open()
    source.open()
    assert captures[0].released is True
    assert captures[1].released is False
    assert source.is_open is True
    source.close()
    assert captures[1].released is True
