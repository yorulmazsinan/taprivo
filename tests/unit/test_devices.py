"""AVFoundation device enumeration, driven by a fake `AVFoundation` module.

The real framework is macOS-only and reports whatever cameras happen to be
plugged in, so every test here injects a stand-in module into `sys.modules`
and pins `sys.platform` to darwin -- that keeps the suite identical on Linux
CI, where pyobjc is not installed at all.
"""

from __future__ import annotations

import sys
import types
from typing import Any

import pytest

from taprivo.vision.devices import DeviceInfo, classify, enumerate_devices

BUILTIN = "AVCaptureDeviceTypeBuiltInWideAngleCamera"
CONTINUITY = "AVCaptureDeviceTypeContinuityCamera"
EXTERNAL = "AVCaptureDeviceTypeExternal"


class FakeDevice:
    def __init__(self, name: str, device_type: str, model: str = "m", unique: str = "u") -> None:
        self._name = name
        self._type = device_type
        self._model = model
        self._unique = unique

    def localizedName(self) -> str:  # noqa: N802 (AVFoundation API)
        return self._name

    def modelID(self) -> str:  # noqa: N802 (AVFoundation API)
        return self._model

    def uniqueID(self) -> str:  # noqa: N802 (AVFoundation API)
        return self._unique

    def deviceType(self) -> str:  # noqa: N802 (AVFoundation API)
        return self._type


class FakeSession:
    def __init__(self, devices: list[Any]) -> None:
        self._devices = devices
        self.requested_types: list[str] = []

    def discoverySessionWithDeviceTypes_mediaType_position_(  # noqa: N802 (AVFoundation API)
        self, types: list[str], media_type: str, position: int
    ) -> FakeSession:
        self.requested_types = list(types)
        assert media_type == "vide"
        assert position == 0
        return self

    def devices(self) -> list[Any]:
        return self._devices


def install_fake(
    monkeypatch: pytest.MonkeyPatch, devices: list[Any], **extra: Any
) -> types.ModuleType:
    module = types.ModuleType("AVFoundation")
    module.AVCaptureDeviceDiscoverySession = FakeSession(devices)  # type: ignore[attr-defined]
    module.AVMediaTypeVideo = "vide"  # type: ignore[attr-defined]
    module.AVCaptureDevicePositionUnspecified = 0  # type: ignore[attr-defined]
    module.AVCaptureDeviceTypeBuiltInWideAngleCamera = BUILTIN  # type: ignore[attr-defined]
    module.AVCaptureDeviceTypeExternal = EXTERNAL  # type: ignore[attr-defined]
    module.AVCaptureDeviceTypeContinuityCamera = CONTINUITY  # type: ignore[attr-defined]
    for key, value in extra.items():
        setattr(module, key, value)
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setitem(sys.modules, "AVFoundation", module)
    return module


def test_kinds_and_order_follow_the_discovery_session(monkeypatch: pytest.MonkeyPatch) -> None:
    """Index order is the discovery-session order, which is also OpenCV's."""
    install_fake(
        monkeypatch,
        [
            FakeDevice("Sinan's iPhone Camera", CONTINUITY, "iPhone14,2", "u-phone"),
            FakeDevice("FaceTime HD Kamera", BUILTIN, "UVC Camera", "u-facetime"),
            FakeDevice("Logi Webcam", EXTERNAL, "UVC", "u-logi"),
        ],
    )
    infos = enumerate_devices()
    assert infos == [
        DeviceInfo(0, "Sinan's iPhone Camera", "iPhone14,2", "u-phone", "continuity"),
        DeviceInfo(1, "FaceTime HD Kamera", "UVC Camera", "u-facetime", "builtin"),
        DeviceInfo(2, "Logi Webcam", "UVC", "u-logi", "external"),
    ]


