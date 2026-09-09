from __future__ import annotations

import logging
import threading

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from taprivo.config import ComboConfig, ComboTier, Config, EnergyConfig, RhythmConfig
from taprivo.core.energy import EnergyEngine, SpendError, SpendRequest
from taprivo.core.events import Finger, Hand, TapEvent, TapSource


def tap(
    engine: EnergyEngine,
    ts: int = 0,
    finger: Finger = Finger.INDEX,
    hand: Hand = Hand.RIGHT,
) -> None:
    engine.apply_tap(
        TapEvent(
            event_id=f"e{ts}-{hand}-{finger}",
            session_id=engine.session_id,
            hand=hand,
            hand_id=f"kbd-{hand.value}",
            finger=finger,
            timestamp_monotonic_ms=ts,
            displacement=0.04,
            velocity=0.6,
            confidence=1.0,
            source=TapSource.SIMULATOR,
        )
    )


def spend(engine: EnergyEngine, amount: int, request_id: str = "r1", **kw: object):
    return engine.spend(
        SpendRequest(
            amount=amount,
            reason=str(kw.get("reason", "implement change")),
            request_id=request_id,
            session_id=str(kw.get("session_id", engine.session_id)),
        )
    )


def make_engine(max_energy: int = 10000) -> EnergyEngine:
    """An engine with the combo multiplier off: a tap credits 10 unless the
    tap timestamps also happen to spell out a steady beat."""
    cfg = Config(
        energy=EnergyConfig(energy_per_tap=10, max_energy=max_energy),
        combo=ComboConfig(energy_multiplier_enabled=False),
    )
    return EnergyEngine(cfg, now_ms=lambda: 0)


def make_multiplier_engine(max_energy: int = 10000) -> EnergyEngine:
    cfg = Config(
        energy=EnergyConfig(energy_per_tap=10, max_energy=max_energy),
        combo=ComboConfig(
            timeout_ms=600,
            energy_multiplier_enabled=True,
            tiers=(ComboTier(at=10, multiplier=1.5), ComboTier(at=25, multiplier=2.0)),
        ),
    )
    return EnergyEngine(cfg, now_ms=lambda: 0)


def test_each_tap_adds_ten() -> None:
    engine = make_engine()
    for i in range(100):
        tap(engine, ts=i * 100)
    snap = engine.snapshot()
    assert snap.available == 1000
    assert snap.gross_generated == 1000
    assert snap.taps_total == 100


def test_cap_and_overflow() -> None:
    engine = make_engine(max_energy=25)
    for i in range(3):
        tap(engine, ts=i)
    snap = engine.snapshot()
    assert snap.available == 25
    assert snap.gross_generated == 30
    assert snap.overflow == 5


def test_tap_from_old_session_is_ignored() -> None:
    engine = make_engine()
    old = engine.session_id
    engine.reset_session()
    result = engine.apply_tap(
        TapEvent(
            "e", old, Hand.RIGHT, "kbd-right", Finger.INDEX, 0, 0.04, 0.6, 1.0, TapSource.SIMULATOR
        )
    )
    assert result.accepted is False
    assert engine.snapshot().available == 0


def test_spend_success_and_remaining() -> None:
    engine = make_engine()
    for i in range(50):
        tap(engine, ts=i)
    result = spend(engine, 250)
    assert result.success is True
    assert result.spent == 250
    assert result.remaining == 250
    assert result.transaction_id
    assert engine.snapshot().spent == 250
    assert engine.snapshot().spend_count == 1


def test_insufficient_returns_available_without_change() -> None:
    engine = make_engine()
    tap(engine)
    result = spend(engine, 100)
    assert result.success is False
    assert result.error is SpendError.INSUFFICIENT_ENERGY
    assert result.available == 10
    assert engine.snapshot().available == 10


def test_invalid_amounts() -> None:
    engine = make_engine()
    for bad in (0, -5, 10001):
        assert spend(engine, bad, request_id=f"r{bad}").error is SpendError.INVALID_AMOUNT


def test_invalid_reason() -> None:
    engine = make_engine()
    assert spend(engine, 10, reason="   ").error is SpendError.INVALID_REASON
    assert spend(engine, 10, request_id="r2", reason="x" * 201).error is SpendError.INVALID_REASON


def test_stale_session_rejected() -> None:
    engine = make_engine()
    assert spend(engine, 10, session_id="nope").error is SpendError.SESSION_CHANGED


def test_idempotent_replay_returns_same_transaction() -> None:
    engine = make_engine()
    for i in range(10):
        tap(engine, ts=i)
    first = spend(engine, 50, request_id="same")
    second = spend(engine, 50, request_id="same")
    assert second == first
    assert engine.snapshot().spent == 50


def test_idempotent_replay_does_not_notify_listeners() -> None:
    engine = make_engine()
    for i in range(10):
        tap(engine, ts=i)
    seen: list[int] = []
    engine.subscribe(lambda s: seen.append(s.spent))
    first = spend(engine, 50, request_id="same")
    count_after_first = len(seen)
    second = spend(engine, 50, request_id="same")
    assert len(seen) == count_after_first
    assert second == first


