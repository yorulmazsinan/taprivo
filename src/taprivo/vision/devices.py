"""Camera device names and kinds, read from AVFoundation on macOS.

OpenCV's macOS backend builds its capture-index list from the same
`AVCaptureDeviceDiscoverySession` this module queries, so the position of a
device in `enumerate_devices()` is the OpenCV index for it. That mapping is
what lets Taprivo tell a built-in FaceTime camera from an iPhone Continuity
Camera *before* opening anything -- opening a Continuity Camera wakes the
phone, which is exactly what we want to avoid.

Everything PyObjC is imported lazily inside `enumerate_devices()`: Taprivo
runs (and its tests run) on machines without pyobjc installed, where this
module simply reports no devices and the caller falls back to index labels.
"""

from __future__ import annotations

import logging
import sys
from dataclasses import dataclass
from typing import Any, Literal

log = logging.getLogger(__name__)

DeviceKind = Literal["builtin", "continuity", "external", "unknown"]

# AVCaptureDeviceType values are plain strings whose names end in the type.
# Matching on the suffix keeps this working across the AVFoundation renames
# (AVCaptureDeviceTypeExternalUnknown became ...External in macOS 14).
_TYPE_SUFFIXES: tuple[tuple[str, DeviceKind], ...] = (
    ("BuiltInWideAngleCamera", "builtin"),
    ("ContinuityCamera", "continuity"),
    ("ExternalUnknown", "external"),
    ("External", "external"),
)
_NAME_HINTS: tuple[tuple[str, DeviceKind], ...] = (
    ("iphone", "continuity"),
    ("ipad", "continuity"),
    ("facetime", "builtin"),
)
_DEVICE_TYPE_NAMES = (
    "AVCaptureDeviceTypeBuiltInWideAngleCamera",
    "AVCaptureDeviceTypeExternal",
    "AVCaptureDeviceTypeExternalUnknown",
    "AVCaptureDeviceTypeContinuityCamera",
)


@dataclass(frozen=True, slots=True)
class DeviceInfo:
    index: int
    name: str
    model_id: str
    unique_id: str
    kind: DeviceKind


def classify(device_type: str, name: str) -> DeviceKind:
    """Map an AVCaptureDeviceType (and, failing that, the device name) to a kind."""
    for suffix, kind in _TYPE_SUFFIXES:
        if device_type.endswith(suffix):
            return kind
    lowered = name.lower()
    for hint, kind in _NAME_HINTS:
        if hint in lowered:
            return kind
    return "unknown"


def _text(value: Any) -> str:
    return "" if value is None else str(value)


def _capture_device_types(avfoundation: Any) -> list[Any]:
    """The discovery-session device types, skipping any this macOS lacks.

    `AVCaptureDeviceTypeContinuityCamera` only exists from macOS 13 on, and
    passing an unknown type to the discovery session raises.
    """
    types: list[Any] = []
    for name in _DEVICE_TYPE_NAMES:
        value = getattr(avfoundation, name, None)
        if value is not None and value not in types:
            types.append(value)
    return types


def _is_macos() -> bool:
    """Kept as a function so mypy type-checks the AVFoundation branch on every
    platform instead of narrowing `sys.platform` away."""
    return sys.platform == "darwin"


def enumerate_devices() -> list[DeviceInfo]:
    """Video capture devices in OpenCV index order, or `[]` off macOS.

    Never raises: any AVFoundation problem degrades to an empty list, which
    callers read as "no names available".
    """
    if not _is_macos():
        return []
    try:
        import AVFoundation
    except Exception:
        log.debug("AVFoundation is unavailable; camera devices fall back to index labels")
        return []
    try:
        session = AVFoundation.AVCaptureDeviceDiscoverySession
        discovered = session.discoverySessionWithDeviceTypes_mediaType_position_(
            _capture_device_types(AVFoundation),
            getattr(AVFoundation, "AVMediaTypeVideo", "vide"),
            getattr(AVFoundation, "AVCaptureDevicePositionUnspecified", 0),
        ).devices()
    except Exception:
        log.exception("AVFoundation device discovery failed")
        return []
    infos: list[DeviceInfo] = []
    for index, device in enumerate(discovered):
        try:
            name = _text(device.localizedName())
            info = DeviceInfo(
                index=index,
                name=name or f"Camera {index}",
                model_id=_text(device.modelID()),
                unique_id=_text(device.uniqueID()),
                kind=classify(_text(device.deviceType()), name),
            )
        except Exception:
            log.exception("could not read AVFoundation device %d", index)
            return []
        infos.append(info)
    return infos
