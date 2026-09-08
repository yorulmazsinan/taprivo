from __future__ import annotations

import math

import pytest

from taprivo.core.events import Finger
from taprivo.vision.features import (
    MCP,
    MIDDLE_MCP,
    TIP,
    WRIST,
    DegenerateHandError,
    compute_features,
)


def open_hand() -> list[tuple[float, float, float]]:
    """Synthetic 21-point hand: wrist at origin, middle MCP 0.1 up, fingers extended upward."""
    pts = [(0.0, 0.0, 0.0)] * 21
    pts[WRIST] = (0.5, 0.8, 0.0)
    pts[MIDDLE_MCP] = (0.5, 0.7, 0.0)
    xs = {
        Finger.THUMB: 0.40,
        Finger.INDEX: 0.45,
        Finger.MIDDLE: 0.50,
        Finger.RING: 0.55,
        Finger.PINKY: 0.60,
    }
    for finger, x in xs.items():
        pts[MCP[finger]] = (x, 0.70, 0.0)
        pts[TIP[finger]] = (x, 0.60, 0.0)  # tip 0.1 above its MCP → c2 = 1.0
    return pts


def test_c2_is_tip_mcp_distance_over_hand_scale() -> None:
    feats = compute_features(open_hand())
    assert math.isclose(feats.scale, 0.1)
    for finger in Finger:
        assert math.isclose(feats.c2[finger], 1.0)


def test_translation_invariance() -> None:
    base = compute_features(open_hand())
    moved = compute_features([(x + 0.2, y - 0.3, z) for x, y, z in open_hand()])
    for finger in Finger:
        assert math.isclose(base.c2[finger], moved.c2[finger])


def test_uniform_scale_invariance() -> None:
    base = compute_features(open_hand())
    scaled = compute_features([(x * 2.5, y * 2.5, z) for x, y, z in open_hand()])
    for finger in Finger:
        assert math.isclose(base.c2[finger], scaled.c2[finger])


def test_depth_is_ignored() -> None:
    pts = open_hand()
    pts[TIP[Finger.INDEX]] = (pts[TIP[Finger.INDEX]][0], pts[TIP[Finger.INDEX]][1], 0.9)
    assert math.isclose(compute_features(pts).c2[Finger.INDEX], 1.0)


def test_flexed_finger_has_smaller_c2() -> None:
    pts = open_hand()
    x, _, z = pts[TIP[Finger.RING]]
    pts[TIP[Finger.RING]] = (x, 0.68, z)  # tip nearly on its MCP
    feats = compute_features(pts)
    assert feats.c2[Finger.RING] < 0.3
    assert math.isclose(feats.c2[Finger.INDEX], 1.0)


def test_degenerate_scale_rejected() -> None:
    pts = open_hand()
    pts[MIDDLE_MCP] = pts[WRIST]
    with pytest.raises(DegenerateHandError):
        compute_features(pts)


def test_requires_21_landmarks() -> None:
    with pytest.raises(ValueError):
        compute_features(open_hand()[:20])