def test_real_spend_logs_once_and_replay_logs_nothing(caplog: pytest.LogCaptureFixture) -> None:
    engine = make_engine()
    for i in range(10):
        tap(engine, ts=i)
    with caplog.at_level(logging.INFO, logger="taprivo.core.energy"):
        spend(engine, 50, request_id="same", reason="do not leak this reason")
        spend_logs = [r for r in caplog.records if "spend" in r.message]
        assert len(spend_logs) == 1
        assert "50" in spend_logs[0].message
        assert "do not leak this reason" not in spend_logs[0].message

        caplog.clear()
        spend(engine, 50, request_id="same", reason="do not leak this reason")
        assert [r for r in caplog.records if "spend" in r.message] == []


def test_same_request_id_different_payload_conflicts() -> None:
    engine = make_engine()
    for i in range(10):
        tap(engine, ts=i)
    spend(engine, 50, request_id="same")
    conflict = spend(engine, 60, request_id="same")
    assert conflict.error is SpendError.IDEMPOTENCY_CONFLICT
    assert engine.snapshot().spent == 50


def test_failed_attempt_can_be_retried_after_more_taps() -> None:
    engine = make_engine()
    assert spend(engine, 20, request_id="r").error is SpendError.INSUFFICIENT_ENERGY
    tap(engine, ts=0)
    tap(engine, ts=1)
    assert spend(engine, 20, request_id="r").success is True


def test_reset_clears_balance_and_idempotency() -> None:
    engine = make_engine()
    tap(engine)
    spend(engine, 10, request_id="r")
    old_session = engine.session_id
    snap = engine.reset_session()
    assert snap.session_id != old_session
    assert snap.available == 0
    tap(engine)
    assert spend(engine, 10, request_id="r").success is True


def test_listeners_receive_snapshots_and_survive_errors() -> None:
    engine = make_engine()
    seen: list[int] = []

    def bad(_: object) -> None:
        raise RuntimeError("boom")

    engine.subscribe(bad)
    engine.subscribe(lambda s: seen.append(s.available))
    tap(engine)
    assert seen == [10]
    engine.unsubscribe(bad)


def test_status_setters() -> None:
    engine = make_engine()
    engine.set_tracking("simulator")
    engine.set_mcp_status("error", "port in use")
    engine.mark_tool_call()
    snap = engine.snapshot()
    assert snap.tracking == "simulator"
    assert snap.mcp == "error"
    assert snap.mcp_error == "port in use"
    assert snap.last_tool_call_utc is not None


def test_concurrent_spends_never_overspend() -> None:
    engine = make_engine()
    for i in range(100):
        tap(engine, ts=i)  # 1000 energy
    results: list[bool] = []
    lock = threading.Lock()
    barrier = threading.Barrier(50)

    def worker(n: int) -> None:
        barrier.wait()
        r = spend(engine, 100, request_id=f"w{n}")
        with lock:
            results.append(r.success)

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(50)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert results.count(True) == 10
    assert engine.snapshot().available == 0


Op = tuple[str, int]


def test_camera_stats_and_no_signal_status() -> None:
    engine = make_engine()
    seen: list[float] = []
    engine.subscribe(lambda s: seen.append(s.camera_fps))
    engine.set_camera_stats(21.5, 0.93)
    engine.set_tracking("no_signal")
    snap = engine.snapshot()
    assert snap.camera_fps == 21.5
    assert snap.detection_ratio == 0.93
    assert snap.tracking == "no_signal"
    assert seen == [21.5, 21.5]


@settings(max_examples=200, deadline=None)
@given(
    st.lists(
        st.one_of(
            st.tuples(st.just("tap"), st.just(0)), st.tuples(st.just("spend"), st.integers(1, 500))
        ),
        max_size=300,
    )
)
def test_available_identity_holds(ops: list[Op]) -> None:
    engine = make_engine(max_energy=800)
    for n, (kind, amount) in enumerate(ops):
        if kind == "tap":
            tap(engine, ts=n)
        else:
            spend(engine, amount, request_id=f"h{n}")
        s = engine.snapshot()
        assert s.available == s.gross_generated - s.overflow - s.spent
        assert 0 <= s.available <= 800


def test_combo_tiers_scale_each_tap() -> None:
    engine = make_multiplier_engine()
    expected = 0
    for count in range(1, 31):
        tap(engine, ts=count * 100)
        expected += 10 if count < 10 else 15 if count < 25 else 20
        snap = engine.snapshot()
        assert snap.available == expected, count
        assert snap.combo == count
    assert engine.snapshot().combo_multiplier == 2.0


def test_combo_timeout_drops_back_to_the_base_rate() -> None:
    engine = make_multiplier_engine()
    for count in range(1, 13):
        tap(engine, ts=count * 100)
    assert engine.snapshot().combo_multiplier == 1.5
    before = engine.snapshot().available
    tap(engine, ts=100_000)
    snap = engine.snapshot()
    assert snap.combo == 1
    assert snap.combo_multiplier == 1.0
    assert snap.available == before + 10


