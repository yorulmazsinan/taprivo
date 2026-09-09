from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QImage
from pytestqt.qtbot import QtBot

from taprivo.core.events import Finger, Hand
from taprivo.ui.theme import DARK
from taprivo.ui.widgets import Badge, Card, Chip, EnergyBar, HandMap, Meter, StatusDot


def _has_multiple_colors(image: QImage) -> bool:
    colors: set[int] = set()
    for y in range(image.height()):
        for x in range(image.width()):
            colors.add(image.pixel(x, y))
            if len(colors) > 1:
                return True
    return False


def test_energy_bar_range_and_value_clamp(qtbot: QtBot) -> None:
    bar = EnergyBar()
    qtbot.addWidget(bar)
    bar.setRange(0, 10)
    bar.setValue(15)
    assert bar.value() == 10
    bar.setValue(-5)
    assert bar.value() == 0
    bar.setValue(4)
    assert bar.value() == 4


def test_energy_bar_renders_non_uniform(qtbot: QtBot) -> None:
    bar = EnergyBar()
    qtbot.addWidget(bar)
    bar.resize(200, 14)
    bar.setRange(0, 100)
    bar.setValue(60)
    bar.show()
    image = bar.grab().toImage()
    assert image.size().width() == 200
    assert _has_multiple_colors(image)


def test_meter_value_clamps_and_levels_stored(qtbot: QtBot) -> None:
    meter = Meter()
    qtbot.addWidget(meter)
    meter.setValue(1.5)
    assert meter.value() == 1.0
    meter.setValue(-0.5)
    assert meter.value() == 0.0
    meter.setLevels(0.8, 0.45)
    assert meter.levels() == (0.8, 0.45)


def test_meter_renders_non_uniform(qtbot: QtBot) -> None:
    meter = Meter()
    qtbot.addWidget(meter)
    meter.resize(180, 22)
    meter.setValue(0.7)
    meter.setLevels(0.8, 0.45)
    meter.setState("Open")
    meter.show()
    image = meter.grab().toImage()
    assert image.size().height() == 22
    assert _has_multiple_colors(image)


def test_status_dot_sets_status_and_text(qtbot: QtBot) -> None:
    dot = StatusDot()
    qtbot.addWidget(dot)
    dot.setStatus("ok", "Ready")
    assert dot.text() == "Ready"


def test_status_dot_renders_non_uniform(qtbot: QtBot) -> None:
    dot = StatusDot()
    qtbot.addWidget(dot)
    dot.setStatus("err", "No signal")
    dot.resize(120, 18)
    dot.show()
    image = dot.grab().toImage()
    assert _has_multiple_colors(image)


def test_chip_set_chip_contains_label_and_value(qtbot: QtBot) -> None:
    chip = Chip()
    qtbot.addWidget(chip)
    chip.setChip("#F97316", "Thumb", 3)
    assert "Thumb" in chip.text()
    assert "3" in chip.text()


def test_chip_renders_non_uniform(qtbot: QtBot) -> None:
    chip = Chip()
    qtbot.addWidget(chip)
    chip.setChip("#F97316", "Thumb", 3)
    chip.adjustSize()
    chip.show()
    image = chip.grab().toImage()
    assert _has_multiple_colors(image)


def _column_colors(image: QImage, x: int) -> set[int]:
    return {image.pixel(x, y) for y in range(image.height())}


def test_energy_bar_fill_is_a_gradient(qtbot: QtBot) -> None:
    """The fill is lighter on the right than on the left."""
    bar = EnergyBar()
    qtbot.addWidget(bar)
    bar.resize(200, 14)
    bar.setRange(0, 100)
    bar.setValue(100)
    bar.show()
    image = bar.grab().toImage()
    left = QColor(image.pixel(20, 7))
    right = QColor(image.pixel(180, 7))
    assert right.lightness() > left.lightness()


def test_energy_bar_draws_quarter_ticks(qtbot: QtBot) -> None:
    """A tick column differs from the plain fill column next to it."""
    bar = EnergyBar()
    qtbot.addWidget(bar)
    bar.resize(200, 14)
    bar.setRange(0, 100)
    bar.setValue(100)
    bar.show()
    image = bar.grab().toImage()
    for fraction in EnergyBar.TICKS:
        tick_x = round(200 * fraction)
        tick = _column_colors(image, tick_x)
        plain = _column_colors(image, tick_x + 6)
        assert tick != plain, fraction


def test_hand_map_keycaps_cover_every_drummed_key(qtbot: QtBot) -> None:
    hand_map = HandMap()
    qtbot.addWidget(hand_map)
    assert sorted(hand_map.keycaps.values()) == ["0", "1", "2", "3", "4", "7", "8", "9"]
    assert hand_map.keycaps[(Hand.LEFT, Finger.PINKY)] == "1"
    assert hand_map.keycaps[(Hand.RIGHT, Finger.PINKY)] == "0"
    assert (Hand.LEFT, Finger.THUMB) not in hand_map.keycaps


