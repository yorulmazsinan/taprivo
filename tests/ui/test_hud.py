from __future__ import annotations

import pytest
from PySide6.QtWidgets import QMessageBox
from pytestqt.qtbot import QtBot

from taprivo.config import Config
from taprivo.core.energy import EnergyEngine
from taprivo.ui import hud as hud_module
from tests.ui.conftest import HudBundle


def test_initial_render(hud: HudBundle) -> None:
    assert hud.window.energy_label.text() == "0 / 10000"
    assert "Inactive" in hud.window.tracking_label.text()
    assert "Starting" in hud.window.mcp_label.text()
    assert "resets when Taprivo quits" in hud.window.footer_label.text()
    assert hud.window.toggle_button.text() == "Start Simulator"


def test_key_press_taps_when_simulator_running(hud: HudBundle, qtbot: QtBot) -> None:
    qtbot.keyClick(hud.window, "2")
    assert hud.engine.snapshot().available == 0  # simulator not started yet
    hud.window.toggle_simulator()
    assert hud.window.toggle_button.text() == "Stop Simulator"
    qtbot.keyClick(hud.window, "2")
    qtbot.keyClick(hud.window, "5")
    qtbot.waitUntil(lambda: hud.window.energy_label.text() == "20 / 10000", timeout=2000)
    assert "Index 1" in hud.window.fingers_label.text()
    assert "Pinky 1" in hud.window.fingers_label.text()
    assert hud.window.bar.value() == 20
    assert "Simulator" in hud.window.tracking_label.text()


def test_reset_requires_confirmation(
    hud: HudBundle, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    hud.window.toggle_simulator()
    qtbot.keyClick(hud.window, "3")
    qtbot.waitUntil(lambda: hud.engine.snapshot().available == 10)
    monkeypatch.setattr(
        QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.StandardButton.No)
    )
    hud.window.confirm_reset()
    assert hud.engine.snapshot().available == 10
    monkeypatch.setattr(
        QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes)
    )
    hud.window.confirm_reset()
    qtbot.waitUntil(lambda: hud.window.energy_label.text() == "0 / 10000", timeout=2000)


def test_mcp_error_is_shown(hud: HudBundle, qtbot: QtBot) -> None:
    hud.engine.set_mcp_status("error", "port 32145 is already in use")
    qtbot.waitUntil(lambda: "Error" in hud.window.mcp_label.text(), timeout=2000)
    text = hud.window.mcp_status_text()
    assert "already in use" in text and "taprivo doctor" in text
    hud.engine.set_mcp_status("ready")
    qtbot.waitUntil(lambda: "Ready" in hud.window.mcp_label.text(), timeout=2000)


def test_reduced_motion_renders(reduced_motion_hud: HudBundle, qtbot: QtBot) -> None:
    reduced_motion_hud.window.toggle_simulator()
    qtbot.keyClick(reduced_motion_hud.window, "1")
    qtbot.waitUntil(lambda: reduced_motion_hud.window.bar.value() == 10, timeout=2000)
    assert hud_module.REDUCED_MOTION_STYLE in reduced_motion_hud.window.bar.styleSheet()


def test_no_signal_status_text(hud: HudBundle, qtbot: QtBot) -> None:
    hud.engine.set_tracking("no_signal")
    qtbot.waitUntil(lambda: "No signal" in hud.window.tracking_label.text(), timeout=2000)


def test_auto_repeat_is_ignored(hud: HudBundle) -> None:
    from PySide6.QtCore import QEvent, Qt
    from PySide6.QtGui import QKeyEvent

    hud.window.toggle_simulator()
    event = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_2, Qt.KeyboardModifier.NoModifier, "2", True)
    hud.window.keyPressEvent(event)
    assert hud.engine.snapshot().available == 0


def test_open_camera_button_calls_back(qtbot: QtBot) -> None:
    from taprivo.simulator import Simulator
    from taprivo.ui.hud import HudWindow

    calls: list[int] = []
    engine = EnergyEngine(Config())
    window = HudWindow(
        engine, Simulator(engine, Config()), Config(), on_open_camera=lambda: calls.append(1)
    )
    qtbot.addWidget(window)
    window.open_camera_button.click()
    assert calls == [1]
    plain = HudWindow(engine, Simulator(engine, Config()), Config())
    qtbot.addWidget(plain)
    assert not plain.open_camera_button.isEnabled()
