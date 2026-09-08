"""Fail if the installed mediapipe binaries contain Google usage-telemetry code."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

MARKERS = (b"clearcut", b"playlog", b"play.googleapis.com/log")


def _binaries() -> list[Path]:
    spec = importlib.util.find_spec("mediapipe")
    if spec is None or spec.origin is None:
        return []
    root = Path(spec.origin).parent
    return [p for p in root.rglob("*") if p.suffix in {".so", ".dylib", ".pyd", ".dll"}]


@pytest.mark.skipif(importlib.util.find_spec("mediapipe") is None, reason="mediapipe not installed")
def test_mediapipe_binaries_have_no_telemetry_markers() -> None:
    binaries = _binaries()
    assert binaries, "no mediapipe binaries found to scan"
    for binary in binaries:
        data = binary.read_bytes()
        hits = [m.decode() for m in MARKERS if m in data]
        msg = (
            f"{binary.name} contains telemetry markers {hits}; "
            "keep mediapipe pinned to a telemetry-free release"
        )
        assert not hits, msg