def test_hand_map_counts_round_trip(qtbot: QtBot) -> None:
    hand_map = HandMap()
    qtbot.addWidget(hand_map)
    assert hand_map.counts() == {}
    counts = {(Hand.LEFT, Finger.INDEX): 4, (Hand.RIGHT, Finger.RING): 9}
    hand_map.setCounts(counts)
    assert hand_map.counts() == counts
    hand_map.counts()[(Hand.LEFT, Finger.INDEX)] = 99
    assert hand_map.counts()[(Hand.LEFT, Finger.INDEX)] == 4


def test_hand_map_flash_decays_back_to_dark(qtbot: QtBot) -> None:
    hand_map = HandMap()
    qtbot.addWidget(hand_map)
    hand_map.resize(400, 96)
    hand_map.show()
    hand_map.flash(Hand.LEFT, Finger.MIDDLE)
    assert hand_map.flashLevel(Hand.LEFT, Finger.MIDDLE) == 1.0
    qtbot.waitUntil(
        lambda: hand_map.flashLevel(Hand.LEFT, Finger.MIDDLE) == 0.0,
        timeout=2000,
    )


def test_hand_map_reduced_motion_blinks_off(qtbot: QtBot) -> None:
    """No fade: the tip is fully lit, then flat off."""
    hand_map = HandMap()
    qtbot.addWidget(hand_map)
    hand_map.setReducedMotion(True)
    assert hand_map.isReducedMotion() is True
    hand_map.flash(Hand.RIGHT, Finger.INDEX)
    assert hand_map.flashLevel(Hand.RIGHT, Finger.INDEX) == 1.0
    qtbot.waitUntil(
        lambda: hand_map.flashLevel(Hand.RIGHT, Finger.INDEX) == 0.0,
        timeout=2000,
    )


def test_hand_map_renders_non_uniform(qtbot: QtBot) -> None:
    hand_map = HandMap()
    qtbot.addWidget(hand_map)
    hand_map.resize(400, 96)
    hand_map.setCounts({(Hand.LEFT, Finger.INDEX): 3})
    hand_map.show()
    image = hand_map.grab().toImage()
    assert image.size().height() == 96
    assert _has_multiple_colors(image)


def test_hand_map_lights_a_tapped_finger(qtbot: QtBot) -> None:
    """A flashed finger changes what the widget paints."""
    hand_map = HandMap()
    qtbot.addWidget(hand_map)
    hand_map.resize(400, 96)
    hand_map.show()
    before = hand_map.grab().toImage()
    hand_map.flash(Hand.RIGHT, Finger.MIDDLE)
    after = hand_map.grab().toImage()
    assert before != after


def test_badge_keeps_the_full_text_and_shows_the_short_one(qtbot: QtBot) -> None:
    badge = Badge()
    qtbot.addWidget(badge)
    badge.setStatus("ok", "⌨", "Keyboard", "Tracking: Keyboard running")
    assert badge.text() == "Tracking: Keyboard running"
    assert badge.shortText() == "Keyboard"
    assert badge.kind() == "ok"
    assert badge.toolTip() == "Tracking: Keyboard running"


def test_badge_falls_back_to_the_short_text(qtbot: QtBot) -> None:
    badge = Badge()
    qtbot.addWidget(badge)
    badge.setStatus("err", "◎", "No signal")
    assert badge.text() == "No signal"


def test_badge_emits_clicked_only_when_clickable(qtbot: QtBot) -> None:
    badge = Badge()
    qtbot.addWidget(badge)
    badge.setStatus("ok", "⇄", "MCP ready")
    badge.resize(120, 22)
    badge.show()
    clicks: list[int] = []
    badge.clicked.connect(lambda: clicks.append(1))
    qtbot.mouseClick(badge, Qt.MouseButton.LeftButton)
    assert clicks == []
    badge.setClickable(True)
    qtbot.mouseClick(badge, Qt.MouseButton.LeftButton)
    assert clicks == [1]


def test_badge_renders_non_uniform(qtbot: QtBot) -> None:
    badge = Badge()
    qtbot.addWidget(badge)
    badge.setStatus("warn", "◎", "Camera stale")
    badge.resize(badge.sizeHint())
    badge.show()
    assert _has_multiple_colors(badge.grab().toImage())


def test_card_carries_a_shadow_and_the_surface_colour(qtbot: QtBot) -> None:
    card = Card(palette=DARK)
    qtbot.addWidget(card)
    assert DARK.surface in card.styleSheet()
    assert DARK.border in card.styleSheet()
    assert card.graphicsEffect() is not None
