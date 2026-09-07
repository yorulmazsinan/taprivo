import dataclasses

import pytest

from taprivo.core.events import Finger, Hand, TapEvent, TapSource, now_monotonic_ms


def make_event(**overrides: object) -> TapEvent:
    base = dict(
        event_id="e1",
        session_id="s1",
        hand=Hand.RIGHT,
        hand_id="sim-right",
        finger=Finger.INDEX,
        timestamp_monotonic_ms=1000,
        displacement=0.04,
        velocity=0.6,
        confidence=1.0,
        source=TapSource.SIMULATOR,
    )
    base.update(overrides)
    return TapEvent(**base)  # type: ignore[arg-type]


def test_finger_values_are_lowercase_names() -> None:
    assert [f.value for f in Finger] == ["thumb", "index", "middle", "ring", "pinky"]


def test_event_is_frozen() -> None:
    event = make_event()
    with pytest.raises(dataclasses.FrozenInstanceError):
        event.finger = Finger.THUMB  # type: ignore[misc]


def test_now_monotonic_ms_is_monotonic() -> None:
    a = now_monotonic_ms()
    b = now_monotonic_ms()
    assert b >= a
