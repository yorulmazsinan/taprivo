from taprivo.config import Config
from taprivo.core.energy import EnergyEngine
from taprivo.core.events import Finger, TapSource
from taprivo.simulator import KEY_TO_FINGER, Simulator


def test_key_mapping() -> None:
    assert KEY_TO_FINGER == {
        "1": Finger.THUMB,
        "2": Finger.INDEX,
        "3": Finger.MIDDLE,
        "4": Finger.RING,
        "5": Finger.PINKY,
    }


def test_tap_ignored_until_started() -> None:
    engine = EnergyEngine(Config())
    sim = Simulator(engine, Config())
    assert sim.tap_key("2") is None
    sim.start()
    assert engine.snapshot().tracking == "simulator"
    result = sim.tap_key("2")
    assert result is not None and result.accepted
    assert engine.snapshot().available == 10
    assert engine.snapshot().taps_per_finger[Finger.INDEX] == 1


def test_unmapped_key_and_stop() -> None:
    engine = EnergyEngine(Config())
    sim = Simulator(engine, Config())
    sim.start()
    assert sim.tap_key("x") is None
    sim.stop()
    assert sim.running is False
    assert engine.snapshot().tracking == "inactive"


def test_event_carries_simulator_source_and_current_session() -> None:
    engine = EnergyEngine(Config())
    sim = Simulator(engine, Config())
    sim.start()
    engine.reset_session()
    result = sim.tap(Finger.PINKY)
    assert result is not None and result.accepted
    seen = engine.snapshot()
    assert seen.session_id == engine.session_id
    assert TapSource.SIMULATOR.value == "simulator"
