from __future__ import annotations

import dataclasses
import itertools
import json
from pathlib import Path

from taprivo.config import SqueezeConfig
from taprivo.core.events import Finger, Hand, TapSource
from taprivo.vision.squeeze import Levels, SqueezeDetector, SqueezeParams, hand_in_frame
from tests.vision_helpers import (
    hand_frame,
    hands_frame,
    replay_csv,
    run_squeeze,
    squeeze_cycle,
    steady,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "landmarks"


def make(**kw: object) -> SqueezeDetector:
    return SqueezeDetector(SqueezeParams(**kw), lambda: "sess-1")  # type: ignore[arg-type]


def test_params_match_config_defaults() -> None:
    assert SqueezeParams.from_config(SqueezeConfig()) == SqueezeParams()


def test_bands() -> None:
    close_at, open_at = Levels(0.8, 0.4).bands(0.25)
    assert abs(close_at - 0.5) < 1e-9 and abs(open_at - 0.7) < 1e-9


def test_one_cycle_emits_five_finger_events() -> None:
    events = run_squeeze(make(), steady(0, 600) + squeeze_cycle(600))
    assert len(events) == 5
    assert sorted(e.finger for e in events) == sorted(Finger)
    assert {e.timestamp_monotonic_ms for e in events} == {events[0].timestamp_monotonic_ms}
    e = events[0]
    assert e.source is TapSource.CAMERA and e.hand is Hand.RIGHT and e.hand_id == "right-1"
    assert 0.3 < e.displacement < 0.7 and e.velocity > 0 and e.confidence == 0.95


def test_shallow_dip_is_not_a_cycle() -> None:
    assert run_squeeze(make(), steady(0, 600) + squeeze_cycle(600, closed_value=0.65)) == []


def test_long_fist_is_not_a_cycle() -> None:
    seq = steady(0, 600) + steady(600, 3000, value=0.3) + steady(3600, 400)
    assert run_squeeze(make(), seq) == []


def test_cooldown_merges_rapid_cycles() -> None:
    # 200 ms round trips leave the EMA below close_at for only ~80 ms (under
    # min_closed_ms), so nothing would register at all; 240 ms is fast enough to
    # keep the second squeeze inside the 300 ms cooldown of the first while still
    # crossing min_closed_ms on its own.
    seq = steady(0, 600) + squeeze_cycle(600, duration_ms=240) + squeeze_cycle(880, duration_ms=240)
    assert len(run_squeeze(make(), seq)) == 5
    spaced = steady(0, 600) + squeeze_cycle(600) + steady(1440, 400) + squeeze_cycle(1840)
    assert len(run_squeeze(make(), spaced)) == 10


def test_two_hands_count_independently() -> None:
    seq = []
    for ts in range(0, 600, 40):
        seq.append(hands_frame(ts, {Hand.LEFT: 0.9, Hand.RIGHT: 0.9}))
    right = squeeze_cycle(600, Hand.RIGHT)
    left = squeeze_cycle(600, Hand.LEFT)
    for right_hands, left_hands in zip(right, left, strict=True):
        seq.append(right_hands + left_hands)
    events = run_squeeze(make(), seq)
    assert len(events) == 10
    assert {e.hand for e in events} == {Hand.LEFT, Hand.RIGHT}


def test_duplicate_handedness_in_one_frame_keeps_higher_score() -> None:
    # MediaPipe can label two detections "Right" in the same frame (mirrored
    # or partly-turned hands); the collision must resolve deterministically to
    # the higher-score detection, not to whichever happens to come last.
    detector = make()
    low = hand_frame(0, {f: 0.3 for f in Finger}, hand_id="low", score=0.4, hand=Hand.RIGHT)
    high = hand_frame(0, {f: 0.9 for f in Finger}, hand_id="high", score=0.95, hand=Hand.RIGHT)
    detector.process((high, low), 0)  # higher-score frame listed first
    state = detector.state().hands[Hand.RIGHT]
    assert abs(state.ema - 0.9) < 1e-9

    detector2 = make()
    detector2.process((low, high), 0)  # higher-score frame listed last
    state2 = detector2.state().hands[Hand.RIGHT]
    assert abs(state2.ema - 0.9) < 1e-9


def test_hand_loss_mid_cycle_and_reacquisition_emit_nothing() -> None:
    detector = make()
    events = run_squeeze(detector, steady(0, 600), tail_ms=0)
    half = squeeze_cycle(600)[
        : len(squeeze_cycle(600)) // 2
    ]  # down to the fist, then the hand vanishes
    events += run_squeeze(detector, half, tail_ms=0)
    for ts in range(1100, 1500, 40):
        events += detector.process((), ts)
    events += run_squeeze(detector, steady(1500, 600))  # re-acquired open hand: no event
    assert events == []


def test_unknown_start_needs_an_open_hand_first() -> None:
    # hand first seen closed, then opens: not a cycle
    assert run_squeeze(make(), steady(0, 400, value=0.3) + steady(400, 600, value=0.9)) == []


def test_set_levels_changes_bands() -> None:
    detector = make()
    detector.set_levels(Levels(0.95, 0.1))
    assert detector.levels() == Levels(0.95, 0.1)
    # with closed_level 0.1, close_at = 0.3125: a dip to 0.6 never drops below it, so no cycle
    assert (
        run_squeeze(
            detector,
            steady(0, 600, value=0.95) + squeeze_cycle(600, open_value=0.95, closed_value=0.6),
        )
        == []
    )


def test_state_exposes_phase_and_seen() -> None:
    detector = make()
    run_squeeze(detector, steady(0, 400), tail_ms=0)
    state = detector.state()
    assert state.hands[Hand.RIGHT].phase == "open" and state.hands[Hand.RIGHT].seen
    assert state.hands[Hand.LEFT].seen is False


def test_hand_in_frame_accepts_landmarks_within_bounds() -> None:
    assert hand_in_frame(hand_frame(0, {})) is True


def test_hand_in_frame_rejects_a_landmark_outside_bounds() -> None:
    frame = hand_frame(0, {})
    landmarks = list(frame.landmarks)
    landmarks[8] = (landmarks[8][0], 1.05, landmarks[8][2])  # index fingertip below frame
    frame = dataclasses.replace(frame, landmarks=tuple(landmarks))
    assert hand_in_frame(frame) is False


def test_edge_artefact_landmarks_suppress_the_cycle() -> None:
    # A normal open->closed->open cycle registers one cycle (five finger events).
    seq = steady(0, 600) + squeeze_cycle(600)
    assert len(run_squeeze(make(), seq)) == 5

    # Shift every "open" frame of the cycle (openness above the open band) so one
    # landmark reports outside the frame, mimicking a hand sliding past the bottom
    # edge as it appears to reopen. Those frames are then treated as hand-not-visible,
    # so the reopen is never observed and no cycle is registered.
    _, open_at = Levels(SqueezeParams().open_level, SqueezeParams().closed_level).bands(
        SqueezeParams().band_ratio
    )
    edged_cycle = []
    for hands in squeeze_cycle(600):
        if hands and hands[0].features.openness > open_at:
            hand = hands[0]
            landmarks = list(hand.landmarks)
            landmarks[0] = (landmarks[0][0], 1.4, landmarks[0][2])
            hand = dataclasses.replace(hand, landmarks=tuple(landmarks))
            hands = (hand,)
        edged_cycle.append(hands)
    assert run_squeeze(make(), steady(0, 600) + edged_cycle) == []


def test_recorded_replay_is_deterministic_and_pinned() -> None:
    path = FIXTURES / "spike-2026-09-08.csv"
    first = replay_csv(path, SqueezeParams())
    second = replay_csv(path, SqueezeParams())
    as_list = [{"ts_ms": e.timestamp_monotonic_ms, "finger": e.finger.value} for e in first]
    assert as_list == [
        {"ts_ms": e.timestamp_monotonic_ms, "finger": e.finger.value} for e in second
    ]
    assert as_list == json.loads((FIXTURES / "spike-2026-09-08.events.json").read_text())
    assert len(as_list) % 5 == 0


def test_field_recording_counts_both_hands() -> None:
    path = FIXTURES / "squeeze-2026-09-09.csv"
    first = replay_csv(path, SqueezeParams())
    second = replay_csv(path, SqueezeParams())
    as_list = [
        {"ts_ms": e.timestamp_monotonic_ms, "hand": e.hand.value, "finger": e.finger.value}
        for e in first
    ]
    assert as_list == [
        {"ts_ms": e.timestamp_monotonic_ms, "hand": e.hand.value, "finger": e.finger.value}
        for e in second
    ]
    assert as_list == json.loads((FIXTURES / "squeeze-2026-09-09.events.json").read_text())

    right_cycles = [e for e in first if e.hand is Hand.RIGHT and e.finger is Finger.INDEX]
    left_cycles = [e for e in first if e.hand is Hand.LEFT and e.finger is Finger.INDEX]
    assert len(right_cycles) == 27
    assert len(left_cycles) == 20
    assert len(first) == 47 * 5

    for cycles in (right_cycles, left_cycles):
        timestamps = sorted(e.timestamp_monotonic_ms for e in cycles)
        gaps = [b - a for a, b in itertools.pairwise(timestamps)]
        assert all(gap >= 500 for gap in gaps)


def test_typing_recording_counts_few_cycles() -> None:
    path = FIXTURES / "typing-2026-09-09.csv"
    first = replay_csv(path, SqueezeParams())
    second = replay_csv(path, SqueezeParams())
    as_list = [
        {"ts_ms": e.timestamp_monotonic_ms, "hand": e.hand.value, "finger": e.finger.value}
        for e in first
    ]
    assert as_list == [
        {"ts_ms": e.timestamp_monotonic_ms, "hand": e.hand.value, "finger": e.finger.value}
        for e in second
    ]
    assert as_list == json.loads((FIXTURES / "typing-2026-09-09.events.json").read_text())

    right_cycles = [e for e in first if e.hand is Hand.RIGHT and e.finger is Finger.INDEX]
    left_cycles = [e for e in first if e.hand is Hand.LEFT and e.finger is Finger.INDEX]
    assert len(right_cycles) == 3
    assert len(left_cycles) == 0
