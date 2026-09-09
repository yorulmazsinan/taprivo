from __future__ import annotations

from dataclasses import replace

import pytest
from PySide6.QtWidgets import QMessageBox
from pytestqt.qtbot import QtBot

from taprivo.config import Config
from taprivo.core.energy import EnergyEngine
from taprivo.core.events import Finger, Hand
from taprivo.simulator import KEY_MAP, Simulator
from taprivo.ui.hud import HudWindow
from taprivo.ui.theme import DARK
from tests.ui.conftest import HudBundle


def test_initial_render(hud: HudBundle) -> None:
    assert hud.window.energy_label.text() == "0"
    assert hud.window.energy_cap_label.text() == "/ 10000"
    assert "Inactive" in hud.window.tracking_label.text()
    assert "Starting" in hud.window.mcp_label.text()
    footer = hud.window.footer_label.text()
    assert "Keys 1-4 left hand, 7-8-9-0 right hand." in footer
    assert "resets when Taprivo quits" in footer
    assert hud.window.toggle_button.text() == "Start Keyboard"


def test_key_press_taps_when_keyboard_running(hud: HudBundle, qtbot: QtBot) -> None:
    qtbot.keyClick(hud.window, "2")
    assert hud.engine.snapshot().available == 0  # keyboard mode not started yet
    hud.window.toggle_simulator()
    assert hud.window.toggle_button.text() == "Stop Keyboard"
    qtbot.keyClick(hud.window, "2")
    qtbot.keyClick(hud.window, "0")
    qtbot.waitUntil(lambda: hud.window.energy_label.text() == "20", timeout=2000)
    assert "Ring 1" in hud.window.fingers_label.text()
    assert "Pinky 1" in hud.window.fingers_label.text()
    assert hud.window.bar.value() == 20
    assert "Keyboard running" in hud.window.tracking_label.text()


def test_chips_cover_both_hands_and_show_their_keys(hud: HudBundle, qtbot: QtBot) -> None:
    """The chips are hidden behind the hand map but still carry the per-key counts."""
    assert len(hud.window.chips) == 8
    assert not hud.window.chips[(Hand.LEFT, Finger.INDEX)].isVisible()
    for key, (hand, finger) in KEY_MAP.items():
        chip = hud.window.chips[(hand, finger)]
        assert f"{key} {finger.value.title()}" in chip.text(), key
    hud.window.toggle_simulator()
    qtbot.keyClick(hud.window, "7")
    qtbot.keyClick(hud.window, "7")
    right_index = hud.window.chips[(Hand.RIGHT, Finger.INDEX)]
    qtbot.waitUntil(lambda: "7 Index 2" in right_index.text(), timeout=2000)
    assert "4 Index 0" in hud.window.chips[(Hand.LEFT, Finger.INDEX)].text()
    assert (Hand.LEFT, Finger.THUMB) not in hud.window.chips


def test_combo_line_shows_the_multiplier(hud: HudBundle, qtbot: QtBot) -> None:
    assert hud.window.combo_label.text() == "COMBO x0"
    hud.window.toggle_simulator()
    for _ in range(10):
        qtbot.keyClick(hud.window, "3")
    qtbot.waitUntil(lambda: "1.5×" in hud.window.combo_label.text(), timeout=2000)
    assert hud.window.combo_label.text() == "COMBO x10 · 1.5×"


def test_combo_line_hides_a_flat_multiplier(hud: HudBundle, qtbot: QtBot) -> None:
    hud.window.toggle_simulator()
    qtbot.keyClick(hud.window, "3")
    qtbot.waitUntil(lambda: hud.window.combo_label.text() == "COMBO x1", timeout=2000)


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
    qtbot.waitUntil(lambda: hud.window.energy_label.text() == "0", timeout=2000)


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
    assert reduced_motion_hud.window.bar.isReducedMotion() is True


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


def test_unmapped_keys_do_nothing(hud: HudBundle, qtbot: QtBot) -> None:
    hud.window.toggle_simulator()
    for key in ("5", "6"):
        qtbot.keyClick(hud.window, key)
    assert hud.engine.snapshot().taps_total == 0


def test_snapshot_renders_expected_size_and_colors(hud: HudBundle) -> None:
    image = hud.window.grab().toImage()
    assert image.width() == 480
    colors: set[int] = set()
    for y in range(image.height()):
        for x in range(image.width()):
            colors.add(image.pixel(x, y))
            if len(colors) > 2:
                break
        if len(colors) > 2:
            break
    assert len(colors) > 2


