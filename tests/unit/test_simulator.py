from __future__ import annotations

from taprivo.config import Config, SimulatorConfig
from taprivo.core.energy import EnergyEngine, TapResult
from taprivo.core.events import Finger, Hand, TapEvent, TapSource
from taprivo.simulator import HAND_IDS, KEY_MAP, Simulator


class FakeClock:
    def __init__(self) -> None:
        self.now = 0

    def __call__(self) -> int:
        return self.now


def test_key_mapping_covers_both_hands() -> None:
    assert KEY_MAP == {
        "1": (Hand.LEFT, Finger.PINKY),
        "2": (Hand.LEFT, Finger.RING),
        "3": (Hand.LEFT, Finger.MIDDLE),
        "4": (Hand.LEFT, Finger.INDEX),
        "7": (Hand.RIGHT, Finger.INDEX),
        "8": (Hand.RIGHT, Finger.MIDDLE),
        "9": (Hand.RIGHT, Finger.RING),
        "0": (Hand.RIGHT, Finger.PINKY),
    }
    assert HAND_IDS == {Hand.LEFT: "kbd-left", Hand.RIGHT: "kbd-right"}


def test_each_key_taps_its_hand_and_finger() -> None:
    clock = FakeClock()
    engine = EnergyEngine(Config())
    sim = Simulator(engine, Config(), now_ms=clock)
    sim.start()
    for key, (hand, finger) in KEY_MAP.items():
        clock.now += 1000
        result = sim.tap_key(key)
        assert result is not None and result.accepted, key
        snapshot = result.snapshot
        assert snapshot.taps_per_hand_finger[(hand, finger)] == 1, key
    assert engine.snapshot().taps_per_hand == {Hand.LEFT: 4, Hand.RIGHT: 4}


class RecordingEngine(EnergyEngine):
    """An engine that keeps the raw events it was handed."""

    def __init__(self, config: Config) -> None:
        super().__init__(config)
        self.events: list[TapEvent] = []

    def apply_tap(self, event: TapEvent) -> TapResult:
        self.events.append(event)
        return super().apply_tap(event)


def test_events_carry_the_keyboard_hand_id_and_source() -> None:
    engine = RecordingEngine(Config())
    sim = Simulator(engine, Config())
    sim.start()
    sim.tap_key("1")
    sim.tap_key("0")
    assert [event.hand_id for event in engine.events] == ["kbd-left", "kbd-right"]
    assert [event.hand for event in engine.events] == [Hand.LEFT, Hand.RIGHT]
    assert {event.source for event in engine.events} == {TapSource.SIMULATOR}


def test_tap_ignored_until_started() -> None:
    engine = EnergyEngine(Config())
    sim = Simulator(engine, Config())
    assert sim.tap_key("2") is None
    sim.start()
    assert engine.snapshot().tracking == "simulator"
    result = sim.tap_key("2")
    assert result is not None and result.accepted
    assert engine.snapshot().available == 10
    assert engine.snapshot().taps_per_finger[Finger.RING] == 1


def test_unmapped_keys_and_stop() -> None:
    engine = EnergyEngine(Config())
    sim = Simulator(engine, Config())
    sim.start()
    for key in ("5", "6", "a", "", "!"):
        assert sim.tap_key(key) is None, key
    assert engine.snapshot().taps_total == 0
    sim.stop()
    assert sim.running is False
    assert engine.snapshot().tracking == "inactive"


def test_event_carries_simulator_source_and_current_session() -> None:
    engine = EnergyEngine(Config())
    sim = Simulator(engine, Config())
    sim.start()
    engine.reset_session()
    result = sim.tap(Hand.RIGHT, Finger.PINKY)
    assert result is not None and result.accepted
    seen = engine.snapshot()
    assert seen.session_id == engine.session_id
    assert TapSource.SIMULATOR.value == "simulator"


def test_cap_ignores_taps_beyond_the_window() -> None:
    clock = FakeClock()
    engine = EnergyEngine(Config())
    sim = Simulator(engine, Config(), now_ms=clock)
    sim.start()
    for index in range(12):
        clock.now = index * 10
        assert sim.tap_key("1") is not None, index
    clock.now = 500
    assert sim.tap_key("1") is None
    assert engine.snapshot().taps_total == 12
    clock.now = 1001
    assert sim.tap_key("1") is not None
    assert engine.snapshot().taps_total == 13


def test_cap_counts_both_hands_together() -> None:
    clock = FakeClock()
    engine = EnergyEngine(Config())
    cfg = Config(simulator=SimulatorConfig(max_taps_per_second=4))
    sim = Simulator(engine, cfg, now_ms=clock)
    sim.start()
    assert sim.tap_key("1") is not None
    assert sim.tap_key("0") is not None
    assert sim.tap_key("2") is not None
    assert sim.tap_key("9") is not None
    assert sim.tap_key("3") is None
    assert engine.snapshot().taps_per_hand == {Hand.LEFT: 2, Hand.RIGHT: 2}


def test_stopping_clears_the_cap_window() -> None:
    clock = FakeClock()
    engine = EnergyEngine(Config())
    cfg = Config(simulator=SimulatorConfig(max_taps_per_second=2))
    sim = Simulator(engine, cfg, now_ms=clock)
    sim.start()
    assert sim.tap_key("1") is not None
    assert sim.tap_key("1") is not None
    assert sim.tap_key("1") is None
    sim.stop()
    sim.start()
    assert sim.tap_key("1") is not None
