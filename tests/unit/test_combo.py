from taprivo.core.combo import ComboTracker


def test_consecutive_taps_within_timeout_increment() -> None:
    combo = ComboTracker(timeout_ms=600, enabled=True)
    assert combo.record(0) == 1
    assert combo.record(500) == 2
    assert combo.record(1000) == 3


def test_gap_beyond_timeout_resets_to_one() -> None:
    combo = ComboTracker(timeout_ms=600, enabled=True)
    combo.record(0)
    combo.record(100)
    assert combo.record(800) == 1


def test_disabled_tracker_stays_at_zero() -> None:
    combo = ComboTracker(timeout_ms=600, enabled=False)
    assert combo.record(0) == 0
    assert combo.count == 0


def test_reset() -> None:
    combo = ComboTracker(timeout_ms=600, enabled=True)
    combo.record(0)
    combo.reset()
    assert combo.count == 0
