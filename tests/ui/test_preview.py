from __future__ import annotations

import pytest
from PySide6.QtGui import QColor, QImage
from pytestqt.qtbot import QtBot

from taprivo.core.events import Hand
from taprivo.ui.preview import (
    PULSE_MS,
    REDUCED_PULSE_MS,
    Badge,
    HandGlyph,
    HandOverlay,
    PreviewView,
    StepPips,
    hand_color,
)
from taprivo.ui.theme import DARK

W, H = 320, 240

# A splayed hand: wrist, four knuckles and the joints of every finger, all well
# inside the frame so nothing is clipped by the rounded preview mask.
HAND: tuple[tuple[float, float, float], ...] = (
    (0.50, 0.85, 0.0),  # 0 wrist
    (0.38, 0.78, 0.0),  # 1-4 thumb
    (0.31, 0.70, 0.0),
    (0.26, 0.62, 0.0),
    (0.22, 0.55, 0.0),
    (0.40, 0.58, 0.0),  # 5-8 index
    (0.37, 0.46, 0.0),
    (0.36, 0.38, 0.0),
    (0.35, 0.31, 0.0),
    (0.49, 0.55, 0.0),  # 9-12 middle
    (0.49, 0.42, 0.0),
    (0.49, 0.33, 0.0),
    (0.49, 0.26, 0.0),
    (0.57, 0.57, 0.0),  # 13-16 ring
    (0.59, 0.44, 0.0),
    (0.60, 0.36, 0.0),
    (0.61, 0.29, 0.0),
    (0.64, 0.62, 0.0),  # 17-20 pinky
    (0.68, 0.52, 0.0),
    (0.70, 0.45, 0.0),
    (0.72, 0.39, 0.0),
)


def solid_image(color: str = "#303030") -> QImage:
    image = QImage(W, H, QImage.Format.Format_RGB888)
    image.fill(QColor(color))
    return image


