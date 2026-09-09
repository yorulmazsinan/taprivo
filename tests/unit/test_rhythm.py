from __future__ import annotations

import pytest

from taprivo.core.rhythm import RhythmTracker


def drum(tracker: RhythmTracker, taps: int, interval_ms: int, start: int = 0) -> int:
    """Feed `taps` evenly spaced taps and return the timestamp of the last one."""
    ts = start
    for index in range(taps):
        ts = start + index * interval_ms
        tracker.record(ts)
    return ts


def test_no_bpm_before_the_second_tap() -> None:
    tracker = RhythmTracker()
    assert tracker.bpm == 0.0
    assert tracker.steady is False
    tracker.record(0)
    assert tracker.bpm == 0.0
    assert tracker.multiplier == 1.0


def test_two_taps_give_a_bpm_but_not_a_steady_beat() -> None:
    tracker = RhythmTracker()
    tracker.record(0)
    tracker.record(500)
    assert tracker.bpm == pytest.approx(120.0)
    assert tracker.steady is False


def test_steady_train_becomes_steady_on_the_fifth_tap() -> None:
    tracker = RhythmTracker()
    for index in range(4):  # three intervals: one short of steady
        tracker.record(index * 500)
    assert tracker.steady is False
    tracker.record(4 * 500)
    assert tracker.steady is True
    assert tracker.bpm == pytest.approx(120.0)
    assert tracker.multiplier == 1.25


def test_jittery_taps_are_never_steady() -> None:
    tracker = RhythmTracker()
    ts = 0
    for index in range(20):
        # ±25 % around 500 ms: well outside the default 15 % tolerance.
        ts += 625 if index % 2 else 375
        tracker.record(ts)
        assert tracker.steady is False, index
    assert tracker.multiplier == 1.0


def test_a_long_gap_clears_the_window() -> None:
    tracker = RhythmTracker()
    last = drum(tracker, 8, 500)
    assert tracker.steady is True
    tracker.record(last + 5_000)
    assert tracker.bpm == 0.0
    assert tracker.steady is False
    assert tracker.multiplier == 1.0


def test_a_short_pause_within_the_limit_keeps_the_window() -> None:
    tracker = RhythmTracker()
    last = drum(tracker, 8, 500)
    tracker.record(last + 1_400)  # under the 1500 ms floor, so it counts as an interval
    assert tracker.bpm > 0
    # The odd interval widens the spread, so the beat is no longer steady.
    assert tracker.steady is False


def test_slow_beats_use_twice_the_mean_as_the_gap_limit() -> None:
    tracker = RhythmTracker()
    last = drum(tracker, 8, 1_000)  # 60 BPM, mean 1000 ms → limit 2000 ms
    tracker.record(last + 1_900)
    assert tracker.bpm > 0
    tracker.record(last + 1_900 + 4_000)
    assert tracker.bpm == 0.0


def test_tempo_above_the_maximum_is_not_steady() -> None:
    tracker = RhythmTracker(bpm_max=240)
    drum(tracker, 8, 200)  # 300 BPM
    assert tracker.bpm == pytest.approx(300.0)
    assert tracker.steady is False


def test_tempo_below_the_minimum_is_not_steady() -> None:
    tracker = RhythmTracker(bpm_min=60)
    drum(tracker, 8, 1_400)  # ~43 BPM, and every gap stays under the limit
    assert tracker.bpm < 60
    assert tracker.steady is False


def test_tempo_at_the_bounds_counts_as_steady() -> None:
    tracker = RhythmTracker(bpm_min=60, bpm_max=240)
    drum(tracker, 8, 1_000)
    assert tracker.bpm == pytest.approx(60.0)
    assert tracker.steady is True


def test_window_only_weighs_the_most_recent_intervals() -> None:
    tracker = RhythmTracker(window=4)
    last = drum(tracker, 5, 1_000)
    assert tracker.bpm == pytest.approx(60.0)
    last = drum(tracker, 4, 500, start=last + 500)
    assert tracker.bpm == pytest.approx(120.0)
    assert tracker.steady is True


def test_disabled_tracker_still_reports_bpm_but_never_multiplies() -> None:
    tracker = RhythmTracker(enabled=False)
    drum(tracker, 8, 500)
    assert tracker.steady is True
    assert tracker.bpm == pytest.approx(120.0)
    assert tracker.multiplier == 1.0


def test_custom_steady_multiplier() -> None:
    tracker = RhythmTracker(steady_multiplier=1.5)
    drum(tracker, 8, 500)
    assert tracker.multiplier == 1.5


def test_tolerance_admits_small_jitter() -> None:
    tracker = RhythmTracker(tolerance=0.15)
    ts = 0
    for index in range(8):
        ts += 520 if index % 2 else 480  # ±4 % around 500 ms
        tracker.record(ts)
    assert tracker.steady is True


def test_reset_clears_intervals_and_the_last_timestamp() -> None:
    tracker = RhythmTracker()
    drum(tracker, 8, 500)
    tracker.reset()
    assert tracker.bpm == 0.0
    assert tracker.steady is False
    assert tracker.multiplier == 1.0
    tracker.record(100_000)  # no interval against the pre-reset tap
    assert tracker.bpm == 0.0