def test_device_types_skip_constants_this_macos_lacks(monkeypatch: pytest.MonkeyPatch) -> None:
    """AVCaptureDeviceTypeContinuityCamera only exists from macOS 13 on; asking
    the discovery session for a type the framework does not define raises, so
    missing constants must simply be left out of the request."""
    module = install_fake(monkeypatch, [FakeDevice("FaceTime HD Camera", BUILTIN)])
    monkeypatch.delattr(module, "AVCaptureDeviceTypeContinuityCamera")
    assert [info.kind for info in enumerate_devices()] == ["builtin"]
    session = module.AVCaptureDeviceDiscoverySession
    assert session.requested_types == [BUILTIN, EXTERNAL]


def test_duplicate_device_type_constants_are_requested_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """pyobjc maps the deprecated ...ExternalUnknown onto the same string as
    ...External; the discovery session must not get it twice."""
    module = install_fake(
        monkeypatch,
        [FakeDevice("FaceTime HD Camera", BUILTIN)],
        AVCaptureDeviceTypeExternalUnknown=EXTERNAL,
    )
    enumerate_devices()
    session = module.AVCaptureDeviceDiscoverySession
    assert session.requested_types == [BUILTIN, EXTERNAL, CONTINUITY]


def test_name_fallback_when_the_device_type_is_unfamiliar(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_fake(
        monkeypatch,
        [
            FakeDevice("Sinan's iPad", "AVCaptureDeviceTypeSomethingNew"),
            FakeDevice("FaceTime HD Camera", "AVCaptureDeviceTypeSomethingNew"),
            FakeDevice("Capture Card", "AVCaptureDeviceTypeSomethingNew"),
        ],
    )
    assert [info.kind for info in enumerate_devices()] == ["continuity", "builtin", "unknown"]


def test_classify_prefers_the_device_type_over_the_name() -> None:
    # A Continuity Camera is often named after its owner's iPhone, but an
    # external capture card called "iPhone dock" must not be mistaken for one.
    assert classify(BUILTIN, "Sinan's iPhone Camera") == "builtin"
    assert classify("AVCaptureDeviceTypeExternalUnknown", "iPhone dock") == "external"
    assert classify("", "") == "unknown"


def test_unnamed_device_gets_an_index_name(monkeypatch: pytest.MonkeyPatch) -> None:
    install_fake(monkeypatch, [FakeDevice("", BUILTIN)])
    assert enumerate_devices()[0].name == "Camera 0"


def test_import_failure_returns_no_devices(monkeypatch: pytest.MonkeyPatch) -> None:
    """pyobjc is a macOS-only extra and can be missing (a trimmed app bundle,
    a source checkout on another platform): that must degrade, not raise."""
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setitem(sys.modules, "AVFoundation", None)
    assert enumerate_devices() == []


def test_discovery_failure_returns_no_devices(monkeypatch: pytest.MonkeyPatch) -> None:
    class Exploding:
        def discoverySessionWithDeviceTypes_mediaType_position_(  # noqa: N802 (AVFoundation API)
            self, *args: Any
        ) -> None:
            raise RuntimeError("no such device type")

    module = install_fake(monkeypatch, [])
    module.AVCaptureDeviceDiscoverySession = Exploding()  # type: ignore[attr-defined]
    assert enumerate_devices() == []


def test_unreadable_device_returns_no_devices(monkeypatch: pytest.MonkeyPatch) -> None:
    """A half-usable list would map the wrong names onto OpenCV indexes, so a
    device that cannot be read discards the whole enumeration."""

    class Broken(FakeDevice):
        def localizedName(self) -> str:  # noqa: N802 (AVFoundation API)
            raise RuntimeError("device went away")

    install_fake(monkeypatch, [FakeDevice("FaceTime HD Camera", BUILTIN), Broken("x", BUILTIN)])
    assert enumerate_devices() == []


def test_non_macos_returns_no_devices(monkeypatch: pytest.MonkeyPatch) -> None:
    install_fake(monkeypatch, [FakeDevice("FaceTime HD Camera", BUILTIN)])
    monkeypatch.setattr(sys, "platform", "linux")
    assert enumerate_devices() == []