def test_multiplier_disabled_keeps_every_tap_at_ten() -> None:
    engine = make_engine()
    for count in range(1, 31):
        tap(engine, ts=count * 100)
    snap = engine.snapshot()
    assert snap.available == 300
    assert snap.combo == 30
    assert snap.combo_multiplier == 1.0


def test_multiplied_tap_overflows_against_the_cap() -> None:
    engine = make_multiplier_engine(max_energy=145)
    for count in range(1, 12):
        tap(engine, ts=count * 100)
    snap = engine.snapshot()
    # Nine taps at 10 plus two at 15: still below the cap, nothing lost.
    assert snap.available == 120 and snap.overflow == 0
    for count in range(12, 15):
        tap(engine, ts=count * 100)
    snap = engine.snapshot()
    assert snap.available == 145
    assert snap.gross_generated == 165
    assert snap.overflow == 20


def test_per_hand_counters_track_both_hands() -> None:
    engine = make_engine()
    tap(engine, ts=1, hand=Hand.LEFT, finger=Finger.PINKY)
    tap(engine, ts=2, hand=Hand.LEFT, finger=Finger.PINKY)
    tap(engine, ts=3, hand=Hand.RIGHT, finger=Finger.INDEX)
    snap = engine.snapshot()
    assert snap.taps_per_hand == {Hand.LEFT: 2, Hand.RIGHT: 1}
    assert snap.taps_per_hand_finger[(Hand.LEFT, Finger.PINKY)] == 2
    assert snap.taps_per_hand_finger[(Hand.RIGHT, Finger.INDEX)] == 1
    assert snap.taps_per_hand_finger[(Hand.RIGHT, Finger.PINKY)] == 0
    assert snap.taps_per_finger[Finger.PINKY] == 2


def test_reset_clears_per_hand_counters() -> None:
    engine = make_engine()
    tap(engine, ts=1, hand=Hand.LEFT)
    engine.reset_session()
    snap = engine.snapshot()
    assert snap.taps_per_hand == {Hand.LEFT: 0, Hand.RIGHT: 0}
    assert snap.combo_multiplier == 1.0


def test_steady_beat_stacks_on_top_of_the_combo() -> None:
    engine = make_multiplier_engine()
    # 500 ms apart: inside the 600 ms combo window and a clean 120 BPM.
    expected = 0
    for count in range(1, 11):
        tap(engine, ts=count * 500)
        steady = count >= 5  # four intervals are needed before the beat counts
        combo = 1.5 if count >= 10 else 1.0
        expected += round(10 * combo * (1.25 if steady else 1.0))
        snap = engine.snapshot()
        assert snap.rhythm_steady is steady, count
        assert snap.available == expected, count
    snap = engine.snapshot()
    assert snap.bpm == 120.0
    assert snap.combo_multiplier == 1.5
    assert snap.rhythm_multiplier == 1.25
    # The tenth tap alone is worth 10 x 1.5 x 1.25.
    assert round(10 * snap.combo_multiplier * snap.rhythm_multiplier) == 19


def test_snapshot_reports_no_rhythm_before_any_tap() -> None:
    snap = make_engine().snapshot()
    assert snap.bpm == 0.0
    assert snap.rhythm_steady is False
    assert snap.rhythm_multiplier == 1.0


def test_uneven_taps_earn_no_rhythm_bonus() -> None:
    engine = make_engine()
    ts = 0
    for index in range(12):
        ts += 550 if index % 2 else 350
        tap(engine, ts=ts)
    snap = engine.snapshot()
    assert snap.bpm > 0
    assert snap.rhythm_steady is False
    assert snap.rhythm_multiplier == 1.0
    assert snap.available == 120


def test_disabled_rhythm_reports_the_beat_without_paying_for_it() -> None:
    cfg = Config(
        energy=EnergyConfig(energy_per_tap=10),
        combo=ComboConfig(energy_multiplier_enabled=False),
        rhythm=RhythmConfig(enabled=False),
    )
    engine = EnergyEngine(cfg, now_ms=lambda: 0)
    for count in range(1, 11):
        tap(engine, ts=count * 500)
    snap = engine.snapshot()
    assert snap.bpm == 120.0
    assert snap.rhythm_steady is True
    assert snap.rhythm_multiplier == 1.0
    assert snap.available == 100


def test_a_pause_breaks_the_beat_and_the_bonus() -> None:
    engine = make_engine()
    for count in range(1, 9):
        tap(engine, ts=count * 500)
    assert engine.snapshot().rhythm_multiplier == 1.25
    tap(engine, ts=60_000)
    snap = engine.snapshot()
    assert snap.bpm == 0.0
    assert snap.rhythm_steady is False
    assert snap.rhythm_multiplier == 1.0


def test_reset_session_clears_the_rhythm() -> None:
    engine = make_engine()
    for count in range(1, 9):
        tap(engine, ts=count * 500)
    assert engine.snapshot().rhythm_steady is True
    snap = engine.reset_session()
    assert snap.bpm == 0.0
    assert snap.rhythm_steady is False
    assert snap.rhythm_multiplier == 1.0
