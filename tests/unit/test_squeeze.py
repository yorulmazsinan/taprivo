from __future__ import annotations

import json
from pathlib import Path

from taprivo.config import SqueezeConfig
from taprivo.core.events import Finger, Hand, TapSource
from taprivo.vision.squeeze import Levels, SqueezeDetector, SqueezeParams
from tests.vision_helpers import hands_frame, replay_csv, run_squeeze, squeeze_cycle, steady

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
