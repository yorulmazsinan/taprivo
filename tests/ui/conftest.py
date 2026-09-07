from collections.abc import Iterator
from dataclasses import dataclass

import pytest
from pytestqt.qtbot import QtBot

from taprivo.config import Config, HudConfig
from taprivo.core.energy import EnergyEngine
from taprivo.simulator import Simulator
from taprivo.ui.bridge import EngineSignals, connect_engine
from taprivo.ui.hud import HudWindow


@dataclass
class HudBundle:
    window: HudWindow
    engine: EnergyEngine
    simulator: Simulator
    config: Config


def build_hud(qtbot: QtBot, config: Config | None = None) -> HudBundle:
    cfg = config or Config()
    engine = EnergyEngine(cfg)
    simulator = Simulator(engine, cfg)
    window = HudWindow(engine, simulator, cfg)
    qtbot.addWidget(window)
    signals = EngineSignals(parent=window)
    signals.snapshot_changed.connect(window.on_snapshot)
    connect_engine(engine, signals)
    window.on_snapshot(engine.snapshot())
    window.show()
    return HudBundle(window, engine, simulator, cfg)


@pytest.fixture
def hud(qtbot: QtBot) -> Iterator[HudBundle]:
    yield build_hud(qtbot)


@pytest.fixture
def reduced_motion_hud(qtbot: QtBot) -> Iterator[HudBundle]:
    yield build_hud(qtbot, Config(hud=HudConfig(reduced_motion=True, always_on_top=False)))
