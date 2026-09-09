"""The engine's statistics side: what it hands a sink, and when."""

from __future__ import annotations

import logging

import pytest

from taprivo.config import ComboConfig, ComboTier, Config, EnergyConfig
from taprivo.core.energy import EnergyEngine
from taprivo.core.events import Finger, Hand, TapEvent, TapSource
from taprivo.core.stats_sink import DailyRow, SessionRow


class RecordingSink:
    """Remembers every row the engine sends, tagged with the call that sent it."""

    def __init__(self, today: DailyRow | None = None) -> None:
        self.calls: list[tuple[str, SessionRow]] = []
        self._today = today

    def session_started(self, row: SessionRow) -> None:
        self.calls.append(("started", row))

    def session_snapshot(self, row: SessionRow) -> None:
        self.calls.append(("snapshot", row))

    def session_ended(self, row: SessionRow) -> None:
        self.calls.append(("ended", row))

    def today(self, day: str) -> DailyRow | None:
        return self._today

    def kinds(self) -> list[str]:
        return [kind for kind, _ in self.calls]

    def last(self, kind: str) -> SessionRow:
        rows = [row for name, row in self.calls if name == kind]
        assert rows, f"no {kind} row recorded"
        return rows[-1]


class Clock:
    def __init__(self, step_ms: int) -> None:
        self._step = step_ms
        self.now = 0

    def __call__(self) -> int:
        return self.now

    def advance(self, taps: int = 1) -> None:
        self.now += self._step * taps


def make_engine(sink: RecordingSink | None, clock: Clock) -> EnergyEngine:
    config = Config(
        energy=EnergyConfig(energy_per_tap=10),
        combo=ComboConfig(
            timeout_ms=600,
            energy_multiplier_enabled=False,
            tiers=(ComboTier(at=10, multiplier=1.5),),
        ),
    )
    return EnergyEngine(config, sink=sink, now_ms=clock)


def tap(
    engine: EnergyEngine,
    clock: Clock,
    *,
    hand: Hand = Hand.RIGHT,
    source: TapSource = TapSource.SIMULATOR,
) -> None:
    clock.advance()
    engine.apply_tap(
        TapEvent(
            event_id=f"e{clock.now}",
            session_id=engine.session_id,
            hand=hand,
            hand_id=f"kbd-{hand.value}",
            finger=Finger.INDEX,
            timestamp_monotonic_ms=clock.now,
            displacement=0.04,
            velocity=0.6,
            confidence=1.0,
            source=source,
        )
    )


def test_a_started_row_is_written_when_the_engine_starts() -> None:
    sink = RecordingSink()
    clock = Clock(100)
    engine = make_engine(sink, clock)
    assert sink.kinds() == ["started"]
    row = sink.last("started")
    assert row.session_id == engine.session_id
    assert row.ended_utc is None
    assert row.mode == "simulator"
    assert (row.taps_total, row.generated, row.spent, row.max_combo) == (0, 0, 0, 0)
    assert row.started_utc.startswith(engine.snapshot().started_at_utc.date().isoformat())


def test_heartbeat_writes_the_running_totals() -> None:
    sink = RecordingSink()
    clock = Clock(100)
    engine = make_engine(sink, clock)
    tap(engine, clock, hand=Hand.LEFT)
    tap(engine, clock, hand=Hand.RIGHT)
    tap(engine, clock, hand=Hand.RIGHT)
    clock.advance(10)
    engine.heartbeat()

    assert sink.kinds() == ["started", "snapshot"]
    row = sink.last("snapshot")
    assert (row.taps_total, row.taps_left, row.taps_right) == (3, 1, 2)
    assert row.generated == 30
    assert row.duration_s == 1
    assert row.ended_utc is None


def test_max_combo_is_the_highest_streak_of_the_session() -> None:
    sink = RecordingSink()
    clock = Clock(100)
    engine = make_engine(sink, clock)
    for _ in range(4):
        tap(engine, clock)
    clock.advance(20)  # 2 s of silence breaks the combo
    tap(engine, clock)
    engine.heartbeat()
    assert sink.last("snapshot").max_combo == 4


def test_squeezes_are_camera_taps_in_fives() -> None:
    sink = RecordingSink()
    clock = Clock(100)
    engine = make_engine(sink, clock)
    for _ in range(11):
        tap(engine, clock, source=TapSource.CAMERA)
    tap(engine, clock, source=TapSource.SIMULATOR)
    engine.heartbeat()
    row = sink.last("snapshot")
    assert (row.taps_total, row.squeezes) == (12, 2)


def test_reset_ends_the_previous_row_and_starts_a_new_one() -> None:
    sink = RecordingSink()
    clock = Clock(100)
    engine = make_engine(sink, clock)
    first = engine.session_id
    tap(engine, clock)
    engine.reset_session()

    assert sink.kinds() == ["started", "ended", "started"]
    ended = sink.last("ended")
    assert ended.session_id == first
    assert ended.ended_utc is not None
    assert ended.taps_total == 1
    started = sink.last("started")
    assert started.session_id == engine.session_id != first
    assert started.taps_total == 0


def test_close_ends_the_session_once() -> None:
    sink = RecordingSink()
    clock = Clock(100)
    engine = make_engine(sink, clock)
    tap(engine, clock)
    engine.close()
    engine.close()
    assert sink.kinds() == ["started", "ended"]
    assert sink.last("ended").ended_utc is not None


def test_today_comes_from_the_sink() -> None:
    daily = DailyRow(
        day="2026-09-09",
        sessions=2,
        taps_total=30,
        generated=300,
        spent=50,
        active_seconds=600,
    )
    engine = make_engine(RecordingSink(daily), Clock(100))
    assert engine.today() == daily


def test_without_a_sink_nothing_is_recorded_and_today_is_none() -> None:
    clock = Clock(100)
    engine = make_engine(None, clock)
    tap(engine, clock)
    engine.heartbeat()
    engine.reset_session()
    engine.close()
    assert engine.today() is None


def test_a_failing_sink_never_breaks_the_engine(caplog: pytest.LogCaptureFixture) -> None:
    class BrokenSink(RecordingSink):
        def session_snapshot(self, row: SessionRow) -> None:
            raise RuntimeError("disk on fire")

        def today(self, day: str) -> DailyRow | None:
            raise RuntimeError("disk still on fire")

    clock = Clock(100)
    with caplog.at_level(logging.ERROR, logger="taprivo.core.energy"):
        engine = make_engine(BrokenSink(), clock)
        tap(engine, clock)
        engine.heartbeat()
        assert engine.today() is None
    assert engine.snapshot().taps_total == 1
    assert "statistics sink failed" in caplog.text