def _themed_hud(qtbot: QtBot) -> tuple[HudWindow, EnergyEngine]:
    """A HUD pinned to the dark palette, so the accent colour is known here."""
    cfg = Config()
    engine = EnergyEngine(cfg)
    window = HudWindow(engine, Simulator(engine, cfg), cfg, palette=DARK)
    qtbot.addWidget(window)
    return window, engine


def _render(window: HudWindow, engine: EnergyEngine, qtbot: QtBot, **fields: object) -> None:
    window.on_snapshot(replace(engine.snapshot(), **fields))
    qtbot.waitUntil(lambda: window.rate_label.text() != "", timeout=2000)


def test_rate_line_without_a_beat_shows_taps_only(qtbot: QtBot) -> None:
    window, engine = _themed_hud(qtbot)
    _render(window, engine, qtbot, taps_per_minute=132, bpm=0.0, rhythm_steady=False)
    qtbot.waitUntil(lambda: window.rate_label.text() == "132 taps/min", timeout=2000)
    assert DARK.text_dim in window.rate_label.styleSheet()


def test_rate_line_shows_an_unsteady_bpm_dimmed(qtbot: QtBot) -> None:
    window, engine = _themed_hud(qtbot)
    _render(window, engine, qtbot, taps_per_minute=132, bpm=96.0, rhythm_steady=False)
    qtbot.waitUntil(lambda: window.rate_label.text() == "132 taps/min · 96 BPM", timeout=2000)
    assert DARK.text_dim in window.rate_label.styleSheet()
    assert DARK.accent not in window.rate_label.styleSheet()


def test_rate_line_accents_a_steady_bpm(qtbot: QtBot) -> None:
    window, engine = _themed_hud(qtbot)
    _render(window, engine, qtbot, taps_per_minute=132, bpm=96.0, rhythm_steady=True)
    qtbot.waitUntil(
        lambda: window.rate_label.text() == "132 taps/min · 96 BPM steady", timeout=2000
    )
    assert DARK.accent in window.rate_label.styleSheet()


def test_rate_line_drops_the_accent_when_the_beat_breaks(qtbot: QtBot) -> None:
    window, engine = _themed_hud(qtbot)
    _render(window, engine, qtbot, taps_per_minute=132, bpm=96.0, rhythm_steady=True)
    qtbot.waitUntil(lambda: DARK.accent in window.rate_label.styleSheet(), timeout=2000)
    _render(window, engine, qtbot, taps_per_minute=40, bpm=0.0, rhythm_steady=False)
    qtbot.waitUntil(lambda: window.rate_label.text() == "40 taps/min", timeout=2000)
    assert DARK.text_dim in window.rate_label.styleSheet()


def test_setup_action_calls_back(qtbot: QtBot) -> None:
    """`Setup…` lives in the overflow menu now, so the attribute is a QAction."""
    calls: list[int] = []
    engine = EnergyEngine(Config())
    window = HudWindow(
        engine, Simulator(engine, Config()), Config(), on_open_setup=lambda: calls.append(1)
    )
    qtbot.addWidget(window)
    assert window.setup_button.text() == "Setup…"
    window.setup_button.trigger()
    assert calls == [1]
    plain = HudWindow(engine, Simulator(engine, Config()), Config())
    qtbot.addWidget(plain)
    assert not plain.setup_button.isEnabled()


def test_toolbar_buttons_fit_the_window(hud: HudBundle) -> None:
    """Two primary buttons and the overflow: every label has to fit its button."""
    assert hud.window.grab().width() == 480
    buttons = (
        hud.window.toggle_button,
        hud.window.open_camera_button,
        hud.window.more_button,
    )
    for button in buttons:
        assert button.width() >= button.sizeHint().width(), button.text()


def test_overflow_menu_holds_the_secondary_actions(hud: HudBundle) -> None:
    menu = hud.window.more_button.menu()
    assert menu is not None
    assert [action.text() for action in menu.actions()] == [
        "Reset Session",
        "MCP Status",
        "Setup…",
    ]
    assert menu.actions() == [
        hud.window.reset_button,
        hud.window.mcp_button,
        hud.window.setup_button,
    ]


def test_menu_actions_run_the_same_slots(hud: HudBundle, monkeypatch: pytest.MonkeyPatch) -> None:
    asked: list[str] = []
    monkeypatch.setattr(
        QMessageBox,
        "question",
        staticmethod(lambda *a, **k: asked.append("reset") or QMessageBox.StandardButton.No),
    )
    monkeypatch.setattr(
        QMessageBox, "information", staticmethod(lambda *a, **k: asked.append("mcp"))
    )
    hud.window.reset_button.trigger()
    hud.window.mcp_button.trigger()
    assert asked == ["reset", "mcp"]
