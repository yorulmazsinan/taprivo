from collections.abc import Callable, Iterator
from dataclasses import dataclass

import pytest
from pytestqt.qtbot import QtBot

from taprivo.config import Config, HudConfig
from taprivo.core.energy import EnergyEngine
from taprivo.simulator import Simulator
from taprivo.ui.bridge import EngineSignals, connect_engine
from taprivo.ui.hud import HudWindow
from taprivo.ui.theme import Palette


@dataclass
class HudBundle:
    window: HudWindow
    engine: EnergyEngine
    simulator: Simulator
    config: Config


def build_hud(
    qtbot: QtBot,
    config: Config | None = None,
    clock: Callable[[], float] | None = None,
    palette: Palette | None = None,
) -> HudBundle:
    cfg = config or Config()
    engine = EnergyEngine(cfg)
    simulator = Simulator(engine, cfg)
    extra: dict[str, object] = {}
    if clock is not None:
        extra["clock"] = clock
    if palette is not None:
        extra["palette"] = palette
    window = HudWindow(engine, simulator, cfg, **extra)  # type: ignore[arg-type]
    qtbot.addWidget(window)
    signals = EngineSignals(parent=window)
    signals.snapshot_changed.connect(window.on_snapshot)
    connect_engine(engine, signals)
    window.on_snapshot(engine.snapshot())
    window.show()
    # The HUD coalesces renders through a single-shot timer; wait for the first paint
    # so tests observe a populated window regardless of platform event-loop timing.
    # The status badges are empty until that render, unlike the energy number.
    qtbot.waitUntil(lambda: window.tracking_label.text() != "", timeout=2000)
    return HudBundle(window, engine, simulator, cfg)


@pytest.fixture
def hud(qtbot: QtBot) -> Iterator[HudBundle]:
    yield build_hud(qtbot)


@pytest.fixture
def reduced_motion_hud(qtbot: QtBot) -> Iterator[HudBundle]:
    yield build_hud(qtbot, Config(hud=HudConfig(reduced_motion=True, always_on_top=False)))
