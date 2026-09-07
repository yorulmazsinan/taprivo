from __future__ import annotations

import threading

from hypothesis import given, settings
from hypothesis import strategies as st

from taprivo.config import Config, EnergyConfig
from taprivo.core.energy import EnergyEngine, SpendError, SpendRequest
from taprivo.core.events import Finger, Hand, TapEvent, TapSource


def tap(engine: EnergyEngine, ts: int = 0, finger: Finger = Finger.INDEX) -> None:
    engine.apply_tap(
        TapEvent(
            event_id=f"e{ts}",
            session_id=engine.session_id,
            hand=Hand.RIGHT,
            hand_id="sim-right",
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
    cfg = Config(energy=EnergyConfig(energy_per_tap=10, max_energy=max_energy))
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
            "e", old, Hand.RIGHT, "sim-right", Finger.INDEX, 0, 0.04, 0.6, 1.0, TapSource.SIMULATOR
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
