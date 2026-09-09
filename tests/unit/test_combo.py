from taprivo.config import ComboTier
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


def test_gap_exactly_at_timeout_still_counts() -> None:
    combo = ComboTracker(timeout_ms=600, enabled=True)
    assert combo.record(0) == 1
    assert combo.record(600) == 2
    assert combo.record(1201) == 1


def test_multiplier_without_tiers_is_one() -> None:
    combo = ComboTracker(timeout_ms=600, enabled=True)
    combo.record(0)
    assert combo.multiplier == 1.0


def test_multiplier_follows_the_highest_reached_tier() -> None:
    tiers = (ComboTier(at=10, multiplier=1.5), ComboTier(at=25, multiplier=2.0))
    combo = ComboTracker(timeout_ms=600, enabled=True, tiers=tiers, multiplier_enabled=True)
    for count in range(1, 30):
        combo.record(count * 100)
        if count < 10:
            assert combo.multiplier == 1.0, count
        elif count < 25:
            assert combo.multiplier == 1.5, count
        else:
            assert combo.multiplier == 2.0, count


def test_multiplier_resets_with_the_combo() -> None:
    tiers = (ComboTier(at=10, multiplier=1.5),)
    combo = ComboTracker(timeout_ms=600, enabled=True, tiers=tiers, multiplier_enabled=True)
    for count in range(12):
        combo.record(count * 100)
    assert combo.multiplier == 1.5
    combo.record(10_000)
    assert combo.count == 1
    assert combo.multiplier == 1.0
    combo.reset()
    assert combo.multiplier == 1.0


def test_disabled_multiplier_stays_at_one() -> None:
    tiers = (ComboTier(at=2, multiplier=1.5),)
    combo = ComboTracker(timeout_ms=600, enabled=True, tiers=tiers, multiplier_enabled=False)
    combo.record(0)
    combo.record(100)
    assert combo.count == 2
    assert combo.multiplier == 1.0
