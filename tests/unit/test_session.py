from taprivo.core.events import Finger, Hand
from taprivo.core.session import Session


def test_counters_and_taps_per_minute() -> None:
    session = Session(now_ms=lambda: 0, mode="simulator")
    session.record_tap(Hand.LEFT, Finger.INDEX, 0)
    session.record_tap(Hand.RIGHT, Finger.INDEX, 30_000)
    session.record_tap(Hand.RIGHT, Finger.RING, 59_000)
    assert session.taps_total == 3
    assert session.taps_per_finger[Finger.INDEX] == 2
    assert session.taps_per_finger[Finger.THUMB] == 0
    assert session.taps_per_hand == {Hand.LEFT: 1, Hand.RIGHT: 2}
    assert session.taps_per_hand_finger[(Hand.LEFT, Finger.INDEX)] == 1
    assert session.taps_per_hand_finger[(Hand.RIGHT, Finger.INDEX)] == 1
    assert session.taps_per_hand_finger[(Hand.LEFT, Finger.RING)] == 0
    assert session.taps_per_minute(59_000) == 3
    assert session.taps_per_minute(61_000) == 2


def test_spend_summary_truncates_reason() -> None:
    session = Session(now_ms=lambda: 0, mode="simulator")
    session.record_spend(250, "x" * 100)
    assert session.spend_count == 1
    assert session.last_spend is not None
    assert session.last_spend.amount == 250
    assert len(session.last_spend.reason) == 60


def test_duration_and_ids() -> None:
    session = Session(now_ms=lambda: 5_000, mode="simulator")
    assert len(session.session_id) == 32
    assert session.duration_seconds(65_000) == 60


def test_tap_exactly_sixty_seconds_old_is_pruned() -> None:
    session = Session(now_ms=lambda: 0, mode="simulator")
    session.record_tap(Hand.RIGHT, Finger.INDEX, 0)
    session.record_tap(Hand.RIGHT, Finger.INDEX, 1)
    assert session.taps_per_minute(59_999) == 2
    assert session.taps_per_minute(60_000) == 1
    assert session.taps_per_minute(60_001) == 0


def test_duration_never_negative() -> None:
    session = Session(now_ms=lambda: 5_000, mode="simulator")
    assert session.duration_seconds(1_000) == 0