def gradient_image() -> QImage:
    image = QImage(W, H, QImage.Format.Format_RGB888)
    for y in range(H):
        for x in range(W):
            image.setPixelColor(x, y, QColor(30 + x // 4, 40 + y // 6, 90))
    return image


def overlay(hand: Hand = Hand.RIGHT, state: str = "Open") -> HandOverlay:
    return HandOverlay(hand, HAND, 0.9, state)


@pytest.fixture
def view(qtbot: QtBot) -> PreviewView:
    widget = PreviewView(palette=DARK)
    widget.setFixedSize(W, H)
    qtbot.addWidget(widget)
    widget.show()
    return widget


def test_placeholder_text_when_there_is_no_frame(view: PreviewView) -> None:
    view.setText("Camera off")
    assert view.text() == "Camera off"
    assert not view.has_frame()
    assert view.grab().toImage().width() == W  # paints without a frame


def test_overlay_renders_non_uniform_pixels_for_a_fake_hand(view: PreviewView) -> None:
    view.set_frame(solid_image())
    bare = view.grab().toImage()
    view.set_hands([overlay()])
    drawn = view.grab().toImage()
    assert view.hands()[0].hand is Hand.RIGHT
    assert drawn != bare  # the skeleton changed pixels
    colors = {drawn.pixel(x, y) for y in range(0, H, 3) for x in range(0, W, 3)}
    assert len(colors) > 3  # dots and bones, not one flat fill


def test_each_hand_gets_its_own_colour(view: PreviewView) -> None:
    assert hand_color(DARK, Hand.LEFT) == DARK.accent
    assert hand_color(DARK, Hand.RIGHT) == DARK.ok
    view.set_frame(solid_image())
    view.set_hands([overlay(Hand.LEFT)])
    left = view.grab().toImage()
    view.set_hands([overlay(Hand.RIGHT)])
    assert view.grab().toImage() != left


def test_pulse_appears_and_expires(view: PreviewView, qtbot: QtBot) -> None:
    view.set_frame(solid_image())
    view.set_hands([overlay()])
    quiet = view.grab().toImage()
    view.pulse(Hand.RIGHT)
    assert Hand.RIGHT in view.active_pulses()
    assert view.grab().toImage() != quiet
    qtbot.waitUntil(lambda: view.active_pulses() == (), timeout=PULSE_MS * 10)
    qtbot.wait(20)
    assert view.active_pulses() == ()


def test_reduced_motion_pulse_is_short_and_still_drawn(qtbot: QtBot) -> None:
    widget = PreviewView(palette=DARK, reduced_motion=True)
    widget.setFixedSize(W, H)
    qtbot.addWidget(widget)
    widget.show()
    widget.set_frame(solid_image())
    widget.set_hands([overlay()])
    quiet = widget.grab().toImage()
    assert widget.is_reduced_motion()
    widget.pulse(Hand.RIGHT)
    assert Hand.RIGHT in widget.active_pulses()
    assert widget.grab().toImage() != quiet
    qtbot.waitUntil(lambda: widget.active_pulses() == (), timeout=REDUCED_PULSE_MS * 20)


def test_reduced_motion_can_be_toggled(view: PreviewView) -> None:
    assert not view.is_reduced_motion()
    view.set_reduced_motion(True)
    assert view.is_reduced_motion()


def test_pulse_without_a_hand_falls_back_to_the_centre(view: PreviewView) -> None:
    view.set_frame(solid_image())
    view.pulse(Hand.LEFT)  # no landmarks known for that hand
    assert Hand.LEFT in view.active_pulses()
    view.grab()  # must not raise


def test_chrome_strip_and_live_badge(view: PreviewView) -> None:
    view.set_frame(gradient_image())
    plain = view.grab().toImage()
    view.set_chrome(29.0, 0.87, "FaceTime HD Camera")
    assert view.chrome_text() == "29 fps · hand 87 % · FaceTime HD Camera"
    assert view.grab().toImage() != plain
    assert not view.is_live()
    with_strip = view.grab().toImage()
    view.set_live(True)
    assert view.is_live()
    assert view.grab().toImage() != with_strip
    view.clear_chrome()
    assert view.chrome_text() == ""


def test_frame_cleared_drops_the_hands(view: PreviewView) -> None:
    view.set_frame(solid_image())
    view.set_hands([overlay()])
    view.set_frame(None)
    assert not view.has_frame()
    assert view.hands() == ()


def test_step_pips_fill_in_order(qtbot: QtBot) -> None:
    pips = StepPips(palette=DARK)
    qtbot.addWidget(pips)
    pips.resize(200, 26)
    pips.show()
    assert pips.labels() == ("Visibility", "Open", "Fist", "Squeeze")
    assert pips.filled() == 0
    for index, step in enumerate(("visibility", "open", "fist", "squeeze")):
        pips.setStep(step)
        assert pips.filled() == index + 1
    pips.setStep("done")
    assert pips.filled() == 4
    pips.setStep("nonsense")
    assert pips.filled() == 0
    pips.setStep("squeeze")
    assert pips.grab().toImage().width() > 0


def test_hand_glyph_names_its_side(qtbot: QtBot) -> None:
    for hand in Hand:
        glyph = HandGlyph(hand, palette=DARK)
        qtbot.addWidget(glyph)
        glyph.show()
        assert glyph.hand() is hand
        assert glyph.accessibleName() == f"{hand.value.title()} hand"
        assert glyph.grab().toImage().width() > 0


def test_badge_keeps_its_text_and_hides_when_empty(qtbot: QtBot) -> None:
    badge = Badge(palette=DARK)
    qtbot.addWidget(badge)
    badge.setBadge("uncalibrated", DARK.warn)
    assert badge.text() == "uncalibrated"
    assert badge.accessibleName() == "uncalibrated"
    assert DARK.warn.lower() in badge.styleSheet().lower()
    badge.setBadge("")
    assert badge.text() == ""
    assert not badge.isVisible()
