from __future__ import annotations

import json
from pathlib import Path

from taprivo.config import DetectorConfig
from taprivo.core.events import Finger, TapSource
from taprivo.vision.detector import DetectorParams, TapDetector
from tests.vision_helpers import flat, hand_frame, pulse, replay_csv, run

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "landmarks"


def make() -> TapDetector:
    return TapDetector(DetectorParams(), lambda: "sess-1")


def warm(start_ms: int = 0) -> list:
    """600 ms of flat frames: seeds baselines and passes the reacquire guard."""
    return flat(start_ms, 600)


def test_params_from_config_match_defaults() -> None:
    assert DetectorParams.from_config(DetectorConfig()) == DetectorParams()


def test_single_pulse_yields_one_camera_event() -> None:
    events = run(make(), warm() + pulse(600, Finger.INDEX, 0.4))
    assert len(events) == 1
    e = events[0]
    assert e.finger is Finger.INDEX
    assert e.source is TapSource.CAMERA
    assert e.session_id == "sess-1"
    assert e.hand_id == "h1"
    assert e.displacement > 0.2
    assert e.velocity > 0
    assert e.confidence == 0.95


def test_pulse_below_threshold_yields_nothing() -> None:
    assert run(make(), warm() + pulse(600, Finger.INDEX, 0.1)) == []


def test_extension_pulse_also_counts() -> None:
    frames = warm() + pulse(600, Finger.RING, -0.4)  # negative amplitude = tip moves away
    assert [e.finger for e in run(make(), frames)] == [Finger.RING]


def test_co_moving_fingers_attribute_to_the_larger() -> None:
    frames = warm()
    for a, b in zip(pulse(600, Finger.INDEX, 0.5), pulse(600, Finger.MIDDLE, 0.3), strict=True):
        c2 = dict(a.features.c2)
        c2[Finger.MIDDLE] = b.features.c2[Finger.MIDDLE]
        frames.append(hand_frame(a.ts_ms, c2))
    events = run(make(), frames)
    assert [e.finger for e in events] == [Finger.INDEX]


def test_whole_hand_jitter_yields_nothing() -> None:
    assert run(make(), flat(0, 5000, jitter=0.06, seed=3)) == []


def test_frame_gap_resets_and_reacquire_is_guarded() -> None:
    detector = make()
    events = run(detector, warm(), tail_ms=0)
    for ts in range(600, 1100, 40):  # 500 ms without a hand
        events += detector.process(None, ts)
    # a pulse immediately after re-acquisition falls inside the 300 ms guard
    events += run(detector, pulse(1100, Finger.INDEX, 0.5), tail_ms=400)
    assert events == []
    # after the guard, taps count again (start after the previous run()'s settling
    # tail, which reaches 1780 ms, so frame timestamps stay non-decreasing)
    later = run(detector, flat(1800, 400) + pulse(2200, Finger.INDEX, 0.5))
    assert [e.finger for e in later] == [Finger.INDEX]


def test_hand_id_change_resets() -> None:
    frames = warm() + pulse(600, Finger.MIDDLE, 0.5, hand_id="h2")
    assert run(make(), frames) == []


def test_cooldown_merges_rapid_repeats_and_keeps_spaced_ones() -> None:
    fast = (
        warm()
        + pulse(600, Finger.PINKY, 0.5, duration_ms=120)
        + pulse(760, Finger.PINKY, 0.5, duration_ms=120)
    )
    assert len(run(make(), fast)) == 1
    spaced = warm() + pulse(600, Finger.PINKY, 0.5) + pulse(1100, Finger.PINKY, 0.5)
    assert len(run(make(), spaced)) == 2


def test_held_finger_beyond_max_cycle_is_not_a_tap() -> None:
    frames = warm()
    frames += pulse(600, Finger.INDEX, 0.5, duration_ms=1600)  # 800 ms down, 800 ms up
    assert run(make(), frames) == []


def test_thresholds_are_per_finger() -> None:
    detector = make()
    detector.set_thresholds({Finger.INDEX: 0.5})
    assert detector.thresholds()[Finger.INDEX] == 0.5
    assert detector.thresholds()[Finger.RING] == 0.22
    assert run(detector, warm() + pulse(600, Finger.INDEX, 0.4)) == []


def test_state_exposes_deviation_and_phase() -> None:
    detector = make()
    run(detector, warm(), tail_ms=0)
    state = detector.state()
    assert state.hand_id == "h1"
    assert state.guarded is False
    assert set(state.fingers) == set(Finger)
    assert state.fingers[Finger.INDEX].phase == "idle"


def test_reset_clears_cooldown() -> None:
    # With default params (reacquire_guard_ms=300 > cooldown_ms=140), any tap after a
    # reset()-forced re-acquisition necessarily lands well outside the old cooldown
    # window regardless of whether the stale per-finger timestamp was cleared, so it
    # cannot distinguish fixed from buggy behaviour. Use an elevated cooldown_ms (all
    # other params default) so a stale `_last_event_ts` would still be able to
    # suppress the next legitimate tap if reset() failed to clear it.
    params = DetectorParams(cooldown_ms=5000)
    detector = TapDetector(params, lambda: "sess-1")
    first = warm() + pulse(600, Finger.INDEX, 0.4)
    events = run(detector, first)
    assert [e.finger for e in events] == [Finger.INDEX]

    detector.reset()

    # Keep frame timestamps increasing across the two run() calls (see vision_helpers.run).
    last = first[-1].ts_ms
    tail = list(range(last + 40, last + 400, 40))
    t0 = tail[-1] + 40

    second = run(detector, flat(t0, 400) + pulse(t0 + 400, Finger.INDEX, 0.5))
    assert [e.finger for e in second] == [Finger.INDEX]


def test_recorded_replay_is_deterministic_and_pinned() -> None:
    path = FIXTURES / "spike-2026-09-08.csv"
    first = replay_csv(path, DetectorParams())
    second = replay_csv(path, DetectorParams())

    def summarize(events: list) -> list[dict[str, object]]:
        return [{"ts_ms": e.timestamp_monotonic_ms, "finger": e.finger.value} for e in events]

    as_list = summarize(first)
    assert as_list == summarize(second)
    pinned = json.loads((FIXTURES / "spike-2026-09-08.events.json").read_text())
    assert as_list == pinned
    assert 10 <= len(as_list) <= 60  # sanity: the recording contains dozens of taps, not hundreds
